package trax.io.writeback.api.batch;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.CoreMatchers.notNullValue;
import static org.hamcrest.CoreMatchers.nullValue;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.jwt.JwtSecurity;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * {@code GET /api/v1/runs/{runId}/results} (D16): a thin, ledger-backed replay of what was
 * APPLIED for a run — not the full original request (rejected rows are never ledgered).
 */
@QuarkusTest
class RunResultsResourceTest {

    @Inject EntityManager em;

    private static String endpoint(String runId) {
        return "/api/v1/runs/" + runId + "/results";
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write", "writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void results_for_run_returned_tenant_scoped() {
        seedPn("RUNR-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RUNR-LOC-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-runr-1",
                  "transactionId": "tx-runr-1",
                  "items": [
                    {"rowId": 1, "partNo": "RUNR-PN-1", "location": "RUNR-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post("/api/v1/stock-levels")
                .then()
                .statusCode(200)
                .body("results[0].status", is("ACCEPTED"));

        // Same caller tenant (acme) sees the row it just wrote.
        given()
                .when()
                .get(endpoint("run-runr-1"))
                .then()
                .statusCode(200)
                .body("size()", is(1))
                .body("[0].rowId", is(1))
                .body("[0].domain", is("STOCK_LEVEL"))
                .body("[0].pn", is("RUNR-PN-1"))
                .body("[0].location", is("RUNR-LOC-1"))
                .body("[0].status", is("WRITTEN"))
                .body("[0].version", is(1))
                .body("[0].parentVersion", nullValue())
                .body("[0].createdAt", notNullValue());
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "other-tenant")})
    void results_for_run_are_scoped_to_caller_tenant() {
        // A ledger row for the SAME runId, but belonging to a different tenant ("acme") than the
        // caller ("other-tenant") — the caller must see nothing.
        seedLedgerRow("run-runr-tenant-scope", "acme", "RUNR-PN-2", "RUNR-LOC-2");

        given()
                .when()
                .get(endpoint("run-runr-tenant-scope"))
                .then()
                .statusCode(200)
                .body("size()", is(0));
    }

    /**
     * Pins {@code TraxRepository.findLedgerRowsForRun}'s {@code order by createdAt asc, rowId
     * asc} — seeded out of display order (reverse insertion) to prove the ordering is a real DB
     * sort, not accidental insertion/persist order.
     *
     * <p>Three rows share {@code (tenant, runId)}: row A ({@code rowId=5}) has the earliest
     * {@code createdAt}; rows B ({@code rowId=null}, a batch-origin row per {@code
     * RunResultsResource}'s Javadoc on batch-origin rows) and C ({@code rowId=2}) share a later,
     * identical {@code createdAt} so the {@code rowId} tiebreaker actually gets exercised.
     * Persisted in the order C, B, A (the reverse of the expected response order) so a
     * pass here cannot be explained by insertion order alone.
     *
     * <p><b>Null-rowId placement (documented, not asserted as a guaranteed cross-DB contract):</b>
     * this service targets Oracle, whose default {@code NULLS LAST} behavior for {@code ASC}
     * ordering places row B (null {@code rowId}) after row C ({@code rowId=2}) among the
     * createdAt-tied pair. Expected order: A, C, B.
     */
    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void results_ordered_by_created_at_then_row_id() {
        Instant earlier = Instant.parse("2026-07-01T00:00:00Z");
        Instant tied = Instant.parse("2026-07-01T00:05:00Z");

        // Reverse insertion order on purpose: C, then B, then A.
        seedLedgerRow("run-runr-order", "acme", "RUNR-PN-ORD", "RUNR-LOC-ORD", 2L, tied);
        seedLedgerRow("run-runr-order", "acme", "RUNR-PN-ORD", "RUNR-LOC-ORD", null, tied);
        seedLedgerRow("run-runr-order", "acme", "RUNR-PN-ORD", "RUNR-LOC-ORD", 5L, earlier);

        given()
                .when()
                .get(endpoint("run-runr-order"))
                .then()
                .statusCode(200)
                .body("size()", is(3))
                .body("[0].rowId", is(5))
                .body("[1].rowId", is(2))
                .body("[2].rowId", nullValue());
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:read"})
    void unknown_run_returns_empty() {
        given()
                .when()
                .get(endpoint("run-runr-does-not-exist"))
                .then()
                .statusCode(200)
                .body("size()", is(0));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void read_role_required() {
        given().when().get(endpoint("run-runr-any")).then().statusCode(403);
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

    private final AtomicLong seedVersionCounter = new AtomicLong(1L);

    private void seedLedgerRow(String runId, String tenantId, String pn, String location) {
        seedLedgerRow(runId, tenantId, pn, location, 1L, Instant.now());
    }

    /**
     * Each call uses a fresh {@code version} — {@code UQ_WRITEBACK_KEY_VERSION} is unique on
     * {@code (tenant, pn, location, version)}, and {@link #results_ordered_by_created_at_then_row_id}
     * seeds three rows sharing the same {@code (tenant, pn, location)} on purpose.
     */
    private void seedLedgerRow(
            String runId, String tenantId, String pn, String location, Long rowId, Instant createdAt) {
        long version = seedVersionCounter.getAndIncrement();
        QuarkusTransaction.requiringNew()
                .run(
                        () -> {
                            WritebackLedger ledger = new WritebackLedger();
                            ledger.setIdempotencyKey(runId + ":" + (rowId == null ? "null" : rowId) + ":" + createdAt);
                            ledger.setTenantId(tenantId);
                            ledger.setRunId(runId);
                            ledger.setRowId(rowId);
                            ledger.setPn(pn);
                            ledger.setLocation(location);
                            ledger.setPrincipal("test-seed");
                            ledger.setAgentVersion("v1");
                            ledger.setOutcome("WRITTEN");
                            ledger.setDomain("STOCK_LEVEL");
                            ledger.setVersion(version);
                            ledger.setCreatedAt(createdAt);
                            em.persist(ledger);
                        });
    }
}
