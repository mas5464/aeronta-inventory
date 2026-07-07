package trax.io.writeback.persistence;

import java.util.List;
import java.util.Optional;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import org.eclipse.microprofile.config.inject.ConfigProperty;
import jakarta.persistence.EntityManager;
import jakarta.persistence.LockModeType;

/**
 * Read/write lookups against the eMRO schema (PN_MASTER, LOCATION_MASTER, SYSTEM_TRAN_CODE,
 * PROFILE_MASTER, PN_INVENTORY_LEVEL) needed by the writeback pipeline.
 */
@ApplicationScoped
public class TraxRepository {

    @Inject
    EntityManager em;

    @Inject
    @ConfigProperty(name = "writeback.emro.company")
    Optional<String> configuredCompany;

    public Optional<PnMaster> findActivePn(String pn) {
        PnMaster found = em.find(PnMaster.class, pn);
        if (found == null || !"ACTIVE".equals(found.getStatus())) {
            return Optional.empty();
        }
        return Optional.of(found);
    }

    public Optional<LocationMaster> findActiveInventoryLocation(String location) {
        LocationMaster found = em.find(LocationMaster.class, location);
        if (found == null
                || !"Y".equals(found.getInventory())
                || "Y".equals(found.getInventoryQuarantine())) {
            return Optional.empty();
        }
        return Optional.of(found);
    }

    public boolean isConsumable(String pnCategory) {
        // Legacy PTCWebService (Application_Function.getTranCode) matches on SYSTEM_TRANSACTION +
        // SYSTEM_CODE only, ignoring SYSTEM_TRAN_CODE_SUB — mirror that here.
        return em
                .createQuery(
                        "select s.pnTransaction from SystemTranCode s"
                                + " where s.id.systemTransaction = 'PNCATEGORY'"
                                + " and s.id.systemCode = :cat",
                        String.class)
                .setParameter("cat", pnCategory)
                .getResultStream()
                .anyMatch("C"::equals);
    }

    public String company() {
        // Multi-profile eMRO installs exist (live smoke found 4 PROFILE_MASTER rows on the
        // reference instance) — when the operator sets writeback.emro.company, use it and
        // never guess from a single-row lookup.
        if (configuredCompany.isPresent() && !configuredCompany.get().isBlank()) {
            return configuredCompany.get();
        }
        try {
            return em.createQuery("select p.profile from ProfileMaster p", String.class)
                    .getSingleResult();
        } catch (RuntimeException e) {
            // No rows, multiple rows, or any other lookup failure: fall back to "TRAX"
            // (correct for single-tenant installs; multi-profile installs must configure
            // writeback.emro.company explicitly — the eMRO smoke test flags this).
            return "TRAX";
        }
    }

    public PnInventoryLevel findLevel(PnInventoryLevelPK pk) {
        return em.find(PnInventoryLevel.class, pk);
    }

    /**
     * Same lookup as {@link #findLevel}, but taken under {@code PESSIMISTIC_WRITE} so the row (if
     * it exists) is locked for the remainder of the caller's transaction. This is the read used by
     * every writeItem path that later captures {@code oldValues} and/or mutates the row — real
     * writes AND shadow writes alike — so two concurrent writers for the same (PN, LOCATION) never
     * both observe the same pre-conflict snapshot (Finding 2). The lock is released automatically
     * when the enclosing transaction commits or rolls back.
     */
    public PnInventoryLevel findLevelForUpdate(PnInventoryLevelPK pk) {
        return em.find(PnInventoryLevel.class, pk, LockModeType.PESSIMISTIC_WRITE);
    }

    public void persistOrMerge(Object entity) {
        em.merge(entity);
        em.flush();
    }

    /**
     * {@code PN_INVENTORY_LEVEL_AUDIT} rows for {@code (pn, location)} whose {@code MODIFIED_BY}
     * is NOT one of this service's own writing principals — i.e. edits made out-of-band by some
     * other eMRO writer (a planner, another integration). "This service's own principals" is
     * derived data-driven from {@code WRITEBACK_LEDGER.PRINCIPAL} for the same {@code (tenantId,
     * pn, location)} rather than any hardcoded principal list, per spec D13. Ordered newest-first
     * by {@code MODIFIED_DATE}.
     */
    public List<PnInventoryLevelAudit> findOutOfBandAudits(String tenantId, String pn, String location) {
        return em.createQuery(
                        "select a from PnInventoryLevelAudit a"
                                + " where a.id.pn = :pn and a.id.location = :location"
                                + " and (a.modifiedBy is null or a.modifiedBy not in ("
                                + "   select l.principal from WritebackLedger l"
                                + "   where l.tenantId = :tenantId and l.pn = :pn and l.location = :location"
                                + " ))"
                                + " order by a.modifiedDate desc",
                        PnInventoryLevelAudit.class)
                .setParameter("tenantId", tenantId)
                .setParameter("pn", pn)
                .setParameter("location", location)
                .getResultList();
    }

    /**
     * {@code WRITEBACK_LEDGER} rows for a run (D16): every row with the given {@code (tenantId,
     * runId)}, ordered by {@code createdAt} ascending then {@code rowId} ascending. Nullable
     * {@code rowId} is not explicitly steered with a {@code NULLS LAST} clause — Oracle's default
     * ascending-order null placement is already last, which is what this ordering wants (a
     * request-level ledger row with no per-item {@code rowId} sorts after its numbered peers).
     * Unknown {@code runId}/{@code tenantId} combinations simply return an empty list — there is
     * no distinct "run doesn't exist" signal at the ledger layer.
     */
    public List<WritebackLedger> findLedgerRowsForRun(String tenantId, String runId) {
        return em.createQuery(
                        "select l from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.runId = :runId"
                                + " order by l.createdAt asc, l.rowId asc",
                        WritebackLedger.class)
                .setParameter("tenantId", tenantId)
                .setParameter("runId", runId)
                .getResultList();
    }
}
