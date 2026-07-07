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
import org.junit.jupiter.api.Test;

/**
 * Wire-contract tests for the transfers batch REST facade ({@code POST /api/v1/transfers}).
 * Mirrors {@link RequisitionResourceTest}'s exact test infrastructure (RestAssured, {@code
 * TestSecurity}/{@code JwtSecurity}, native-query DB seeding/assertions).
 */
@QuarkusTest
class TransferResourceTest {

    @Inject EntityManager em;

    private static final String ENDPOINT = "/api/v1/transfers";

    @Test
    void no_token_401() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-trff-1","transactionId":"tx-trff-1","items":[]}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(401);
    }

    @Test
    @TestSecurity(user = "x", roles = {"writeback:read"})
    void wrong_role_403() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-trff-2","transactionId":"tx-trff-2","items":[]}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(403);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void mixed_batch_per_row() {
        seedPn("TRFF-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRFF-FROM-1", "Y", "N");
        seedLocation("TRFF-TO-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-trff-3",
                  "transactionId": "tx-trff-3",
                  "items": [
                    {"rowId": 1, "partNo": "TRFF-PN-1", "fromLocation": "TRFF-FROM-1", "toLocation": "TRFF-TO-1", "qty": 5, "batch": 1001, "deliveryDate": "2026-08-01"},
                    {"rowId": 2, "partNo": "TRFF-PN-UNKNOWN", "fromLocation": "TRFF-FROM-1", "toLocation": "TRFF-TO-1", "qty": 5}
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
                .body("runId", is("run-trff-3"))
                .body("transactionId", is("tx-trff-3"))
                .body("results[0].rowId", is(1))
                .body("results[0].status", is("ACCEPTED"))
                .body("results[0].orderNumber", notNullValue())
                .body("results[0].batch", is("1001"))
                .body("results[1].rowId", is(2))
                .body("results[1].status", is("REJECTED_UNKNOWN_KEY"))
                .body("results[1].orderNumber", nullValue());

        long headerCount = ((Number)
                        QuarkusTransaction.requiringNew()
                                .call(() -> em.createNativeQuery(
                                                "SELECT COUNT(*) FROM ORDER_HEADER WHERE ORDER_TYPE = 'TS' AND SHIPPED_FROM_LOCATION = ?1")
                                        .setParameter(1, "TRFF-FROM-1")
                                        .getSingleResult()))
                .longValue();
        org.junit.jupiter.api.Assertions.assertEquals(1, headerCount);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void replay_returns_original_order_number() {
        seedPn("TRFF-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRFF-FROM-2", "Y", "N");
        seedLocation("TRFF-TO-2", "Y", "N");

        String body =
                """
                {
                  "runId": "run-trff-4",
                  "transactionId": "tx-trff-4",
                  "items": [
                    {"rowId": 1, "partNo": "TRFF-PN-2", "fromLocation": "TRFF-FROM-2", "toLocation": "TRFF-TO-2", "qty": 5}
                  ]
                }
                """;

        String firstOrderNumber =
                given()
                        .contentType("application/json")
                        .body(body)
                        .when()
                        .post(ENDPOINT)
                        .then()
                        .statusCode(200)
                        .body("results[0].status", is("ACCEPTED"))
                        .body("results[0].orderNumber", notNullValue())
                        .extract()
                        .path("results[0].orderNumber");

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].status", is("SKIPPED_DUPLICATE"))
                .body("results[0].orderNumber", is(firstOrderNumber));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void error_row_message_is_sanitized() {
        seedPn("TRFF-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRFF-FROM-3", "Y", "N");
        seedLocation("TRFF-TO-3", "Y", "N");

        String body =
                """
                {
                  "runId": "run-trff-6",
                  "transactionId": "tx-trff-6",
                  "items": [
                    {"rowId": null, "partNo": "TRFF-PN-3", "fromLocation": "TRFF-FROM-3", "toLocation": "TRFF-TO-3", "qty": 5},
                    {"rowId": 2, "partNo": "TRFF-PN-3", "fromLocation": "TRFF-FROM-3", "toLocation": "TRFF-TO-3", "qty": 5}
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
                .body("results[0].rowId", nullValue())
                .body("results[0].status", is("ERROR"))
                .body("results[0].code", is(500))
                .body(
                        "results[0].message",
                        is("internal error (run=run-trff-6, row=null)"))
                .body("results[0].message", org.hamcrest.CoreMatchers.not(
                        org.hamcrest.CoreMatchers.containsString("idempotency")))
                .body("results[0].message", org.hamcrest.CoreMatchers.not(
                        org.hamcrest.CoreMatchers.containsString("IllegalState")))
                .body("results[1].rowId", is(2))
                .body("results[1].status", is("ACCEPTED"));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void null_items_200_empty() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-trff-5","transactionId":"tx-trff-5","items":null}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("runId", is("run-trff-5"))
                .body("transactionId", is("tx-trff-5"))
                .body("results.size()", is(0));
    }

    private void seedPn(String pn, String category, String status) {
        QuarkusTransaction.requiringNew()
                .run(
                        () ->
                                em.createNativeQuery(
                                                "INSERT INTO PN_MASTER (PN, CATEGORY, STATUS, STOCK_UOM) VALUES (?1, ?2, ?3, ?4)")
                                        .setParameter(1, pn)
                                        .setParameter(2, category)
                                        .setParameter(3, status)
                                        .setParameter(4, "EA")
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
