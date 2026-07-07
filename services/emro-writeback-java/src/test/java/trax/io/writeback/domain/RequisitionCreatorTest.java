package trax.io.writeback.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import trax.io.writeback.persistence.WritebackLedger;

@QuarkusTest
class RequisitionCreatorTest {

    @Inject RequisitionCreator creator;
    @Inject EntityManager em;

    // ---- validation ----

    @Test
    void unknown_or_inactive_pn_rejected() {
        seedLocation("REQ-JFK1", "Y", "N");

        var unknown = command("REQ-NOPE", "REQ-JFK1", "5", "req-run-1", 1L);
        var unknownResult = creator.createDedup(unknown);
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, unknownResult.status());
        assertEquals(400, unknownResult.code());

        seedPn("REQ-PN-INACTIVE", "SLW-ROTABLE", "INACTIVE");
        var inactive = command("REQ-PN-INACTIVE", "REQ-JFK1", "5", "req-run-1b", 1L);
        var inactiveResult = creator.createDedup(inactive);
        assertEquals(ResultStatus.REJECTED_VALIDATION, inactiveResult.status());
        assertEquals(400, inactiveResult.code());
    }

    @Test
    void ineligible_location_rejected() {
        seedPn("PN-LOC", "SLW-ROTABLE", "ACTIVE");

        var unknownLoc = command("PN-LOC", "NOWHERE", "5", "req-run-2", 1L);
        var unknownResult = creator.createDedup(unknownLoc);
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, unknownResult.status());
        assertEquals(400, unknownResult.code());

        seedLocation("QUAR1", "Y", "Y");
        var quarantined = command("PN-LOC", "QUAR1", "5", "req-run-2b", 1L);
        var quarResult = creator.createDedup(quarantined);
        assertEquals(ResultStatus.REJECTED_VALIDATION, quarResult.status());
        assertEquals(400, quarResult.code());
    }

    @Test
    void non_positive_qty_rejected() {
        seedPn("PN-QTY", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-QTY", "Y", "N");

        var zero = command("PN-QTY", "JFK-QTY", "0", "req-run-3", 1L);
        var zeroResult = creator.createDedup(zero);
        assertEquals(ResultStatus.REJECTED_VALIDATION, zeroResult.status());
        assertEquals(400, zeroResult.code());

        var negative = command("PN-QTY", "JFK-QTY", "-1", "req-run-3b", 1L);
        var negativeResult = creator.createDedup(negative);
        assertEquals(ResultStatus.REJECTED_VALIDATION, negativeResult.status());
        assertEquals(400, negativeResult.code());
    }

    @Test
    void qty_is_category_aware() {
        seedPn("PN-CONS-REQ", "SLW-CONSUMABLE", "ACTIVE");
        seedLocation("JFK-CONS-REQ", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-CONSUMABLE", "C");

        seedPn("PN-ROT-REQ", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-ROT-REQ", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var consumable = command("PN-CONS-REQ", "JFK-CONS-REQ", "3.7", "req-run-4", 1L);
        var consumableResult = creator.createDedup(consumable);
        assertEquals(ResultStatus.ACCEPTED, consumableResult.status());
        BigDecimal consumableQty = readDetailColumn(consumableResult.requisition(), "QTY_REQUIRE");
        assertEquals(0, new BigDecimal("3.7").compareTo(consumableQty), "consumable keeps decimals");

        var nonConsumable = command("PN-ROT-REQ", "JFK-ROT-REQ", "3.7", "req-run-4b", 1L);
        var nonConsumableResult = creator.createDedup(nonConsumable);
        assertEquals(ResultStatus.ACCEPTED, nonConsumableResult.status());
        BigDecimal rotableQty = readDetailColumn(nonConsumableResult.requisition(), "QTY_REQUIRE");
        assertEquals(0, new BigDecimal("3").compareTo(rotableQty), "non-consumable truncates");
    }

    // ---- header/detail/audit creation ----

    @Test
    void creates_header_and_detail_with_audits() {
        seedPn("PN-CREATE", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-CREATE", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        LocalDate needBy = LocalDate.of(2026, 8, 1);
        var cmd = new RequisitionCommand(
                "PN-CREATE",
                "JFK-CREATE",
                new BigDecimal("12"),
                needBy,
                "please expedite",
                new Provenance("acme", "optimizer", "req-run-5", 9L, null, null, null, null, "planner"));

        var result = creator.createDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        assertEquals(200, result.code());
        assertEquals(Long.valueOf(9), result.rowId());
        assertNotNull(result.requisition());
        assertTrue(Long.parseLong(result.requisition()) > 0, "requisition number must be numeric");
        assertEquals(Integer.valueOf(1), result.line());

        long requisitionNo = Long.parseLong(result.requisition());

        // Header field set (ARMAC-ported): type/priority REOR, status OPEN, requester location,
        // company, created/modified stamps from provenance principal.
        Object[] header = (Object[])
                em.createNativeQuery(
                                "SELECT REQUISTION_TYPE, PRIORITY, STATUS, REQUESTER_LOCATION, COMPANY,"
                                        + " CREATED_BY, MODIFIED_BY FROM REQUISITION_HEADER WHERE REQUISITION = ?1")
                        .setParameter(1, requisitionNo)
                        .getSingleResult();
        assertEquals("REOR", header[0]);
        assertEquals("REOR", header[1]);
        assertEquals("OPEN", header[2]);
        assertEquals("JFK-CREATE", header[3]);
        assertNotNull(header[4], "company must be populated from repo.company()");
        assertEquals("planner", header[5]);
        assertEquals("planner", header[6]);

        // Detail line 1: pn/location/qty/status.
        Object[] detail = (Object[])
                em.createNativeQuery(
                                "SELECT PN, LOCATION, QTY_REQUIRE, STATUS, REQUISITION_LINE"
                                        + " FROM REQUISITION_DETAIL WHERE REQUISITION = ?1")
                        .setParameter(1, requisitionNo)
                        .getSingleResult();
        assertEquals("PN-CREATE", detail[0]);
        assertEquals("JFK-CREATE", detail[1]);
        assertEquals(0, new BigDecimal("12").compareTo((BigDecimal) detail[2]));
        assertEquals("OPEN", detail[3]);
        assertEquals(1L, ((Number) detail[4]).longValue());

        // Audit rows mirror both header and detail.
        long headerAuditCount = ((Number)
                        em.createNativeQuery("SELECT COUNT(*) FROM REQUISITION_HEADER_AUDIT WHERE REQUISITION = ?1")
                                .setParameter(1, requisitionNo)
                                .getSingleResult())
                .longValue();
        assertEquals(1, headerAuditCount);

        long detailAuditCount = ((Number)
                        em.createNativeQuery("SELECT COUNT(*) FROM REQUISITION_DETAIL_AUDIT WHERE REQUISITION = ?1")
                                .setParameter(1, requisitionNo)
                                .getSingleResult())
                .longValue();
        assertEquals(1, detailAuditCount);
    }

    // ---- ledger ----

    @Test
    void ledger_row_domain_requisition_with_created_ref() {
        seedPn("PN-LEDGER", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-LEDGER", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-LEDGER", "JFK-LEDGER", "5", "req-run-6", 1L);
        var result = creator.createDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, result.status());

        WritebackLedger ledger = findLedger("acme", "req-run-6:1");
        assertNotNull(ledger);
        assertEquals("WRITTEN", ledger.getOutcome());
        assertEquals(RequisitionCreator.DOMAIN_REQUISITION, ledger.getDomain());
        assertEquals(result.requisition(), ledger.getCreatedRef());
        assertNotNull(ledger.getMessage());
    }

    // ---- duplicates ----

    @Test
    void duplicate_returns_skipped_with_original_requisition_number() {
        seedPn("PN-DUPREQ", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-DUPREQ", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-DUPREQ", "JFK-DUPREQ", "5", "req-run-7", 1L);
        var first = creator.createDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, first.status());
        assertNotNull(first.requisition());

        // Same idempotency key (runId + rowId) but different payload: original wins.
        var replay = command("PN-DUPREQ", "JFK-DUPREQ", "999", "req-run-7", 1L);
        var second = creator.createDedup(replay);

        assertEquals(ResultStatus.SKIPPED_DUPLICATE, second.status());
        assertEquals(200, second.code());
        assertEquals(first.requisition(), second.requisition(), "replay must carry the ORIGINAL requisition number");

        long headerCount = ((Number)
                        em.createNativeQuery(
                                        "SELECT COUNT(*) FROM REQUISITION_HEADER WHERE REQUESTER_LOCATION = ?1")
                                .setParameter(1, "JFK-DUPREQ")
                                .getSingleResult())
                .longValue();
        assertEquals(1, headerCount, "replay must not create a second requisition");
    }

    // ---- concurrency ----

    @Test
    void concurrent_same_key_creates_exactly_one_requisition() throws Exception {
        seedPn("PN-RACE-REQ", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-RACE-REQ", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-RACE-REQ", "JFK-RACE-REQ", "5", "req-run-8", 1L);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);

        try {
            Future<RequisitionResult> f1 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return creator.createDedup(cmd);
            });
            Future<RequisitionResult> f2 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return creator.createDedup(cmd);
            });

            ready.await();
            go.countDown();

            RequisitionResult r1 = f1.get(30, TimeUnit.SECONDS);
            RequisitionResult r2 = f2.get(30, TimeUnit.SECONDS);

            List<RequisitionResult> results = List.of(r1, r2);
            long acceptedCount =
                    results.stream().filter(r -> r.status() == ResultStatus.ACCEPTED).count();
            long skippedCount =
                    results.stream().filter(r -> r.status() == ResultStatus.SKIPPED_DUPLICATE).count();

            assertEquals(1, acceptedCount, "exactly one thread should win ACCEPTED");
            assertEquals(1, skippedCount, "exactly one thread should be SKIPPED_DUPLICATE");
            assertEquals(r1.requisition(), r2.requisition(), "both results must agree on the requisition number");

            long headerCount = ((Number)
                            em.createNativeQuery(
                                            "SELECT COUNT(*) FROM REQUISITION_HEADER WHERE REQUESTER_LOCATION = ?1")
                                    .setParameter(1, "JFK-RACE-REQ")
                                    .getSingleResult())
                    .longValue();
            assertEquals(1, headerCount, "exactly one requisition header row created");
        } finally {
            pool.shutdownNow();
        }
    }

    // ---- helpers ----

    private RequisitionCommand command(String pn, String location, String qty, String runId, Long rowId) {
        return new RequisitionCommand(
                pn,
                location,
                new BigDecimal(qty),
                LocalDate.of(2026, 8, 1),
                null,
                new Provenance("acme", "optimizer", runId, rowId, null, null, null, null, "planner"));
    }

    private void seedPn(String pn, String category, String status) {
        QuarkusTransaction.requiringNew()
                .run(() -> em.createNativeQuery("INSERT INTO PN_MASTER (PN, CATEGORY, STATUS) VALUES (?1, ?2, ?3)")
                        .setParameter(1, pn)
                        .setParameter(2, category)
                        .setParameter(3, status)
                        .executeUpdate());
    }

    private void seedLocation(String location, String inventory, String inventoryQuarantine) {
        QuarkusTransaction.requiringNew()
                .run(() -> em.createNativeQuery(
                                "INSERT INTO LOCATION_MASTER (LOCATION, INVENTORY, INVENTORY_QUARANTINE) VALUES (?1, ?2, ?3)")
                        .setParameter(1, location)
                        .setParameter(2, inventory)
                        .setParameter(3, inventoryQuarantine)
                        .executeUpdate());
    }

    private void seedTranCode(String systemTransaction, String systemCode, String pnTransaction) {
        boolean exists = QuarkusTransaction.requiringNew()
                .call(() -> {
                    var rows = em.createNativeQuery(
                                    "SELECT 1 FROM SYSTEM_TRAN_CODE WHERE SYSTEM_TRANSACTION = ?1 AND SYSTEM_CODE = ?2")
                            .setParameter(1, systemTransaction)
                            .setParameter(2, systemCode)
                            .getResultList();
                    return !rows.isEmpty();
                });
        if (exists) {
            return;
        }
        QuarkusTransaction.requiringNew()
                .run(() -> em.createNativeQuery(
                                "INSERT INTO SYSTEM_TRAN_CODE (SYSTEM_TRANSACTION, SYSTEM_CODE, SYSTEM_TRAN_CODE_SUB, PN_TRANSACTION) VALUES (?1, ?2, ?3, ?4)")
                        .setParameter(1, systemTransaction)
                        .setParameter(2, systemCode)
                        .setParameter(3, "SUB")
                        .setParameter(4, pnTransaction)
                        .executeUpdate());
    }

    private BigDecimal readDetailColumn(String requisition, String column) {
        long requisitionNo = Long.parseLong(requisition);
        return QuarkusTransaction.requiringNew()
                .call(() -> (BigDecimal) em.createNativeQuery(
                                "SELECT " + column + " FROM REQUISITION_DETAIL WHERE REQUISITION = ?1")
                        .setParameter(1, requisitionNo)
                        .getSingleResult());
    }

    private WritebackLedger findLedger(String tenantId, String idempotencyKey) {
        return QuarkusTransaction.requiringNew()
                .call(() -> em.createQuery(
                                "select l from WritebackLedger l"
                                        + " where l.tenantId = :tenantId and l.idempotencyKey = :key",
                                WritebackLedger.class)
                        .setParameter("tenantId", tenantId)
                        .setParameter("key", idempotencyKey)
                        .getResultStream()
                        .findFirst()
                        .orElse(null));
    }
}
