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
