package trax.io.writeback.persistence;

import java.util.Optional;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;

/**
 * Read/write lookups against the eMRO schema (PN_MASTER, LOCATION_MASTER, SYSTEM_TRAN_CODE,
 * PROFILE_MASTER, PN_INVENTORY_LEVEL) needed by the writeback pipeline.
 */
@ApplicationScoped
public class TraxRepository {

    @Inject
    EntityManager em;

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
        try {
            return em.createQuery("select p.profile from ProfileMaster p", String.class)
                    .getSingleResult();
        } catch (RuntimeException e) {
            // No rows, multiple rows, or any other lookup failure: eMRO is effectively
            // single-tenant per install, so "TRAX" is the safe, always-correct default.
            return "TRAX";
        }
    }

    public PnInventoryLevel findLevel(PnInventoryLevelPK pk) {
        return em.find(PnInventoryLevel.class, pk);
    }

    public void persistOrMerge(Object entity) {
        em.merge(entity);
        em.flush();
    }
}
