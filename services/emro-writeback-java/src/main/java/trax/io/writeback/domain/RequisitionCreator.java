package trax.io.writeback.domain;

import io.quarkus.narayana.jta.QuarkusTransaction;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import jakarta.transaction.Transactional;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Date;
import java.util.Optional;
import trax.io.writeback.persistence.LocationMaster;
import trax.io.writeback.persistence.PnMaster;
import trax.io.writeback.persistence.RequisitionDetail;
import trax.io.writeback.persistence.RequisitionDetailAudit;
import trax.io.writeback.persistence.RequisitionDetailAuditPK;
import trax.io.writeback.persistence.RequisitionDetailPK;
import trax.io.writeback.persistence.RequisitionHeader;
import trax.io.writeback.persistence.RequisitionHeaderAudit;
import trax.io.writeback.persistence.RequisitionHeaderAuditPK;
import trax.io.writeback.persistence.RequisitionNumberSource;
import trax.io.writeback.persistence.TraxRepository;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Domain service that creates an eMRO requisition (header + a single line-1 detail row) for a
 * {@code (PN, LOCATION)}, ported from ARMAC's {@code RequisitionData.CreateRequisition} — see
 * {@code /Users/miguelsosa/trax-mgmt-armac_interfaces/TraxReorderRequisition/src/main/java/trax/aero/data/RequisitionData.java}.
 *
 * <p>Structurally this mirrors {@link StockLevelWriter}'s {@code writeItemDedup}/{@code
 * writeItem} split: {@link #createDedup} is the non-transactional seam that resolves a
 * concurrent duplicate idempotency-key insert into a clean {@code SKIPPED_DUPLICATE}, wrapping
 * the {@code REQUIRES_NEW}-transactional {@link #create}. See that class's Javadoc for the full
 * rationale of the self-invocation/transactional-interception and constraint-classification
 * design — this class reuses it verbatim rather than re-deriving it.
 *
 * <p><b>Version chaining (D10):</b> the ledger row this class writes shares the SAME per-{@code
 * (tenantId, pn, location)} version space as {@link StockLevelWriter} (and the not-yet-built
 * transfer creator) — {@code 1 + max(version)} is computed across ALL domains for the key, not
 * just {@code REQUISITION} rows. A requisition and a stock-level write for the same key
 * interleave into one chronological ledger history.
 *
 * <p><b>Out of scope (documented, matches slice-1's writer):</b>
 *
 * <ul>
 *   <li><b>PnInterchangeable resolution</b> — ARMAC resolves {@code parameter.getPartNo()}
 *       through {@code PnInterchangeable} before looking up {@code PnMaster}. The Trax IO
 *       optimizer sends already-resolved PNs, and {@link StockLevelWriter} does not resolve
 *       interchangeables either; this class stays consistent with that choice.
 *   <li><b>NotePad creation</b> — ARMAC writes free-text {@code remarks} into a {@code NotePad}
 *       row keyed off a config-driven transaction-number sequence ({@code
 *       pkg_application_function.config_number('NOTES')}), then points {@code
 *       RequisitionDetail.notes} at it. That sequence/table has no equivalent in this project's
 *       lifted entity set (no {@code NotePad} entity exists here — see {@code
 *       trax.io.writeback.persistence}), so it is out of scope for this slice. {@code
 *       RequisitionCommand.remarks} is intentionally NOT persisted anywhere in this version;
 *       there is no notes/remarks column on the lifted {@code RequisitionDetail}/{@code
 *       RequisitionHeader} entities to fall back to. A future slice can add the NotePad entity and
 *       wire remarks through it faithfully.
 *   <li><b>REQUIRE_HOUR/REQUIRE_MINUTE</b> — ARMAC parses a caller-supplied {@code yyyy-MM-dd
 *       HH:mm:ss} date string and splits it into {@code REQUIRE_DATE} plus separate {@code
 *       REQUIRE_HOUR}/{@code REQUIRE_MINUTE} columns on the detail row. {@link
 *       RequisitionCommand#needBy()} is a date-only {@code LocalDate} (Trax IO's optimizer has no
 *       time-of-day component to supply), so those two columns are intentionally left unset here.
 *       Unlike ARMAC's {@code parameter.getDate()} (never null, always a full timestamp string),
 *       {@code needBy} is nullable in this slice — when null, {@code REQUIRE_DATE} (and hour/minute)
 *       are simply left unset on the detail row rather than defaulted.
 * </ul>
 */
@ApplicationScoped
public class RequisitionCreator {

    static final String AGENT_VERSION = "emro-writeback-java/1.0";

    /** {@link WritebackLedger#getDomain()} value for every row this creator ledgers. */
    public static final String DOMAIN_REQUISITION = "REQUISITION";

    /** Requisition detail line number this slice always creates (single-line requisitions). */
    private static final int DETAIL_LINE = 1;

    private static final int MAX_VERSION_CONFLICT_ATTEMPTS = 3;
    private static final long VERSION_CONFLICT_BACKOFF_MILLIS = 25L;

    @Inject TraxRepository repo;

    @Inject RequisitionNumberSource numberSource;

    @Inject EntityManager em;

    /**
     * Non-transactional wrapper around {@link #create(RequisitionCommand)} — callers use this,
     * never {@code create} directly. See {@link StockLevelWriter#writeItemDedup} for the full
     * rationale (self-invocation transactional interception, constraint classification, ground
     * truth ledger refetch on a concurrent loser).
     */
    public RequisitionResult createDedup(RequisitionCommand cmd) {
        Exception lastVersionConflict = null;
        for (int attempt = 1; attempt <= MAX_VERSION_CONFLICT_ATTEMPTS; attempt++) {
            try {
                return create(cmd);
            } catch (Exception e) {
                StockLevelWriter.ConstraintViolation violation = StockLevelWriter.classifyConstraintViolation(e);
                if (violation == StockLevelWriter.ConstraintViolation.VERSION_CONFLICT
                        || violation == StockLevelWriter.ConstraintViolation.LEVEL_ROW_RACE) {
                    lastVersionConflict = e;
                    if (attempt < MAX_VERSION_CONFLICT_ATTEMPTS) {
                        backoff();
                        continue;
                    }
                    return errorResult(e.getMessage(), cmd.provenance().rowId());
                }
                if (violation == StockLevelWriter.ConstraintViolation.IDEMPOTENCY_DUPLICATE
                        || isUnexpectedPersistenceFailure(e)) {
                    Optional<WritebackLedger> winner = QuarkusTransaction.requiringNew()
                            .call(() -> findByIdempotencyKey(
                                    cmd.provenance().tenantId(), cmd.provenance().idempotencyKey()));
                    if (winner.isPresent()) {
                        return skippedDuplicateFrom(cmd, winner.get());
                    }
                }
                return errorResult(e.getMessage(), cmd.provenance().rowId());
            }
        }
        return errorResult(
                lastVersionConflict == null ? "exhausted retries" : lastVersionConflict.getMessage(),
                cmd.provenance().rowId());
    }

    private static boolean isUnexpectedPersistenceFailure(Throwable e) {
        Throwable cause = e;
        while (cause != null) {
            if (cause instanceof java.sql.SQLIntegrityConstraintViolationException) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }

    private static void backoff() {
        try {
            Thread.sleep(VERSION_CONFLICT_BACKOFF_MILLIS);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        }
    }

    @Transactional(Transactional.TxType.REQUIRES_NEW)
    public RequisitionResult create(RequisitionCommand cmd) {
        // 1. Validate (rejections are NOT ledgered).
        RequisitionResult rejection = validate(cmd);
        if (rejection != null) {
            return rejection;
        }

        // 2. Duplicate check (tenant-scoped).
        String idempotencyKey = cmd.provenance().idempotencyKey();
        String tenantId = cmd.provenance().tenantId();
        Optional<WritebackLedger> existing = findByIdempotencyKey(tenantId, idempotencyKey);
        if (existing.isPresent()) {
            return skippedDuplicateFrom(cmd, existing.get());
        }

        PnMaster pnMaster = repo.findActivePn(cmd.pn()).orElseThrow();
        boolean consumable = repo.isConsumable(pnMaster.getCategory());

        // 3. Category-aware qty (NumericPolicy) — consumable keeps decimals, else truncated.
        BigDecimal qty = NumericPolicy.apply(cmd.qty(), consumable);

        Instant now = Instant.now();
        Date nowDate = Date.from(now);
        String principal = cmd.provenance().principal();
        long requisitionNumber = Long.parseLong(numberSource.nextRequisitionNumber());

        // 4. Header — field set ported from ARMAC's RequisitionData.CreateRequisition:
        // type/priority REOR, status OPEN, requester location, company, created/modified stamps.
        RequisitionHeader header = new RequisitionHeader();
        header.setRequisition(requisitionNumber);
        header.setRequisitionDescription("As per RIOSYS Recommendation");
        header.setRequistionType("REOR");
        header.setPriority("REOR");
        header.setRequesterLocation(cmd.location());
        header.setCompany(repo.company());
        header.setStatus("OPEN");
        header.setCreatedBy(principal);
        header.setCreatedDate(nowDate);
        header.setModifiedBy(principal);
        header.setModifiedDate(nowDate);
        // ARMAC sets these three UNCONDITIONALLY (RequisitionData.java:114-116) — not gated on
        // checkSwitch() or any other condition, unlike the RELEASE_FOR_AUTHORIZATION pair.
        header.setAuthorization("Y");
        header.setAuthorizedBy("TRAX_IFACE");
        header.setAuthorizedDate(nowDate);
        repo.persistOrMerge(header);

        // 5. Detail line 1 — pn/location/qty, status OPEN.
        RequisitionDetailPK detailPk = new RequisitionDetailPK();
        detailPk.setRequisition(requisitionNumber);
        detailPk.setRequisitionLine(DETAIL_LINE);

        RequisitionDetail detail = new RequisitionDetail();
        detail.setId(detailPk);
        detail.setPn(cmd.pn());
        detail.setLocation(cmd.location());
        detail.setQtyRequire(qty);
        detail.setStatus("OPEN");
        if (cmd.needBy() != null) {
            detail.setRequireDate(Date.from(cmd.needBy().atStartOfDay(java.time.ZoneOffset.UTC).toInstant()));
        }
        // ARMAC sets UOM unconditionally too (RequisitionData.java:196-204): PnMaster's stock UOM
        // when present/non-blank, else a hardcoded "EA" fallback.
        String stockUom = pnMaster.getStockUom();
        detail.setUom(stockUom == null || stockUom.isBlank() ? "EA" : stockUom);
        detail.setCreatedBy(principal);
        detail.setCreatedDate(nowDate);
        detail.setModifiedBy(principal);
        detail.setModifiedDate(nowDate);
        repo.persistOrMerge(detail);

        // 6. Audit rows mirroring both header and detail (same created-by/date/company discipline
        // as slice 1's PN_INVENTORY_LEVEL_AUDIT).
        RequisitionHeaderAuditPK headerAuditPk = new RequisitionHeaderAuditPK();
        headerAuditPk.setRequisition(requisitionNumber);
        headerAuditPk.setModifiedBy(principal);
        headerAuditPk.setModifiedDate(nowDate);

        RequisitionHeaderAudit headerAudit = new RequisitionHeaderAudit();
        headerAudit.setId(headerAuditPk);
        headerAudit.setRequisitionDescription(header.getRequisitionDescription());
        headerAudit.setRequistionType(header.getRequistionType());
        headerAudit.setPriority(header.getPriority());
        headerAudit.setRequesterLocation(header.getRequesterLocation());
        headerAudit.setCompany(header.getCompany());
        headerAudit.setStatus(header.getStatus());
        headerAudit.setCreatedBy(header.getCreatedBy());
        headerAudit.setCreatedDate(header.getCreatedDate());
        repo.persistOrMerge(headerAudit);

        RequisitionDetailAuditPK detailAuditPk = new RequisitionDetailAuditPK();
        detailAuditPk.setRequisition(requisitionNumber);
        detailAuditPk.setRequisitionLine(DETAIL_LINE);
        detailAuditPk.setModifiedBy(principal);
        detailAuditPk.setModifiedDate(nowDate);

        RequisitionDetailAudit detailAudit = new RequisitionDetailAudit();
        detailAudit.setId(detailAuditPk);
        detailAudit.setPn(detail.getPn());
        detailAudit.setLocation(detail.getLocation());
        detailAudit.setQtyRequire(detail.getQtyRequire());
        detailAudit.setStatus(detail.getStatus());
        detailAudit.setRequireDate(detail.getRequireDate());
        detailAudit.setCreatedBy(detail.getCreatedBy());
        detailAudit.setCreatedDate(detail.getCreatedDate());
        repo.persistOrMerge(detailAudit);

        // 7. Ledger insert (same tx): version chaining shared with STOCK_LEVEL/TRANSFER (D10).
        Long previousMaxVersion = maxVersion(tenantId, cmd.pn(), cmd.location());
        long version = previousMaxVersion == null ? 1L : previousMaxVersion + 1L;
        String requisitionRef = Long.toString(requisitionNumber);
        String message = "requisition " + requisitionRef + " created";

        WritebackLedger ledger = new WritebackLedger();
        ledger.setIdempotencyKey(idempotencyKey);
        ledger.setTenantId(tenantId);
        ledger.setRunId(cmd.provenance().runId());
        ledger.setRowId(cmd.provenance().rowId());
        ledger.setProvenanceId(cmd.provenance().provenanceId());
        ledger.setPn(cmd.pn());
        ledger.setLocation(cmd.location());
        ledger.setSource(cmd.provenance().source());
        ledger.setTier(cmd.provenance().tier());
        ledger.setApprover(cmd.provenance().approver());
        ledger.setPrincipal(principal);
        ledger.setAgentVersion(AGENT_VERSION);
        ledger.setOutcome("WRITTEN");
        ledger.setDomain(DOMAIN_REQUISITION);
        ledger.setCreatedRef(requisitionRef);
        ledger.setVersion(version);
        ledger.setParentVersion(previousMaxVersion);
        ledger.setMessage(message);
        ledger.setCreatedAt(now);

        em.persist(ledger);
        em.flush();

        return new RequisitionResult(
                ResultStatus.ACCEPTED, codeFor(ResultStatus.ACCEPTED), message, cmd.provenance().rowId(),
                requisitionRef, DETAIL_LINE);
    }

    private RequisitionResult validate(RequisitionCommand cmd) {
        Long rowId = cmd.provenance().rowId();

        Optional<PnMaster> pnMaster = repo.findActivePn(cmd.pn());
        if (pnMaster.isEmpty()) {
            Optional<String> actualStatus = pnStatusRegardlessOfActivity(cmd.pn());
            if (actualStatus.isPresent()) {
                return rejection(
                        ResultStatus.REJECTED_VALIDATION,
                        "PN " + cmd.pn() + " is not active, status=" + actualStatus.get(),
                        rowId);
            }
            return rejection(ResultStatus.REJECTED_UNKNOWN_KEY, "unknown PN: " + cmd.pn(), rowId);
        }

        Optional<LocationMaster> location = repo.findActiveInventoryLocation(cmd.location());
        if (location.isEmpty()) {
            if (!locationExistsRegardlessOfEligibility(cmd.location())) {
                return rejection(
                        ResultStatus.REJECTED_UNKNOWN_KEY, "unknown location: " + cmd.location(), rowId);
            }
            return rejection(
                    ResultStatus.REJECTED_VALIDATION,
                    "location is not an active, non-quarantine inventory location: " + cmd.location(),
                    rowId);
        }

        if (cmd.qty() == null || cmd.qty().signum() <= 0) {
            return rejection(ResultStatus.REJECTED_VALIDATION, "qty must be > 0", rowId);
        }

        return null;
    }

    private Optional<String> pnStatusRegardlessOfActivity(String pn) {
        try {
            return Optional.of(em.createQuery("select p.status from PnMaster p where p.pn = :pn", String.class)
                    .setParameter("pn", pn)
                    .getSingleResult());
        } catch (jakarta.persistence.NoResultException e) {
            return Optional.empty();
        }
    }

    private boolean locationExistsRegardlessOfEligibility(String location) {
        try {
            em.createQuery("select l.location from LocationMaster l where l.location = :location", String.class)
                    .setParameter("location", location)
                    .getSingleResult();
            return true;
        } catch (jakarta.persistence.NoResultException e) {
            return false;
        }
    }

    /**
     * Looks up a ledger row by (tenant, idempotency key), scoped exactly like {@link
     * StockLevelWriter#findByIdempotencyKey} — shared uniqueness constraint, so the same lookup
     * shape applies regardless of domain.
     */
    public Optional<WritebackLedger> findByIdempotencyKey(String tenantId, String key) {
        return em.createQuery(
                        "select l from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.idempotencyKey = :key",
                        WritebackLedger.class)
                .setParameter("tenantId", tenantId)
                .setParameter("key", key)
                .getResultStream()
                .findFirst();
    }

    private Long maxVersion(String tenantId, String pn, String location) {
        return em.createQuery(
                        "select max(l.version) from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.pn = :pn and l.location = :location",
                        Long.class)
                .setParameter("tenantId", tenantId)
                .setParameter("pn", pn)
                .setParameter("location", location)
                .getSingleResult();
    }

    private RequisitionResult skippedDuplicateFrom(RequisitionCommand cmd, WritebackLedger ledger) {
        return new RequisitionResult(
                ResultStatus.SKIPPED_DUPLICATE,
                codeFor(ResultStatus.SKIPPED_DUPLICATE),
                "duplicate idempotency key: " + ledger.getIdempotencyKey(),
                cmd.provenance().rowId(),
                ledger.getCreatedRef(),
                DETAIL_LINE);
    }

    private RequisitionResult rejection(ResultStatus status, String message, Long rowId) {
        return new RequisitionResult(status, codeFor(status), message, rowId, null, null);
    }

    private RequisitionResult errorResult(String message, Long rowId) {
        return new RequisitionResult(ResultStatus.ERROR, codeFor(ResultStatus.ERROR), message, rowId, null, null);
    }

    private static int codeFor(ResultStatus status) {
        return switch (status) {
            case ACCEPTED, SHADOWED, SKIPPED_DUPLICATE -> 200;
            case REJECTED_VALIDATION, REJECTED_UNKNOWN_KEY -> 400;
            case ERROR -> 500;
        };
    }
}
