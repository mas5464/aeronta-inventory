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

    private void seedLedgerRow(String runId, String tenantId, String pn, String location) {
        QuarkusTransaction.requiringNew()
                .run(
                        () -> {
                            WritebackLedger ledger = new WritebackLedger();
                            ledger.setIdempotencyKey(runId + ":1");
                            ledger.setTenantId(tenantId);
                            ledger.setRunId(runId);
                            ledger.setRowId(1L);
                            ledger.setPn(pn);
                            ledger.setLocation(location);
                            ledger.setPrincipal("test-seed");
                            ledger.setAgentVersion("v1");
                            ledger.setOutcome("WRITTEN");
                            ledger.setDomain("STOCK_LEVEL");
                            ledger.setVersion(1L);
                            ledger.setCreatedAt(Instant.now());
                            em.persist(ledger);
                        });
    }
}
