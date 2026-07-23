package trax.io.writeback.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Concurrency-focused test for {@link RollbackService}, complementing the HTTP-level behaviors in
 * {@code TraxIoRollbackTest}. Exercises the domain service as a CDI bean directly so a true race
 * on the "latest written" read can be forced deterministically with {@link CountDownLatch} —
 * mirroring {@code StockLevelWriterTest}'s own concurrency tests.
 */
@QuarkusTest
class RollbackServiceTest {

    @Inject RollbackService rollbackService;
    @Inject StockLevelWriter writer;
    @Inject RequisitionCreator requisitionCreator;
    @Inject EntityManager em;

    /**
     * PR #5 review finding: requisition/transfer ledger rows share the {@code (tenant, pn,
     * location)} version chain with STOCK_LEVEL rows (D10) but carry null {@code old_values}/{@code
     * new_values} — before {@link RollbackService#findLatestWritten} was domain-scoped, a
     * requisition create that landed AFTER the latest stock-level write would become the (highest
     * version) "latest written" row, and its null {@code old_values} would resolve to {@code
     * NOTHING_TO_REVERT} even though a perfectly good stock-level version existed to revert to. The
     * fix restricts the search to {@code STOCK_LEVEL}-domain rows, so the requisition is skipped and
     * the search finds the real target: the v2 stock-level write, reverting it back to v1.
     */
    @Test
    void rollback_ignores_requisition_and_transfer_rows() {
        seedPn("PN-RB-XDOM", "SLW-ROTABLE", "ACTIVE");
        seedLocation("LOC-RB-XDOM", "Y", "N");

        write("PN-RB-XDOM", "LOC-RB-XDOM", 5, "rb-xdom-k1");
        write("PN-RB-XDOM", "LOC-RB-XDOM", 7, "rb-xdom-k2");

        var reqCmd = new RequisitionCommand(
                "PN-RB-XDOM",
                "LOC-RB-XDOM",
                new BigDecimal("3"),
                LocalDate.of(2026, 8, 1),
                null,
                new Provenance("acme", "optimizer", "rb-xdom-req", 1L, null, null, null, null, "planner"));
        RequisitionResult reqResult = requisitionCreator.createDedup(reqCmd);
        assertEquals(ResultStatus.ACCEPTED, reqResult.status());

        // Sanity: the requisition really is now the highest-versioned ledger row for this key.
        List<WritebackLedger> historyBeforeRollback = writer.history("acme", "PN-RB-XDOM", "LOC-RB-XDOM");
        assertEquals(2, historyBeforeRollback.size(), "history() must exclude the requisition row");

        RollbackCommand cmd = new RollbackCommand(
                "acme",
                "PN-RB-XDOM",
                "LOC-RB-XDOM",
                "bad rec",
                "planner",
                Instant.parse("2026-04-01T00:00:00Z"));
        RollbackOutcome outcome = rollbackService.rollback(cmd);

        assertEquals(
                RollbackStatus.ROLLED_BACK,
                outcome.status(),
                "a trailing requisition row must not shadow the stock-level rollback target");
        assertEquals(7, outcome.fromValues().get("rop"));
        assertEquals(5, outcome.toValues().get("rop"));
        assertEquals(2L, outcome.revertedFromVersion(), "must revert the v2 STOCK_LEVEL write, not the requisition");
        assertEquals(null, outcome.errorMessage());
    }

    @Test
    void concurrent_duplicate_rollback_requests_replay_the_same_rolled_back_response() throws Exception {
        // Two concurrent rollback requests racing to revert the SAME entry (e.g. a network-level
        // retry of the exact same HTTP request): both must read "latest written" = version 2
        // BEFORE either commits its reverting write, so both derive the identical idempotency key
        // "rollback:rb-race-k2" — StockLevelWriter.writeItemDedup then resolves the loser to
        // SKIPPED_DUPLICATE, and RollbackService.reconstructFromDuplicate must translate that into
        // the SAME faithful ROLLED_BACK response the winner received, rather than erroring or
        // ping-ponging. A CountDownLatch forces both threads past the read before either proceeds,
        // since two sequential HTTP calls can't exercise this (the first call's own reverting
        // write becomes the new "latest written" before a second call's query ever runs — see
        // TraxIoRollbackTest#second_sequential_rollback_ping_pongs_to_the_next_latest_written).
        seedPn("PN-RB-RACE", "SLW-ROTABLE", "ACTIVE");
        seedLocation("LOC-RB-RACE", "Y", "N");

        write("PN-RB-RACE", "LOC-RB-RACE", 5, "rb-race-k1");
        write("PN-RB-RACE", "LOC-RB-RACE", 7, "rb-race-k2");

        RollbackCommand cmd =
                new RollbackCommand(
                        "acme", "PN-RB-RACE", "LOC-RB-RACE", "bad rec", "planner", Instant.parse("2026-04-01T00:00:00Z"));

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);
        try {
            Future<RollbackOutcome> f1 =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                // RollbackService.rollback issues a plain (non-@Transactional)
                                // EntityManager query before delegating to writeItemDedup (which
                                // manages its own REQUIRES_NEW transactions) — in production this
                                // runs inside the ambient JAX-RS request scope Quarkus activates
                                // per HTTP request, but a raw executor thread here has no such
                                // context, so we activate one explicitly, matching how a real
                                // request would look to this call.
                                return QuarkusTransaction.requiringNew().call(() -> rollbackService.rollback(cmd));
                            });
            Future<RollbackOutcome> f2 =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                return QuarkusTransaction.requiringNew().call(() -> rollbackService.rollback(cmd));
                            });

            ready.await();
            go.countDown();

            RollbackOutcome r1 = f1.get(30, TimeUnit.SECONDS);
            RollbackOutcome r2 = f2.get(30, TimeUnit.SECONDS);

            for (RollbackOutcome r : List.of(r1, r2)) {
                assertEquals(RollbackStatus.ROLLED_BACK, r.status());
                assertEquals(7, r.fromValues().get("rop"));
                assertEquals(5, r.toValues().get("rop"));
                assertEquals(2L, r.revertedFromVersion());
                assertEquals(3L, r.newVersion());
                assertEquals(null, r.errorMessage());
            }

            List<trax.io.writeback.persistence.WritebackLedger> history =
                    writer.history("acme", "PN-RB-RACE", "LOC-RB-RACE");
            assertEquals(3, history.size(), "exactly one new ledger row from the reverting write");
        } finally {
            pool.shutdownNow();
        }
    }

    // ---- helpers ----

    private void write(String pn, String location, int rop, String idempotencyKey) {
        WritebackCommand cmd =
                new WritebackCommand(
                        pn,
                        location,
                        new LevelValues(
                                BigDecimal.valueOf(rop),
                                BigDecimal.valueOf(4),
                                BigDecimal.valueOf(2),
                                BigDecimal.valueOf(12),
                                null,
                                null,
                                null),
                        new Provenance("acme", "agent-spine", null, null, "p-1", idempotencyKey, null, null, "planner"),
                        false);
        ItemResult result = writer.writeItemDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, result.status());
    }

    private void seedPn(String pn, String category, String status) {
        QuarkusTransaction.requiringNew()
                .run(
                        () ->
                                em.createNativeQuery(
                                                "INSERT INTO PN_MASTER (PN, CATEGORY, STATUS) VALUES (?1, ?2, ?3)")
                                        .setParameter(1, pn)
                                        .setParameter(2, category)
                                        .setParameter(3, status)
                                        .executeUpdate());
    }

    private void seedLocation(String location, String inventory, String inventoryQuarantine) {
        QuarkusTransaction.requiringNew()
                .run(
                        () ->
                                em.createNativeQuery(
                                                "INSERT INTO LOCATION_MASTER (LOCATION, INVENTORY, INVENTORY_QUARANTINE) VALUES (?1, ?2, ?3)")
                                        .setParameter(1, location)
                                        .setParameter(2, inventory)
                                        .setParameter(3, inventoryQuarantine)
                                        .executeUpdate());
    }
}
