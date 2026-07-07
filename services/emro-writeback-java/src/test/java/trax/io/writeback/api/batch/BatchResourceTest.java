package trax.io.writeback.api.batch;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.CoreMatchers.notNullValue;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.jwt.JwtSecurity;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;

@QuarkusTest
class BatchResourceTest {

    @Inject EntityManager em;

    private static final String ENDPOINT = "/api/v1/stock-levels";

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void null_items_returns_200_empty_results() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-null-items","transactionId":"tx-null-items","items":null}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("runId", is("run-null-items"))
                .body("transactionId", is("tx-null-items"))
                .body("results.size()", is(0));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void empty_items_returns_200_empty_results() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-empty-items","transactionId":"tx-empty-items","items":[]}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("runId", is("run-empty-items"))
                .body("transactionId", is("tx-empty-items"))
                .body("results.size()", is(0));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void tenant_claim_lands_in_ledger() {
        seedPn("BATCHFIX-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("BATCHFIX-LOC-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-batchfix-1",
                  "transactionId": "tx-batchfix-1",
                  "items": [
                    {"rowId": 1, "partNo": "BATCHFIX-PN-1", "location": "BATCHFIX-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].status", is("ACCEPTED"));

        String tenantId = ledgerTenantId("run-batchfix-1:1");
        org.junit.jupiter.api.Assertions.assertEquals("acme", tenantId);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void missing_tenant_claim_defaults() {
        seedPn("BATCHFIX-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("BATCHFIX-LOC-2", "Y", "N");

        String body =
                """
                {
                  "runId": "run-batchfix-2",
                  "transactionId": "tx-batchfix-2",
                  "items": [
                    {"rowId": 1, "partNo": "BATCHFIX-PN-2", "location": "BATCHFIX-LOC-2", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].status", is("ACCEPTED"));

        String tenantId = ledgerTenantId("run-batchfix-2:1");
        org.junit.jupiter.api.Assertions.assertEquals("default", tenantId);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void error_row_message_is_sanitized() {
        seedPn("BATCHFIX-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("BATCHFIX-LOC-3", "Y", "N");

        String body =
                """
                {
                  "runId": "run-batchfix-3",
                  "transactionId": "tx-batchfix-3",
                  "items": [
                    {"rowId": null, "partNo": "BATCHFIX-PN-3", "location": "BATCHFIX-LOC-3", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50},
                    {"rowId": 2, "partNo": "BATCHFIX-PN-3", "location": "BATCHFIX-LOC-3", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].rowId", org.hamcrest.CoreMatchers.nullValue())
                .body("results[0].status", is("ERROR"))
                .body("results[0].code", is(500))
                .body(
                        "results[0].message",
                        is("internal error (run=run-batchfix-3, row=null)"))
                .body("results[0].message", org.hamcrest.CoreMatchers.not(
                        org.hamcrest.CoreMatchers.containsString("idempotency")))
                .body("results[0].message", org.hamcrest.CoreMatchers.not(
                        org.hamcrest.CoreMatchers.containsString("IllegalState")))
                .body("results[1].rowId", is(2))
                .body("results[1].status", is("ACCEPTED"));
    }

    @Test
    void no_token_is_401() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-b1","transactionId":"tx-1","items":[]}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(401);
    }

    @Test
    @TestSecurity(user = "x", roles = {"writeback:read"})
    void wrong_role_is_403() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-b2","transactionId":"tx-2","items":[]}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(403);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void mixed_batch_reports_per_row() {
        seedPn("BATCH-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("BATCH-LOC-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-batch-1",
                  "transactionId": "tx-batch-1",
                  "items": [
                    {"rowId": 1, "partNo": "BATCH-PN-1", "location": "BATCH-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50},
                    {"rowId": 2, "partNo": "BATCH-PN-UNKNOWN", "location": "BATCH-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("runId", is("run-batch-1"))
                .body("transactionId", is("tx-batch-1"))
                .body("results[0].rowId", is(1))
                .body("results[0].status", is("ACCEPTED"))
                .body("results[1].rowId", is(2))
                .body("results[1].status", is("REJECTED_UNKNOWN_KEY"));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void replayed_batch_row_is_skipped_duplicate() {
        seedPn("BATCH-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("BATCH-LOC-2", "Y", "N");

        String body =
                """
                {
                  "runId": "run-batch-2",
                  "transactionId": "tx-batch-2",
                  "items": [
                    {"rowId": 1, "partNo": "BATCH-PN-2", "location": "BATCH-LOC-2", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        given().contentType("application/json").body(body).when().post(ENDPOINT).then()
                .statusCode(200)
                .body("results[0].status", is("ACCEPTED"));

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].status", is("SKIPPED_DUPLICATE"))
                .body("runId", notNullValue());
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

    private String ledgerTenantId(String idempotencyKey) {
        return (String)
                QuarkusTransaction.requiringNew()
                        .call(
                                () ->
                                        em.createNativeQuery(
                                                        "SELECT TENANT_ID FROM WRITEBACK_LEDGER WHERE IDEMPOTENCY_KEY = ?1")
                                                .setParameter(1, idempotencyKey)
                                                .getSingleResult());
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
