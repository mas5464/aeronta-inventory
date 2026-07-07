package trax.io.writeback.domain;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import jakarta.persistence.NoResultException;
import jakarta.transaction.Transactional;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.SQLIntegrityConstraintViolationException;
import java.time.Instant;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import trax.io.writeback.persistence.LocationMaster;
import trax.io.writeback.persistence.PnInventoryLevel;
import trax.io.writeback.persistence.PnInventoryLevelAudit;
import trax.io.writeback.persistence.PnInventoryLevelAuditPK;
import trax.io.writeback.persistence.PnInventoryLevelPK;
import trax.io.writeback.persistence.PnMaster;
import trax.io.writeback.persistence.TraxRepository;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Domain core of the write-back pipeline: validates a {@link WritebackCommand} against eMRO
 * reference data, applies {@link NumericPolicy}, upserts {@code PN_INVENTORY_LEVEL} (+ audit
 * row), and records an idempotency ledger entry — all in a single {@code REQUIRES_NEW}
 * transaction per item. See the Task 6 brief for the full 9-point pipeline contract.
 *
 * <p>This class is the single place that guarantees the {@link ItemResult} status/code
 * invariant (ACCEPTED/SHADOWED/SKIPPED_DUPLICATE → 200, REJECTED_* → 400, ERROR → 500) —
 * every result is built through {@link #result} / {@link #codeFor}. {@code ItemResult.rowId}
 * always echoes the request's {@code Provenance.rowId} so facades can correlate per-item
 * results back to batch rows.
 */
@ApplicationScoped
public class StockLevelWriter {

    static final String AGENT_VERSION = "emro-writeback-java/1.0";

    private static final TypeReference<LinkedHashMap<String, Integer>> VALUES_JSON_TYPE =
            new TypeReference<>() {};

    @Inject TraxRepository repo;

    @Inject EntityManager em;

    @Inject ObjectMapper objectMapper;

    /**
     * Non-transactional wrapper around {@link #writeItem(WritebackCommand)}. Facades/consumers
     * call this method (never {@code writeItem} directly): it is the seam that resolves a
     * concurrent duplicate idempotency-key insert (which forces the inner transaction to roll
     * back) into a clean {@code SKIPPED_DUPLICATE} result, and turns any other failure into an
     * {@code ERROR} result rather than letting the exception propagate.
     *
     * <p>The direct {@code writeItem(cmd)} self-call below IS intercepted: Quarkus ArC applies
     * {@code @Transactional} via a generated bean subclass (not a wrapping client proxy à la
     * Spring), so virtual dispatch reaches the transactional override even on self-invocation.
     * Verified empirically by this class's test suite — writes commit and are visible from
     * separate transactions.
     */
    public ItemResult writeItemDedup(WritebackCommand cmd) {
        try {
            return writeItem(cmd);
        } catch (Exception e) {
            // The REQUIRES_NEW transaction has already been rolled back by the transactional
            // interceptor by the time this exception reaches us. A concurrent duplicate may
            // arrive as a PersistenceException, a jakarta.transaction.RollbackException, or an
            // Arjuna-specific runtime exception depending on how deep the ORA-00001 surfaces —
            // inspect the full cause chain rather than the exception's declared type.
            if (isDuplicateKeyViolation(e)) {
                Optional<WritebackLedger> winner =
                        findByIdempotencyKey(cmd.provenance().idempotencyKey());
                if (winner.isPresent()) {
                    return skippedDuplicateFrom(cmd, winner.get());
                }
            }
            return result(
                    ResultStatus.ERROR, e.getMessage(), cmd.provenance().rowId(), null, null, null, null);
        }
    }

    @Transactional(Transactional.TxType.REQUIRES_NEW)
    public ItemResult writeItem(WritebackCommand cmd) {
        // 1. Validate.
        ItemResult rejection = validate(cmd);
        if (rejection != null) {
            return rejection;
        }

        // 2. Duplicate check.
        String idempotencyKey = cmd.provenance().idempotencyKey();
        Optional<WritebackLedger> existing = findByIdempotencyKey(idempotencyKey);
        if (existing.isPresent()) {
            return skippedDuplicateFrom(cmd, existing.get());
        }

        PnMaster pnMaster = repo.findActivePn(cmd.pn()).orElseThrow();
        boolean consumable = repo.isConsumable(pnMaster.getCategory());

        PnInventoryLevelPK pk = new PnInventoryLevelPK();
        pk.setPn(cmd.pn());
        pk.setLocation(cmd.location());

        // 3. Load existing row (nullable) -> capture oldValues.
        PnInventoryLevel existingLevel = repo.findLevel(pk);
        Map<String, Integer> oldValues = existingLevel == null ? null : levelToMap(existingLevel);

        // 4. Apply NumericPolicy per field; null command field leaves column untouched.
        LevelValues lv = cmd.levels();
        BigDecimal rop = NumericPolicy.apply(lv.reorderLevel(), consumable);
        BigDecimal eoq = NumericPolicy.apply(lv.eoqLevel(), consumable);
        BigDecimal stockMin = NumericPolicy.apply(lv.stockMin(), consumable);
        BigDecimal stockMax = NumericPolicy.apply(lv.stockMax(), consumable);
        BigDecimal orderMin = NumericPolicy.apply(lv.orderMin(), consumable);
        BigDecimal orderMax = NumericPolicy.apply(lv.orderMax(), consumable);
        BigDecimal leadTime = NumericPolicy.apply(lv.replenishmentLeadTime(), consumable);

        Instant now = Instant.now();
        Date nowDate = Date.from(now);

        // Compute the "would-be" resulting values without mutating any managed entity yet — in
        // shadow mode we must NOT touch the managed existingLevel, since Hibernate would flush
        // any field mutation as dirty state at transaction commit regardless of whether we ever
        // call persistOrMerge. rop/eoq/stockMin/stockMax fall back to the existing row's values
        // (or null on a new row) when the command field is null, mirroring the "column untouched"
        // rule for the values map/ledger too.
        Map<String, Integer> newValues = new LinkedHashMap<>();
        newValues.put(
                "rop",
                toIntOrNull(rop != null ? rop : existingLevel == null ? null : existingLevel.getReorderLevel()));
        newValues.put(
                "eoq",
                toIntOrNull(eoq != null ? eoq : existingLevel == null ? null : existingLevel.getEoqLevel()));
        newValues.put(
                "safety_stock",
                toIntOrNull(
                        stockMin != null ? stockMin : existingLevel == null ? null : existingLevel.getMinimumStock()));
        newValues.put(
                "max_stock",
                toIntOrNull(
                        stockMax != null ? stockMax : existingLevel == null ? null : existingLevel.getMaximumStock()));

        boolean shadow = cmd.shadow();
        if (!shadow) {
            // 6. Upsert entity, then insert audit row mirroring it.
            PnInventoryLevel level = existingLevel != null ? existingLevel : new PnInventoryLevel();
            if (existingLevel == null) {
                level.setId(pk);
                level.setCreatedBy(cmd.provenance().principal());
                level.setCreatedDate(nowDate);
                // COMPANY is set only on first insert; an update must not overwrite the
                // pre-existing eMRO row's own company code.
                level.setCompany(repo.company());
            }
            level.setModifiedBy(cmd.provenance().principal());
            level.setModifiedDate(nowDate);
            if (rop != null) level.setReorderLevel(rop);
            if (eoq != null) level.setEoqLevel(eoq);
            if (stockMin != null) level.setMinimumStock(stockMin);
            if (stockMax != null) level.setMaximumStock(stockMax);
            if (orderMin != null) level.setMinimumOrder(orderMin);
            if (orderMax != null) level.setMaximumOrder(orderMax);
            if (leadTime != null) level.setReplenishmentLeadTime(leadTime);

            repo.persistOrMerge(level);

            PnInventoryLevelAudit audit = new PnInventoryLevelAudit();
            PnInventoryLevelAuditPK auditPk = new PnInventoryLevelAuditPK();
            auditPk.setPn(cmd.pn());
            auditPk.setLocation(cmd.location());
            auditPk.setCreatedBy(cmd.provenance().principal());
            auditPk.setCreatedDate(nowDate);
            // Every audit PK component must be non-null (Oracle rejects the row otherwise); an
            // existing row's COMPANY may legitimately be null, so fall back to the default.
            auditPk.setCompany(level.getCompany() != null ? level.getCompany() : repo.company());
            audit.setId(auditPk);
            audit.setReorderLevel(level.getReorderLevel());
            audit.setEoqLevel(level.getEoqLevel());
            audit.setMinimumStock(level.getMinimumStock());
            audit.setMaximumStock(level.getMaximumStock());
            audit.setMinimumOrder(level.getMinimumOrder());
            audit.setMaximumOrder(level.getMaximumOrder());
            audit.setModifiedBy(level.getModifiedBy());
            audit.setModifiedDate(level.getModifiedDate());
            repo.persistOrMerge(audit);
        }

        // 7. Ledger insert (same tx): version chaining.
        Long previousMaxVersion = maxVersion(cmd.provenance().tenantId(), cmd.pn(), cmd.location());
        long version = previousMaxVersion == null ? 1L : previousMaxVersion + 1L;

        WritebackLedger ledger = new WritebackLedger();
        ledger.setIdempotencyKey(idempotencyKey);
        ledger.setTenantId(cmd.provenance().tenantId());
        ledger.setRunId(cmd.provenance().runId());
        ledger.setRowId(cmd.provenance().rowId());
        ledger.setPn(cmd.pn());
        ledger.setLocation(cmd.location());
        ledger.setSource(cmd.provenance().source());
        ledger.setTier(cmd.provenance().tier());
        ledger.setApprover(cmd.provenance().approver());
        ledger.setPrincipal(cmd.provenance().principal());
        ledger.setAgentVersion(AGENT_VERSION);
        ledger.setOutcome(shadow ? "SHADOWED" : "WRITTEN");
        ledger.setVersion(version);
        ledger.setParentVersion(previousMaxVersion);
        ledger.setOldValuesJson(toJson(oldValues));
        ledger.setNewValuesJson(toJson(newValues));
        ledger.setCreatedAt(now);

        em.persist(ledger);
        em.flush();

        ResultStatus status = shadow ? ResultStatus.SHADOWED : ResultStatus.ACCEPTED;
        return result(status, null, cmd.provenance().rowId(), oldValues, newValues, version, now);
    }

    public Optional<WritebackLedger> findByIdempotencyKey(String key) {
        return em
                .createQuery(
                        "select l from WritebackLedger l where l.idempotencyKey = :key", WritebackLedger.class)
                .setParameter("key", key)
                .getResultStream()
                .findFirst();
    }

    public List<WritebackLedger> history(String tenantId, String pn, String location) {
        return em
                .createQuery(
                        "select l from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.pn = :pn and l.location = :location"
                                + " order by l.version asc",
                        WritebackLedger.class)
                .setParameter("tenantId", tenantId)
                .setParameter("pn", pn)
                .setParameter("location", location)
                .getResultList();
    }

    private Long maxVersion(String tenantId, String pn, String location) {
        return em
                .createQuery(
                        "select max(l.version) from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.pn = :pn and l.location = :location",
                        Long.class)
                .setParameter("tenantId", tenantId)
                .setParameter("pn", pn)
                .setParameter("location", location)
                .getSingleResult();
    }

    private ItemResult validate(WritebackCommand cmd) {
        Long rowId = cmd.provenance().rowId();

        Optional<PnMaster> pnMaster = repo.findActivePn(cmd.pn());
        if (pnMaster.isEmpty()) {
            Optional<String> actualStatus = pnStatusRegardlessOfActivity(cmd.pn());
            if (actualStatus.isPresent()) {
                return result(
                        ResultStatus.REJECTED_VALIDATION,
                        "PN " + cmd.pn() + " is not active, status=" + actualStatus.get(),
                        rowId,
                        null,
                        null,
                        null,
                        null);
            }
            return result(
                    ResultStatus.REJECTED_UNKNOWN_KEY,
                    "unknown PN: " + cmd.pn(),
                    rowId,
                    null,
                    null,
                    null,
                    null);
        }

        Optional<LocationMaster> location = repo.findActiveInventoryLocation(cmd.location());
        if (location.isEmpty()) {
            if (!locationExistsRegardlessOfEligibility(cmd.location())) {
                return result(
                        ResultStatus.REJECTED_UNKNOWN_KEY,
                        "unknown location: " + cmd.location(),
                        rowId,
                        null,
                        null,
                        null,
                        null);
            }
            return result(
                    ResultStatus.REJECTED_VALIDATION,
                    "location is not an active, non-quarantine inventory location: " + cmd.location(),
                    rowId,
                    null,
                    null,
                    null,
                    null);
        }

        LevelValues lv = cmd.levels();
        if (isNegative(lv.reorderLevel())
                || isNegative(lv.eoqLevel())
                || isNegative(lv.stockMin())
                || isNegative(lv.stockMax())
                || isNegative(lv.orderMin())
                || isNegative(lv.orderMax())
                || isNegative(lv.replenishmentLeadTime())) {
            return result(
                    ResultStatus.REJECTED_VALIDATION,
                    "numeric values must be >= 0",
                    rowId,
                    null,
                    null,
                    null,
                    null);
        }

        if (lv.stockMin() != null && lv.stockMax() != null && lv.stockMin().compareTo(lv.stockMax()) > 0) {
            return result(
                    ResultStatus.REJECTED_VALIDATION,
                    "stockMin must be <= stockMax",
                    rowId,
                    null,
                    null,
                    null,
                    null);
        }
        if (lv.orderMin() != null && lv.orderMax() != null && lv.orderMin().compareTo(lv.orderMax()) > 0) {
            return result(
                    ResultStatus.REJECTED_VALIDATION,
                    "orderMin must be <= orderMax",
                    rowId,
                    null,
                    null,
                    null,
                    null);
        }

        return null;
    }

    private Optional<String> pnStatusRegardlessOfActivity(String pn) {
        try {
            return Optional.of(
                    em.createQuery("select p.status from PnMaster p where p.pn = :pn", String.class)
                            .setParameter("pn", pn)
                            .getSingleResult());
        } catch (NoResultException e) {
            return Optional.empty();
        }
    }

    private boolean locationExistsRegardlessOfEligibility(String location) {
        try {
            em.createQuery(
                            "select l.location from LocationMaster l where l.location = :location", String.class)
                    .setParameter("location", location)
                    .getSingleResult();
            return true;
        } catch (NoResultException e) {
            return false;
        }
    }

    private static boolean isNegative(BigDecimal value) {
        return value != null && value.signum() < 0;
    }

    private ItemResult skippedDuplicateFrom(WritebackCommand cmd, WritebackLedger ledger) {
        return result(
                ResultStatus.SKIPPED_DUPLICATE,
                "duplicate idempotency key: " + ledger.getIdempotencyKey(),
                cmd.provenance().rowId(),
                fromJson(ledger.getOldValuesJson()),
                fromJson(ledger.getNewValuesJson()),
                ledger.getVersion(),
                ledger.getCreatedAt());
    }

    /** Single seam enforcing the status → HTTP-ish code invariant for every ItemResult. */
    private static ItemResult result(
            ResultStatus status,
            String message,
            Long rowId,
            Map<String, Integer> oldValues,
            Map<String, Integer> newValues,
            Long ledgerVersion,
            Instant writtenAt) {
        return new ItemResult(status, codeFor(status), message, rowId, oldValues, newValues, ledgerVersion, writtenAt);
    }

    private static int codeFor(ResultStatus status) {
        return switch (status) {
            case ACCEPTED, SHADOWED, SKIPPED_DUPLICATE -> 200;
            case REJECTED_VALIDATION, REJECTED_UNKNOWN_KEY -> 400;
            case ERROR -> 500;
        };
    }

    private static Map<String, Integer> levelToMap(PnInventoryLevel level) {
        Map<String, Integer> map = new LinkedHashMap<>();
        map.put("rop", toIntOrNull(level.getReorderLevel()));
        map.put("eoq", toIntOrNull(level.getEoqLevel()));
        map.put("safety_stock", toIntOrNull(level.getMinimumStock()));
        map.put("max_stock", toIntOrNull(level.getMaximumStock()));
        return map;
    }

    private static Integer toIntOrNull(BigDecimal value) {
        return value == null ? null : value.setScale(0, RoundingMode.HALF_UP).intValueExact();
    }

    private String toJson(Map<String, Integer> values) {
        if (values == null) {
            return null;
        }
        try {
            return objectMapper.writeValueAsString(values);
        } catch (Exception e) {
            throw new IllegalStateException("failed to serialize values map", e);
        }
    }

    private Map<String, Integer> fromJson(String json) {
        if (json == null) {
            return null;
        }
        try {
            return objectMapper.readValue(json, VALUES_JSON_TYPE);
        } catch (Exception e) {
            throw new IllegalStateException("failed to deserialize values map", e);
        }
    }

    private static boolean isDuplicateKeyViolation(Throwable e) {
        Throwable cause = e;
        while (cause != null) {
            if (cause instanceof SQLIntegrityConstraintViolationException) {
                return true;
            }
            String message = cause.getMessage();
            if (message != null
                    && (message.contains("UQ_WRITEBACK_IDEMPOTENCY") || message.contains("ORA-00001"))) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }
}
