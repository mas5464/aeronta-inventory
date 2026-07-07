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
class TransferCreatorTest {

    @Inject TransferCreator creator;
    @Inject EntityManager em;

    // ---- validation ----

    @Test
    void unknown_pn_rejected() {
        seedLocation("TRF-JFK1", "Y", "N");
        seedLocation("TRF-LAX1", "Y", "N");

        var cmd = command("TRF-NOPE", "TRF-JFK1", "TRF-LAX1", "5", "trf-run-1", 1L);
        var result = creator.createDedup(cmd);
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, result.status());
        assertEquals(400, result.code());

        seedPn("TRF-PN-INACTIVE", "SLW-ROTABLE", "INACTIVE");
        var inactive = command("TRF-PN-INACTIVE", "TRF-JFK1", "TRF-LAX1", "5", "trf-run-1b", 1L);
        var inactiveResult = creator.createDedup(inactive);
        assertEquals(ResultStatus.REJECTED_VALIDATION, inactiveResult.status());
        assertEquals(400, inactiveResult.code());
    }

    @Test
    void ineligible_from_location_rejected() {
        seedPn("TRF-PN-FROM", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-TO1", "Y", "N");

        var unknownFrom = command("TRF-PN-FROM", "NOWHERE-FROM", "TRF-TO1", "5", "trf-run-2", 1L);
        var unknownResult = creator.createDedup(unknownFrom);
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, unknownResult.status());
        assertEquals(400, unknownResult.code());

        seedLocation("TRF-QUAR-FROM", "Y", "Y");
        var quarantined = command("TRF-PN-FROM", "TRF-QUAR-FROM", "TRF-TO1", "5", "trf-run-2b", 1L);
        var quarResult = creator.createDedup(quarantined);
        assertEquals(ResultStatus.REJECTED_VALIDATION, quarResult.status());
        assertEquals(400, quarResult.code());
    }

    @Test
    void ineligible_to_location_rejected() {
        seedPn("TRF-PN-TO", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-FROM1", "Y", "N");

        var unknownTo = command("TRF-PN-TO", "TRF-FROM1", "NOWHERE-TO", "5", "trf-run-3", 1L);
        var unknownResult = creator.createDedup(unknownTo);
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, unknownResult.status());
        assertEquals(400, unknownResult.code());

        seedLocation("TRF-QUAR-TO", "Y", "Y");
        var quarantined = command("TRF-PN-TO", "TRF-FROM1", "TRF-QUAR-TO", "5", "trf-run-3b", 1L);
        var quarResult = creator.createDedup(quarantined);
        assertEquals(ResultStatus.REJECTED_VALIDATION, quarResult.status());
        assertEquals(400, quarResult.code());
    }

    @Test
    void same_from_and_to_rejected() {
        seedPn("TRF-PN-SAME", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-SAME1", "Y", "N");

        var cmd = command("TRF-PN-SAME", "TRF-SAME1", "TRF-SAME1", "5", "trf-run-4", 1L);
        var result = creator.createDedup(cmd);
        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void non_positive_qty_rejected() {
        seedPn("TRF-PN-QTY", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-QTY-FROM", "Y", "N");
        seedLocation("TRF-QTY-TO", "Y", "N");

        var zero = command("TRF-PN-QTY", "TRF-QTY-FROM", "TRF-QTY-TO", "0", "trf-run-5", 1L);
        var zeroResult = creator.createDedup(zero);
        assertEquals(ResultStatus.REJECTED_VALIDATION, zeroResult.status());
        assertEquals(400, zeroResult.code());

        var negative = command("TRF-PN-QTY", "TRF-QTY-FROM", "TRF-QTY-TO", "-1", "trf-run-5b", 1L);
        var negativeResult = creator.createDedup(negative);
        assertEquals(ResultStatus.REJECTED_VALIDATION, negativeResult.status());
        assertEquals(400, negativeResult.code());
    }

    // ---- header/detail/audit creation ----

    @Test
    void creates_ts_order_header_and_detail_with_audits() {
        seedPn("TRF-PN-CREATE", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-FROM-CREATE", "Y", "N");
        seedLocation("TRF-TO-CREATE", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        LocalDate deliveryDate = LocalDate.of(2026, 8, 1);
        var cmd = new TransferCommand(
                "TRF-PN-CREATE",
                "TRF-FROM-CREATE",
                "TRF-TO-CREATE",
                new BigDecimal("12"),
                new BigDecimal("1001"),
                deliveryDate,
                new Provenance("acme", "optimizer", "trf-run-6", 9L, null, null, null, null, "planner"));

        var result = creator.createDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        assertEquals(200, result.code());
        assertEquals(Long.valueOf(9), result.rowId());
        assertNotNull(result.orderNumber());
        assertEquals("1001", result.batch());

        // Header: PK {orderType="TS", orderNumber}, requester/bill-to = TO location, shipped-from
        // = FROM location, status OPEN (ARMAC opens then closes the header — see
        // TransferCreator's Javadoc for why this slice leaves it OPEN), priority NORM,
        // inventory type MAINTENANCE, override address N, currency exchange 1, no-of-print 0,
        // and the UNCONDITIONAL authorization triple (StockTransferOrderData.java:145-147).
        Object[] header = (Object[])
                em.createNativeQuery(
                                "SELECT PRIORITY, STATUS, REQUESTER_LOCATION, BILL_TO_LOCATION, SHIPPED_FROM_LOCATION,"
                                        + " INVENTORY_TYPE, OVERRIDE_ADDRESS, CREATED_BY, MODIFIED_BY,"
                                        + " \"AUTHORIZATION\", AUTHORIZATION_BY, AUTHORIZATION_DATE"
                                        + " FROM ORDER_HEADER WHERE ORDER_TYPE = 'TS' AND ORDER_NUMBER = ?1")
                        .setParameter(1, result.orderNumber())
                        .getSingleResult();
        assertEquals("NORM", header[0]);
        assertEquals("OPEN", header[1]);
        assertEquals("TRF-TO-CREATE", header[2]);
        assertEquals("TRF-TO-CREATE", header[3]);
        assertEquals("TRF-FROM-CREATE", header[4]);
        assertEquals("MAINTENANCE", header[5]);
        assertEquals("N", header[6]);
        assertEquals("planner", header[7]);
        assertEquals("planner", header[8]);
        assertEquals("Y", header[9], "AUTHORIZATION must be set unconditionally, mirroring ARMAC");
        assertEquals("TRAX_IFACE", header[10], "AUTHORIZATION_BY must be set unconditionally, mirroring ARMAC");
        assertNotNull(header[11], "AUTHORIZATION_DATE must be set unconditionally, mirroring ARMAC");

        // Detail line 1: pn/location(=TO)/ro_location(=FROM)/qty/status/batch/delivery date.
        Object[] detail = (Object[])
                em.createNativeQuery(
                                "SELECT PN, LOCATION, RO_LOCATION, QTY_REQUIRE, STATUS, ORDER_LINE, BATCH, DELIVERY_DATE"
                                        + " FROM ORDER_DETAIL WHERE ORDER_TYPE = 'TS' AND ORDER_NUMBER = ?1")
                        .setParameter(1, result.orderNumber())
                        .getSingleResult();
        assertEquals("TRF-PN-CREATE", detail[0]);
        assertEquals("TRF-TO-CREATE", detail[1]);
        assertEquals("TRF-FROM-CREATE", detail[2]);
        assertEquals(0, new BigDecimal("12").compareTo((BigDecimal) detail[3]));
        assertEquals("OPEN", detail[4]);
        assertEquals(1L, ((Number) detail[5]).longValue());
        assertNotNull(detail[6], "BATCH must be persisted");
        assertNotNull(detail[7], "DELIVERY_DATE must be persisted");

        // Audit rows mirror both header and detail.
        long headerAuditCount = ((Number)
                        em.createNativeQuery(
                                        "SELECT COUNT(*) FROM ORDER_HEADER_AUDIT WHERE ORDER_TYPE = 'TS' AND ORDER_NUMBER = ?1")
                                .setParameter(1, result.orderNumber())
                                .getSingleResult())
                .longValue();
        assertEquals(1, headerAuditCount);

        long detailAuditCount = ((Number)
                        em.createNativeQuery(
                                        "SELECT COUNT(*) FROM ORDER_DETAIL_AUDIT WHERE ORDER_TYPE = 'TS' AND ORDER_NUMBER = ?1")
                                .setParameter(1, result.orderNumber())
                                .getSingleResult())
                .longValue();
        assertEquals(1, detailAuditCount);
    }

    // ---- qty category-awareness ----

    @Test
    void qty_is_category_aware() {
        seedPn("TRF-PN-CONS", "SLW-CONSUMABLE", "ACTIVE");
        seedLocation("TRF-CONS-FROM", "Y", "N");
        seedLocation("TRF-CONS-TO", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-CONSUMABLE", "C");

        seedPn("TRF-PN-ROT", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-ROT-FROM", "Y", "N");
        seedLocation("TRF-ROT-TO", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var consumable = command("TRF-PN-CONS", "TRF-CONS-FROM", "TRF-CONS-TO", "3.7", "trf-run-7", 1L);
        var consumableResult = creator.createDedup(consumable);
        assertEquals(ResultStatus.ACCEPTED, consumableResult.status());
        BigDecimal consumableQty = readDetailColumn(consumableResult.orderNumber(), "QTY_REQUIRE");
        assertEquals(0, new BigDecimal("3.7").compareTo(consumableQty), "consumable keeps decimals");

        var nonConsumable = command("TRF-PN-ROT", "TRF-ROT-FROM", "TRF-ROT-TO", "3.7", "trf-run-7b", 1L);
        var nonConsumableResult = creator.createDedup(nonConsumable);
        assertEquals(ResultStatus.ACCEPTED, nonConsumableResult.status());
        BigDecimal rotableQty = readDetailColumn(nonConsumableResult.orderNumber(), "QTY_REQUIRE");
        assertEquals(0, new BigDecimal("3").compareTo(rotableQty), "non-consumable truncates");
    }

    // ---- ledger ----

    @Test
    void ledger_row_domain_transfer_with_created_ref() {
        seedPn("TRF-PN-LEDGER", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-LEDGER-FROM", "Y", "N");
        seedLocation("TRF-LEDGER-TO", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("TRF-PN-LEDGER", "TRF-LEDGER-FROM", "TRF-LEDGER-TO", "5", "trf-run-8", 1L);
        var result = creator.createDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, result.status());

        WritebackLedger ledger = findLedger("acme", "trf-run-8:1");
        assertNotNull(ledger);
        assertEquals("WRITTEN", ledger.getOutcome());
        assertEquals(TransferCreator.DOMAIN_TRANSFER, ledger.getDomain());
        assertEquals(result.orderNumber(), ledger.getCreatedRef());
        assertNotNull(ledger.getMessage());
        // Ledger key location for a transfer is the FROM location (stock leaves there) — see
        // TransferCreator's Javadoc for the rationale.
        assertEquals("TRF-LEDGER-FROM", ledger.getLocation());
    }

    // ---- duplicates ----

    @Test
    void duplicate_returns_skipped_with_original_order_number() {
        seedPn("TRF-PN-DUP", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-DUP-FROM", "Y", "N");
        seedLocation("TRF-DUP-TO", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("TRF-PN-DUP", "TRF-DUP-FROM", "TRF-DUP-TO", "5", "trf-run-9", 1L);
        var first = creator.createDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, first.status());
        assertNotNull(first.orderNumber());

        // Same idempotency key (runId + rowId) but different payload: original wins.
        var replay = command("TRF-PN-DUP", "TRF-DUP-FROM", "TRF-DUP-TO", "999", "trf-run-9", 1L);
        var second = creator.createDedup(replay);

        assertEquals(ResultStatus.SKIPPED_DUPLICATE, second.status());
        assertEquals(200, second.code());
        assertEquals(first.orderNumber(), second.orderNumber(), "replay must carry the ORIGINAL order number");

        long headerCount = ((Number)
                        em.createNativeQuery(
                                        "SELECT COUNT(*) FROM ORDER_HEADER WHERE ORDER_TYPE = 'TS' AND SHIPPED_FROM_LOCATION = ?1")
                                .setParameter(1, "TRF-DUP-FROM")
                                .getSingleResult())
                .longValue();
        assertEquals(1, headerCount, "replay must not create a second transfer order");
    }

    // ---- concurrency ----

    @Test
    void concurrent_same_key_creates_exactly_one_order() throws Exception {
        seedPn("TRF-PN-RACE", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRF-RACE-FROM", "Y", "N");
        seedLocation("TRF-RACE-TO", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("TRF-PN-RACE", "TRF-RACE-FROM", "TRF-RACE-TO", "5", "trf-run-10", 1L);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);

        try {
            Future<TransferResult> f1 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return creator.createDedup(cmd);
            });
            Future<TransferResult> f2 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return creator.createDedup(cmd);
            });

            ready.await();
            go.countDown();

            TransferResult r1 = f1.get(30, TimeUnit.SECONDS);
            TransferResult r2 = f2.get(30, TimeUnit.SECONDS);

            List<TransferResult> results = List.of(r1, r2);
            long acceptedCount =
                    results.stream().filter(r -> r.status() == ResultStatus.ACCEPTED).count();
            long skippedCount =
                    results.stream().filter(r -> r.status() == ResultStatus.SKIPPED_DUPLICATE).count();

            assertEquals(1, acceptedCount, "exactly one thread should win ACCEPTED");
            assertEquals(1, skippedCount, "exactly one thread should be SKIPPED_DUPLICATE");
            assertEquals(r1.orderNumber(), r2.orderNumber(), "both results must agree on the order number");

            long headerCount = ((Number)
                            em.createNativeQuery(
                                            "SELECT COUNT(*) FROM ORDER_HEADER WHERE ORDER_TYPE = 'TS' AND SHIPPED_FROM_LOCATION = ?1")
                                    .setParameter(1, "TRF-RACE-FROM")
                                    .getSingleResult())
                    .longValue();
            assertEquals(1, headerCount, "exactly one transfer order header row created");
        } finally {
            pool.shutdownNow();
        }
    }

    // ---- helpers ----

    private TransferCommand command(
            String pn, String fromLocation, String toLocation, String qty, String runId, Long rowId) {
        return new TransferCommand(
                pn,
                fromLocation,
                toLocation,
                new BigDecimal(qty),
                new BigDecimal("1000"),
                LocalDate.of(2026, 8, 1),
                new Provenance("acme", "optimizer", runId, rowId, null, null, null, null, "planner"));
    }

    private void seedPn(String pn, String category, String status) {
        QuarkusTransaction.requiringNew()
                .run(() -> em.createNativeQuery(
                                "INSERT INTO PN_MASTER (PN, CATEGORY, STATUS, STOCK_UOM) VALUES (?1, ?2, ?3, ?4)")
                        .setParameter(1, pn)
                        .setParameter(2, category)
                        .setParameter(3, status)
                        .setParameter(4, "EA")
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

    private BigDecimal readDetailColumn(String orderNumber, String column) {
        return QuarkusTransaction.requiringNew()
                .call(() -> (BigDecimal) em.createNativeQuery(
                                "SELECT " + column + " FROM ORDER_DETAIL WHERE ORDER_TYPE = 'TS' AND ORDER_NUMBER = ?1")
                        .setParameter(1, orderNumber)
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
