package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import jakarta.transaction.Transactional;
import org.junit.jupiter.api.Test;

@QuarkusTest
class TraxRepositoryTest {
    @Inject TraxRepository repo;
    @Inject EntityManager em;

    @Test
    @Transactional
    void active_pn_found_inactive_filtered() {
        seedPn("PN-A", "ROTABLE", "ACTIVE");
        seedPn("PN-B", "ROTABLE", "INACTIVE");
        assertTrue(repo.findActivePn("PN-A").isPresent());
        assertTrue(repo.findActivePn("PN-B").isEmpty());
        assertTrue(repo.findActivePn("NOPE").isEmpty());
    }

    @Test
    @Transactional
    void location_must_be_inventory_and_not_quarantine() {
        seedLocation("JFK", "Y", "N");
        seedLocation("QUAR", "Y", "Y");
        seedLocation("OFFICE", "N", "N");
        assertTrue(repo.findActiveInventoryLocation("JFK").isPresent());
        assertTrue(repo.findActiveInventoryLocation("QUAR").isEmpty());
        assertTrue(repo.findActiveInventoryLocation("OFFICE").isEmpty());
    }

    @Test
    @Transactional
    void consumable_category_detected_via_pncategory_trancode() {
        seedTranCode("PNCATEGORY", "CONSUMABLE", "C");
        seedTranCode("PNCATEGORY", "ROTABLE", "R");
        assertTrue(repo.isConsumable("CONSUMABLE"));
        assertFalse(repo.isConsumable("ROTABLE"));
        assertFalse(repo.isConsumable("UNKNOWN"));
    }

    @Test
    @Transactional
    void company_defaults_to_TRAX_when_no_profile() {
        assertEquals("TRAX", repo.company());
    }

    private void seedPn(String pn, String category, String status) {
        em.createNativeQuery("INSERT INTO PN_MASTER (PN, CATEGORY, STATUS) VALUES (?1, ?2, ?3)")
                .setParameter(1, pn)
                .setParameter(2, category)
                .setParameter(3, status)
                .executeUpdate();
    }

    private void seedLocation(String location, String inventory, String inventoryQuarantine) {
        em.createNativeQuery(
                        "INSERT INTO LOCATION_MASTER (LOCATION, INVENTORY, INVENTORY_QUARANTINE) VALUES (?1, ?2, ?3)")
                .setParameter(1, location)
                .setParameter(2, inventory)
                .setParameter(3, inventoryQuarantine)
                .executeUpdate();
    }

    private void seedTranCode(String systemTransaction, String systemCode, String pnTransaction) {
        em.createNativeQuery(
                        "INSERT INTO SYSTEM_TRAN_CODE (SYSTEM_TRANSACTION, SYSTEM_CODE, SYSTEM_TRAN_CODE_SUB, PN_TRANSACTION) VALUES (?1, ?2, ?3, ?4)")
                .setParameter(1, systemTransaction)
                .setParameter(2, systemCode)
                // Oracle stores '' as NULL and SYSTEM_TRAN_CODE_SUB is part of the PK
                // (NOT NULL), so seed a real sub-code. Lookups ignore it (legacy parity).
                .setParameter(3, "SUB")
                .setParameter(4, pnTransaction)
                .executeUpdate();
    }
}
