package trax.io.writeback.domain;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.math.BigDecimal;
import java.sql.SQLIntegrityConstraintViolationException;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import trax.io.writeback.persistence.WritebackLedger;

@QuarkusTest
class StockLevelWriterTest {

    @Inject StockLevelWriter writer;
    @Inject EntityManager em;

    // ---- constraint-violation classifier (Finding 1) ----

    @Test
    void classifier_identifies_idempotency_constraint_by_name() {
        Exception e =
                new RuntimeException(
                        "ORA-00001: unique constraint (SCHEMA.UQ_WRITEBACK_IDEMPOTENCY) violated");
        assertEquals(
                StockLevelWriter.ConstraintViolation.IDEMPOTENCY_DUPLICATE,
                StockLevelWriter.classifyConstraintViolation(e));
    }

    @Test
    void classifier_identifies_version_constraint_by_name() {
        Exception e =
                new RuntimeException(
                        "ORA-00001: unique constraint (SCHEMA.UQ_WRITEBACK_KEY_VERSION) violated");
        assertEquals(
                StockLevelWriter.ConstraintViolation.VERSION_CONFLICT,
                StockLevelWriter.classifyConstraintViolation(e));
    }

    @Test
    void classifier_does_not_misclassify_a_different_constraint() {
        // A bare SQLIntegrityConstraintViolationException whose message names a DIFFERENT
        // constraint (e.g. a system-generated PK name, or the audit table's own PK) must NOT be
        // classified as either of our named constraints.
        Exception sysGenerated =
                new SQLIntegrityConstraintViolationException(
                        "ORA-00001: unique constraint (SCHEMA.SYS_C0012345) violated");
        assertEquals(
                StockLevelWriter.ConstraintViolation.NONE,
                StockLevelWriter.classifyConstraintViolation(sysGenerated));

        Exception wrapped =
                new RuntimeException(
                        "wrapped",
                        new SQLIntegrityConstraintViolationException(
                                "ORA-00001: unique constraint (SCHEMA.SYS_C0012345) violated"));
        assertEquals(
                StockLevelWriter.ConstraintViolation.NONE,
                StockLevelWriter.classifyConstraintViolation(wrapped));
    }

    @Test
    void classifier_finds_constraint_name_anywhere_in_cause_chain() {
        Exception deep =
                new RuntimeException(
                        "outer",
                        new RuntimeException(
                                "middle",
                                new SQLIntegrityConstraintViolationException(
                                        "ORA-00001: unique constraint (SCHEMA.UQ_WRITEBACK_KEY_VERSION) violated")));
        assertEquals(
                StockLevelWriter.ConstraintViolation.VERSION_CONFLICT,
                StockLevelWriter.classifyConstraintViolation(deep));
    }

    @Test
    void classifier_identifies_level_row_race_by_table_name() {
        // Finding 2: an insert race on PN_INVENTORY_LEVEL's own (system-named) PK, hit before
        // either writer reaches the ledger insert. Identified by exclusion (table name present,
        // neither ledger constraint named).
        Exception e =
                new SQLIntegrityConstraintViolationException(
                        "ORA-00001: unique constraint (SCHEMA.SYS_C0099999) violated: PN_INVENTORY_LEVEL");
        assertEquals(
                StockLevelWriter.ConstraintViolation.LEVEL_ROW_RACE,
                StockLevelWriter.classifyConstraintViolation(e));
    }

    @Test
    void classifier_does_not_confuse_level_audit_table_with_level_row_race() {
        // The _AUDIT table's own PK violation must NOT be swept into LEVEL_ROW_RACE — it gets its
        // own classification instead (D17: AUDIT_PK_COLLISION, a self-healing retry rather than
        // slice-1's plain ERROR fail-safe).
        Exception e =
                new SQLIntegrityConstraintViolationException(
                        "ORA-00001: unique constraint (SCHEMA.SYS_C0011111) violated: PN_INVENTORY_LEVEL_AUDIT");
        assertEquals(
                StockLevelWriter.ConstraintViolation.AUDIT_PK_COLLISION,
                StockLevelWriter.classifyConstraintViolation(e));
    }

    // ---- validation ----

    @Test
    void unknown_pn_rejected() {
        seedLocation("JFK1", "Y", "N");

        var cmd = command("NOPE", "JFK1", levels("10", "20", "5", "50", null, null, null), "run-1", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void inactive_pn_rejected() {
        seedPn("PN-INACTIVE", "SLW-ROTABLE", "INACTIVE");
        seedLocation("JFK2", "Y", "N");

        var cmd = command("PN-INACTIVE", "JFK2", levels("10", "20", "5", "50", null, null, null), "run-2", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
        assertTrue(
                result.message() != null && result.message().contains("INACTIVE"),
                "rejection message must carry the PN's actual status, got: " + result.message());
    }

    @Test
    void unknown_location_rejected() {
        seedPn("SLW-PN-B", "SLW-ROTABLE", "ACTIVE");

        var cmd = command("SLW-PN-B", "NOWHERE", levels("10", "20", "5", "50", null, null, null), "run-3", 1L);
        var result = writer.writeItemDedup(cmd);

        // An absent location makes the whole PN x Location key unknown — parallel to unknown PN.
        assertEquals(ResultStatus.REJECTED_UNKNOWN_KEY, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void quarantine_location_rejected() {
        seedPn("PN-C", "SLW-ROTABLE", "ACTIVE");
        seedLocation("SLW-QUAR", "Y", "Y");

        var cmd = command("PN-C", "SLW-QUAR", levels("10", "20", "5", "50", null, null, null), "run-4", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void negative_numeric_rejected() {
        seedPn("PN-D", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK3", "Y", "N");

        var cmd = command("PN-D", "JFK3", levels("-1", "20", "5", "50", null, null, null), "run-5", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void stock_min_greater_than_max_rejected() {
        seedPn("PN-E", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK4", "Y", "N");

        var cmd = command("PN-E", "JFK4", levels("10", "20", "60", "50", null, null, null), "run-6", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
    }

    @Test
    void order_min_greater_than_max_rejected() {
        seedPn("PN-F", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK5", "Y", "N");

        var cmd = command("PN-F", "JFK5", levels("10", "20", "5", "50", "40", "30", null), "run-6b", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.REJECTED_VALIDATION, result.status());
        assertEquals(400, result.code());
    }

    // ---- numeric policy on write ----

    @Test
    void consumable_keeps_decimals_on_write() {
        seedPn("PN-CONS", "SLW-CONSUMABLE", "ACTIVE");
        seedLocation("JFK-CONS", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-CONSUMABLE", "C");

        var cmd = command("PN-CONS", "JFK-CONS", levels("10.7", "20.3", "5", "50", null, null, null), "run-7", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        BigDecimal rop = readColumn("PN-CONS", "JFK-CONS", "REORDER_LEVEL");
        assertEquals(0, new BigDecimal("10.7").compareTo(rop));
    }

    @Test
    void non_consumable_truncates_on_write() {
        seedPn("PN-ROT", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-ROT", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-ROT", "JFK-ROT", levels("10.7", "20.3", "5", "50", null, null, null), "run-8", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        BigDecimal rop = readColumn("PN-ROT", "JFK-ROT", "REORDER_LEVEL");
        assertEquals(0, new BigDecimal("10").compareTo(rop));
    }

    // ---- insert / update / ledger chaining ----

    @Test
    void insert_creates_row_and_audit_and_ledger_v1() {
        seedPn("PN-NEW", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-NEW", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-NEW", "JFK-NEW", levels("10", "20", "5", "50", null, null, null), "run-9", 7L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        assertEquals(200, result.code());
        assertEquals(Long.valueOf(7), result.rowId(), "rowId must echo the request's provenance rowId");
        assertNull(result.oldValues());
        assertEquals(Integer.valueOf(10), result.newValues().get("rop"));
        assertEquals(Integer.valueOf(20), result.newValues().get("eoq"));
        assertEquals(Integer.valueOf(5), result.newValues().get("safety_stock"));
        assertEquals(Integer.valueOf(50), result.newValues().get("max_stock"));
        assertEquals(Long.valueOf(1), result.ledgerVersion());

        BigDecimal rop = readColumn("PN-NEW", "JFK-NEW", "REORDER_LEVEL");
        assertEquals(0, new BigDecimal("10").compareTo(rop), "eMRO row must carry the written ROP");

        long auditCount = countAuditRows("PN-NEW", "JFK-NEW");
        assertEquals(1, auditCount);

        var ledger =
                writer.findByIdempotencyKey(cmd.provenance().tenantId(), cmd.provenance().idempotencyKey())
                        .orElseThrow();
        assertEquals(1L, ledger.getVersion());
        assertNull(ledger.getParentVersion());
        assertEquals("WRITTEN", ledger.getOutcome());
    }

    @Test
    void ledger_row_carries_domain_and_message() {
        seedPn("PN-DOMSG", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-DOMSG", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-DOMSG", "JFK-DOMSG", levels("10", "20", "5", "50", null, null, null), "run-domsg", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        assertEquals("accepted", result.message(), "a successful write's ItemResult carries a human message");

        var ledger =
                writer.findByIdempotencyKey(cmd.provenance().tenantId(), cmd.provenance().idempotencyKey())
                        .orElseThrow();
        assertEquals(
                StockLevelWriter.DOMAIN_STOCK_LEVEL,
                ledger.getDomain(),
                "ledger row must be tagged with the STOCK_LEVEL domain");
        assertEquals(
                result.message(),
                ledger.getMessage(),
                "ledger MESSAGE must equal the same human message the ItemResult carries");
    }

    @Test
    void update_captures_old_values_and_chains_v2() {
        seedPn("PN-UPD", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-UPD", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd1 = command("PN-UPD", "JFK-UPD", levels("10", "20", "5", "50", null, null, null), "run-10", 1L);
        var r1 = writer.writeItemDedup(cmd1);
        assertEquals(ResultStatus.ACCEPTED, r1.status());

        var cmd2 = command("PN-UPD", "JFK-UPD", levels("15", "25", "6", "55", null, null, null), "run-10", 2L);
        var r2 = writer.writeItemDedup(cmd2);
        assertEquals(ResultStatus.ACCEPTED, r2.status());

        assertEquals(Integer.valueOf(10), r2.oldValues().get("rop"));
        assertEquals(Integer.valueOf(20), r2.oldValues().get("eoq"));
        assertEquals(Integer.valueOf(5), r2.oldValues().get("safety_stock"));
        assertEquals(Integer.valueOf(50), r2.oldValues().get("max_stock"));
        assertEquals(Integer.valueOf(15), r2.newValues().get("rop"));
        assertEquals(Long.valueOf(2), r2.ledgerVersion());

        var ledger2 =
                writer.findByIdempotencyKey(cmd2.provenance().tenantId(), cmd2.provenance().idempotencyKey())
                        .orElseThrow();
        assertEquals(2L, ledger2.getVersion());
        assertEquals(1L, ledger2.getParentVersion());
    }

    @Test
    void null_fields_leave_existing_columns_untouched() {
        seedPn("PN-PARTIAL", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-PARTIAL", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd1 = command("PN-PARTIAL", "JFK-PARTIAL", levels("10", "20", "5", "50", null, null, null), "run-11", 1L);
        writer.writeItemDedup(cmd1);

        var onlyRop = new LevelValues(new BigDecimal("99"), null, null, null, null, null, null);
        var cmd2 = new WritebackCommand(
                "PN-PARTIAL",
                "JFK-PARTIAL",
                onlyRop,
                new Provenance("acme", "optimizer", "run-11", 2L, null, null, null, null, "planner"),
                false);
        var r2 = writer.writeItemDedup(cmd2);

        assertEquals(ResultStatus.ACCEPTED, r2.status());
        assertEquals(Integer.valueOf(99), r2.newValues().get("rop"));
        assertEquals(Integer.valueOf(20), r2.newValues().get("eoq"));

        BigDecimal eoq = readColumn("PN-PARTIAL", "JFK-PARTIAL", "EOQ_LEVEL");
        assertEquals(0, new BigDecimal("20").compareTo(eoq));
    }

    @Test
    void replenishment_lead_time_written_when_supplied() {
        seedPn("PN-LT", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-LT", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-LT", "JFK-LT", levels("10", "20", "5", "50", null, null, "14"), "run-12", 1L);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.ACCEPTED, result.status());
        BigDecimal lt = readColumn("PN-LT", "JFK-LT", "REPLENISHMENT_LEAD_TIME");
        assertEquals(0, new BigDecimal("14").compareTo(lt));
    }

    // ---- duplicates / shadow ----

    @Test
    void duplicate_key_returns_skipped_duplicate_with_original_values() {
        seedPn("PN-DUP", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-DUP", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-DUP", "JFK-DUP", levels("10", "20", "5", "50", null, null, null), "run-13", 1L);
        var first = writer.writeItemDedup(cmd);
        assertEquals(ResultStatus.ACCEPTED, first.status());

        // Same idempotency key (runId + rowId) but a DIFFERENT payload: the original write wins.
        var replay = command("PN-DUP", "JFK-DUP", levels("999", "20", "5", "50", null, null, null), "run-13", 1L);
        var second = writer.writeItemDedup(replay);
        assertEquals(ResultStatus.SKIPPED_DUPLICATE, second.status());
        assertEquals(200, second.code());
        assertEquals(first.newValues(), second.newValues(), "replay must carry the ORIGINAL written values");
        assertEquals(first.ledgerVersion(), second.ledgerVersion());

        BigDecimal rop = readColumn("PN-DUP", "JFK-DUP", "REORDER_LEVEL");
        assertEquals(0, new BigDecimal("10").compareTo(rop), "replayed payload must not overwrite the original write");
        assertEquals(1, writer.history("acme", "PN-DUP", "JFK-DUP").size(), "exactly one ledger row for the key");
    }

    @Test
    void same_idempotency_key_different_tenants_both_apply() {
        // Finding 1: WRITEBACK_LEDGER uniqueness is (TENANT_ID, IDEMPOTENCY_KEY), so two different
        // tenants using the exact same explicit idempotency key must NOT collide — each must apply
        // independently, and each tenant's history must see only its own row (no cross-tenant leak
        // of old/new values).
        seedPn("PN-XTENANT", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-XTENANT", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        String sharedKey = "shared-idempotency-key-xtenant";

        var cmdAcme = new WritebackCommand(
                "PN-XTENANT",
                "JFK-XTENANT",
                levels("10", "20", "5", "50", null, null, null),
                new Provenance("acme", "optimizer", null, null, null, sharedKey, null, null, "planner"),
                false);
        var cmdBeta = new WritebackCommand(
                "PN-XTENANT",
                "JFK-XTENANT",
                levels("33", "44", "6", "60", null, null, null),
                new Provenance("beta", "optimizer", null, null, null, sharedKey, null, null, "planner"),
                false);

        var resultAcme = writer.writeItemDedup(cmdAcme);
        var resultBeta = writer.writeItemDedup(cmdBeta);

        assertEquals(ResultStatus.ACCEPTED, resultAcme.status(), "acme's write must apply, not collide");
        assertEquals(ResultStatus.ACCEPTED, resultBeta.status(), "beta's write must ALSO apply, not be skipped");
        assertEquals(Integer.valueOf(10), resultAcme.newValues().get("rop"));
        assertEquals(Integer.valueOf(33), resultBeta.newValues().get("rop"));

        var ledgerAcme = writer.findByIdempotencyKey("acme", sharedKey).orElseThrow();
        var ledgerBeta = writer.findByIdempotencyKey("beta", sharedKey).orElseThrow();
        assertEquals("acme", ledgerAcme.getTenantId());
        assertEquals("beta", ledgerBeta.getTenantId());
        assertEquals(sharedKey, ledgerAcme.getIdempotencyKey());
        assertEquals(sharedKey, ledgerBeta.getIdempotencyKey());

        List<WritebackLedger> acmeHistory = writer.history("acme", "PN-XTENANT", "JFK-XTENANT");
        List<WritebackLedger> betaHistory = writer.history("beta", "PN-XTENANT", "JFK-XTENANT");
        assertEquals(1, acmeHistory.size(), "acme sees only its own ledger row");
        assertEquals(1, betaHistory.size(), "beta sees only its own ledger row");
        assertEquals("acme", acmeHistory.get(0).getTenantId());
        assertEquals("beta", betaHistory.get(0).getTenantId());
    }

    @Test
    void shadow_write_ledgers_but_does_not_touch_emro_row() {
        seedPn("PN-SHADOW", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-SHADOW", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = new WritebackCommand(
                "PN-SHADOW",
                "JFK-SHADOW",
                levels("10", "20", "5", "50", null, null, null),
                new Provenance("acme", "optimizer", "run-14", 1L, null, null, null, null, "planner"),
                true);
        var result = writer.writeItemDedup(cmd);

        assertEquals(ResultStatus.SHADOWED, result.status());
        assertEquals(200, result.code());

        boolean rowExists = levelRowExists("PN-SHADOW", "JFK-SHADOW");
        assertFalse(rowExists, "shadow mode must not write the eMRO PN_INVENTORY_LEVEL row");
        assertEquals(0, countAuditRows("PN-SHADOW", "JFK-SHADOW"), "shadow mode must not write an audit row");

        var ledger =
                writer.findByIdempotencyKey(cmd.provenance().tenantId(), cmd.provenance().idempotencyKey())
                        .orElseThrow();
        assertEquals("SHADOWED", ledger.getOutcome());
        assertEquals(1L, ledger.getVersion());
    }

    @Test
    void shadow_update_leaves_existing_emro_row_untouched() {
        seedPn("PN-SHUPD", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-SHUPD", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var real = command("PN-SHUPD", "JFK-SHUPD", levels("10", "20", "5", "50", null, null, null), "run-14b", 1L);
        assertEquals(ResultStatus.ACCEPTED, writer.writeItemDedup(real).status());

        var shadow = new WritebackCommand(
                "PN-SHUPD",
                "JFK-SHUPD",
                levels("77", "88", "6", "60", null, null, null),
                new Provenance("acme", "optimizer", "run-14b", 2L, null, null, null, null, "planner"),
                true);
        var result = writer.writeItemDedup(shadow);
        assertEquals(ResultStatus.SHADOWED, result.status());
        assertEquals(Integer.valueOf(77), result.newValues().get("rop"), "shadow result reports the would-be values");

        BigDecimal rop = readColumn("PN-SHUPD", "JFK-SHUPD", "REORDER_LEVEL");
        assertEquals(0, new BigDecimal("10").compareTo(rop),
                "shadow mode must not flush dirty state onto an EXISTING eMRO row");
        assertEquals(1, countAuditRows("PN-SHUPD", "JFK-SHUPD"), "no additional audit row from a shadow write");
    }

    @Test
    void existing_company_preserved_on_update() {
        seedPn("PN-CO", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-CO", "Y", "N");
        // Pre-existing eMRO row owned by another company code (not this service's default).
        QuarkusTransaction.requiringNew()
                .run(() -> em
                        .createNativeQuery(
                                "INSERT INTO PN_INVENTORY_LEVEL (PN, LOCATION, COMPANY, REORDER_LEVEL) VALUES (?1, ?2, ?3, ?4)")
                        .setParameter(1, "PN-CO")
                        .setParameter(2, "JFK-CO")
                        .setParameter(3, "OTHERCO")
                        .setParameter(4, new BigDecimal("5"))
                        .executeUpdate());

        var result = writer.writeItemDedup(
                command("PN-CO", "JFK-CO", levels("11", null, null, null, null, null, null), "run-co", 1L));
        assertEquals(ResultStatus.ACCEPTED, result.status());
        assertEquals(Integer.valueOf(5), result.oldValues().get("rop"), "old values come from the pre-existing row");

        String company = QuarkusTransaction.requiringNew()
                .call(() -> (String) em
                        .createNativeQuery("SELECT COMPANY FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                        .setParameter(1, "PN-CO")
                        .setParameter(2, "JFK-CO")
                        .getSingleResult());
        assertEquals("OTHERCO", company, "an update must not overwrite the existing row's COMPANY");
    }

    // ---- concurrency ----

    @Test
    void concurrent_same_key_yields_one_written_one_skipped() throws Exception {
        seedPn("PN-RACE", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-RACE", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        var cmd = command("PN-RACE", "JFK-RACE", levels("10", "20", "5", "50", null, null, null), "run-15", 1L);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);

        try {
            Future<ItemResult> f1 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return writer.writeItemDedup(cmd);
            });
            Future<ItemResult> f2 = pool.submit(() -> {
                ready.countDown();
                go.await();
                return writer.writeItemDedup(cmd);
            });

            ready.await();
            go.countDown();

            ItemResult r1 = f1.get(30, TimeUnit.SECONDS);
            ItemResult r2 = f2.get(30, TimeUnit.SECONDS);

            List<ItemResult> results = List.of(r1, r2);
            long acceptedCount = results.stream().filter(r -> r.status() == ResultStatus.ACCEPTED).count();
            long skippedCount = results.stream().filter(r -> r.status() == ResultStatus.SKIPPED_DUPLICATE).count();

            assertEquals(1, acceptedCount, "exactly one thread should win ACCEPTED");
            assertEquals(1, skippedCount, "exactly one thread should be SKIPPED_DUPLICATE");

            List<WritebackLedger> history = writer.history("acme", "PN-RACE", "JFK-RACE");
            assertEquals(1, history.size(), "exactly one ledger row for the idempotency key");
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void concurrent_different_keys_same_part_get_distinct_versions() throws Exception {
        seedPn("PN-VCHAIN", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-VCHAIN", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        // A pre-existing row means BOTH concurrent writers are updates racing on the SAME
        // (pn, location) — the scenario Finding 2 is about: without the pessimistic lock, a
        // version-N+1 writer could ledger oldValues from a stale pre-commit snapshot instead of
        // the just-committed version-N writer's values, corrupting the history chain.
        var seed =
                command(
                        "PN-VCHAIN", "JFK-VCHAIN", levels("1", "2", "1", "9", null, null, null), "run-vchain-seed", 1L);
        var seedResult = writer.writeItemDedup(seed);
        assertEquals(ResultStatus.ACCEPTED, seedResult.status());

        // Two DIFFERENT idempotency keys (different runId), SAME (tenant, pn, location): both are
        // otherwise-valid writes racing to compute 1 + max(version) in parallel REQUIRES_NEW txs.
        var cmdA =
                command(
                        "PN-VCHAIN", "JFK-VCHAIN", levels("10", "20", "5", "50", null, null, null), "run-vchain-a", 1L);
        var cmdB =
                command(
                        "PN-VCHAIN", "JFK-VCHAIN", levels("11", "21", "6", "51", null, null, null), "run-vchain-b", 1L);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);

        try {
            Future<ItemResult> fA =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                return writer.writeItemDedup(cmdA);
                            });
            Future<ItemResult> fB =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                return writer.writeItemDedup(cmdB);
                            });

            ready.await();
            go.countDown();

            ItemResult rA = fA.get(30, TimeUnit.SECONDS);
            ItemResult rB = fB.get(30, TimeUnit.SECONDS);

            assertEquals(ResultStatus.ACCEPTED, rA.status(), "thread A must be accepted (after retry if needed)");
            assertEquals(ResultStatus.ACCEPTED, rB.status(), "thread B must be accepted (after retry if needed)");

            List<WritebackLedger> history = writer.history("acme", "PN-VCHAIN", "JFK-VCHAIN");
            assertEquals(3, history.size(), "seed ledger row plus one per distinct racing idempotency key");

            WritebackLedger v1 = history.stream().filter(l -> l.getVersion() == 1L).findFirst().orElseThrow();
            WritebackLedger v2 = history.stream().filter(l -> l.getVersion() == 2L).findFirst().orElseThrow();
            WritebackLedger v3 = history.stream().filter(l -> l.getVersion() == 3L).findFirst().orElseThrow();
            assertNull(v1.getParentVersion(), "version 1's parent must be null");
            assertEquals(1L, v2.getParentVersion(), "version 2's parent must chain to version 1");
            assertEquals(2L, v3.getParentVersion(), "version 3's parent must chain to version 2");

            // Chain integrity (Finding 2): each version's old_values must equal the PREVIOUS
            // version's new_values, proving the pessimistic lock serialized the two racing
            // updates rather than letting one ledger a stale pre-commit snapshot.
            assertEquals(
                    v1.getNewValuesJson(),
                    v2.getOldValuesJson(),
                    "version 2's old_values must equal version 1's new_values — the exact corruption Finding 2 fixes");
            assertEquals(
                    v2.getNewValuesJson(),
                    v3.getOldValuesJson(),
                    "version 3's old_values must equal version 2's new_values — the exact corruption Finding 2 fixes");
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void concurrent_different_keys_on_absent_row_both_succeed() throws Exception {
        seedPn("PN-VCHAIN-NEW", "SLW-ROTABLE", "ACTIVE");
        seedLocation("JFK-VCHAIN-NEW", "Y", "N");
        seedTranCode("PNCATEGORY", "SLW-ROTABLE", "R");

        // NO PN_INVENTORY_LEVEL row pre-exists: two concurrent writers for the SAME (pn, location)
        // both attempt to INSERT it. One wins the insert; the other either blocks on the
        // pessimistic lock (acquired the moment either transaction's find() runs) and then
        // proceeds as an update, or — in the brief pre-lock window — collides on the level row's
        // own PK and is retried via the LEVEL_ROW_RACE classification (Finding 2).
        var cmdA =
                command(
                        "PN-VCHAIN-NEW",
                        "JFK-VCHAIN-NEW",
                        levels("10", "20", "5", "50", null, null, null),
                        "run-vchain-new-a",
                        1L);
        var cmdB =
                command(
                        "PN-VCHAIN-NEW",
                        "JFK-VCHAIN-NEW",
                        levels("11", "21", "6", "51", null, null, null),
                        "run-vchain-new-b",
                        1L);

        ExecutorService pool = Executors.newFixedThreadPool(2);
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch go = new CountDownLatch(1);

        try {
            Future<ItemResult> fA =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                return writer.writeItemDedup(cmdA);
                            });
            Future<ItemResult> fB =
                    pool.submit(
                            () -> {
                                ready.countDown();
                                go.await();
                                return writer.writeItemDedup(cmdB);
                            });

            ready.await();
            go.countDown();

            ItemResult rA = fA.get(30, TimeUnit.SECONDS);
            ItemResult rB = fB.get(30, TimeUnit.SECONDS);

            assertEquals(ResultStatus.ACCEPTED, rA.status(), "thread A must be accepted (after retry if needed)");
            assertEquals(ResultStatus.ACCEPTED, rB.status(), "thread B must be accepted (after retry if needed)");

            List<WritebackLedger> history = writer.history("acme", "PN-VCHAIN-NEW", "JFK-VCHAIN-NEW");
            assertEquals(2, history.size(), "exactly two ledger rows, one per distinct idempotency key");

            Set<Long> versions = new HashSet<>();
            for (WritebackLedger entry : history) {
                versions.add(entry.getVersion());
            }
            assertEquals(Set.of(1L, 2L), versions, "versions must be exactly {1,2} with no duplicate/gap");

            WritebackLedger v1 = history.stream().filter(l -> l.getVersion() == 1L).findFirst().orElseThrow();
            WritebackLedger v2 = history.stream().filter(l -> l.getVersion() == 2L).findFirst().orElseThrow();
            assertNull(v1.getParentVersion(), "version 1's parent must be null");
            assertEquals(1L, v2.getParentVersion(), "version 2's parent must chain to version 1");
            assertNull(v1.getOldValuesJson(), "the creating write has no old_values (row was absent)");
            assertEquals(
                    v1.getNewValuesJson(),
                    v2.getOldValuesJson(),
                    "the updating write's old_values must equal the creating write's new_values");
        } finally {
            pool.shutdownNow();
        }
    }

    // ---- helpers ----

    private WritebackCommand command(
            String pn, String location, LevelValues levels, String runId, Long rowId) {
        return new WritebackCommand(
                pn,
                location,
                levels,
                new Provenance("acme", "optimizer", runId, rowId, null, null, null, null, "planner"),
                false);
    }

    private LevelValues levels(
            String rop,
            String eoq,
            String stockMin,
            String stockMax,
            String orderMin,
            String orderMax,
            String leadTime) {
        return new LevelValues(
                bd(rop), bd(eoq), bd(stockMin), bd(stockMax), bd(orderMin), bd(orderMax), bd(leadTime));
    }

    private BigDecimal bd(String s) {
        return s == null ? null : new BigDecimal(s);
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

    /**
     * Idempotent: several tests share the same (PNCATEGORY, CONSUMABLE|ROTABLE) tran-code row, so
     * skip the insert if it already exists rather than colliding on the PK.
     */
    private void seedTranCode(String systemTransaction, String systemCode, String pnTransaction) {
        boolean exists =
                QuarkusTransaction.requiringNew()
                        .call(
                                () -> {
                                    var rows =
                                            em.createNativeQuery(
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
                .run(
                        () ->
                                em.createNativeQuery(
                                                "INSERT INTO SYSTEM_TRAN_CODE (SYSTEM_TRANSACTION, SYSTEM_CODE, SYSTEM_TRAN_CODE_SUB, PN_TRANSACTION) VALUES (?1, ?2, ?3, ?4)")
                                        .setParameter(1, systemTransaction)
                                        .setParameter(2, systemCode)
                                        .setParameter(3, "SUB")
                                        .setParameter(4, pnTransaction)
                                        .executeUpdate());
    }

    private BigDecimal readColumn(String pn, String location, String column) {
        return QuarkusTransaction.requiringNew()
                .call(
                        () ->
                                (BigDecimal)
                                        em.createNativeQuery(
                                                        "SELECT " + column
                                                                + " FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                                                .setParameter(1, pn)
                                                .setParameter(2, location)
                                                .getSingleResult());
    }

    private boolean levelRowExists(String pn, String location) {
        return QuarkusTransaction.requiringNew()
                .call(
                        () -> {
                            var list =
                                    em.createNativeQuery(
                                                    "SELECT PN FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                                            .setParameter(1, pn)
                                            .setParameter(2, location)
                                            .getResultList();
                            return !list.isEmpty();
                        });
    }

    private long countAuditRows(String pn, String location) {
        return QuarkusTransaction.requiringNew()
                .call(
                        () ->
                                ((Number)
                                                em.createNativeQuery(
                                                                "SELECT COUNT(*) FROM PN_INVENTORY_LEVEL_AUDIT WHERE PN = ?1 AND LOCATION = ?2")
                                                        .setParameter(1, pn)
                                                        .setParameter(2, location)
                                                        .getSingleResult())
                                        .longValue());
    }
}
