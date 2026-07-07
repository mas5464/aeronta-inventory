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
 * Wire-contract tests for the requisitions batch REST facade ({@code POST /api/v1/requisitions}).
 * Mirrors {@link BatchResourceTest}'s exact test infrastructure (RestAssured, {@code
 * TestSecurity}/{@code JwtSecurity}, native-query DB seeding/assertions).
 */
@QuarkusTest
class RequisitionResourceTest {

    @Inject EntityManager em;

    private static final String ENDPOINT = "/api/v1/requisitions";

    @Test
    void no_token_401() {
        given()
                .contentType("application/json")
                .body("""
                        {"runId":"run-reqf-1","transactionId":"tx-reqf-1","items":[]}
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
                        {"runId":"run-reqf-2","transactionId":"tx-reqf-2","items":[]}
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
        seedPn("REQF-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("REQF-LOC-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-reqf-3",
                  "transactionId": "tx-reqf-3",
                  "items": [
                    {"rowId": 1, "partNo": "REQF-PN-1", "location": "REQF-LOC-1", "qty": 5, "needBy": "2026-08-01"},
                    {"rowId": 2, "partNo": "REQF-PN-UNKNOWN", "location": "REQF-LOC-1", "qty": 5}
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
                .body("runId", is("run-reqf-3"))
                .body("transactionId", is("tx-reqf-3"))
                .body("results[0].rowId", is(1))
                .body("results[0].status", is("ACCEPTED"))
                .body("results[0].requisition", notNullValue())
                .body("results[1].rowId", is(2))
                .body("results[1].status", is("REJECTED_UNKNOWN_KEY"))
                .body("results[1].requisition", nullValue());

        long headerCount = ((Number)
                        QuarkusTransaction.requiringNew()
                                .call(() -> em.createNativeQuery(
                                                "SELECT COUNT(*) FROM REQUISITION_HEADER WHERE REQUESTER_LOCATION = ?1")
                                        .setParameter(1, "REQF-LOC-1")
                                        .getSingleResult()))
                .longValue();
        org.junit.jupiter.api.Assertions.assertEquals(1, headerCount);
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void replay_returns_original_requisition() {
        seedPn("REQF-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("REQF-LOC-2", "Y", "N");

        String body =
                """
                {
                  "runId": "run-reqf-4",
                  "transactionId": "tx-reqf-4",
                  "items": [
                    {"rowId": 1, "partNo": "REQF-PN-2", "location": "REQF-LOC-2", "qty": 5}
                  ]
                }
                """;

        String firstRequisition =
                given()
                        .contentType("application/json")
                        .body(body)
                        .when()
                        .post(ENDPOINT)
                        .then()
                        .statusCode(200)
                        .body("results[0].status", is("ACCEPTED"))
                        .body("results[0].requisition", notNullValue())
                        .extract()
                        .path("results[0].requisition");

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("results[0].status", is("SKIPPED_DUPLICATE"))
                .body("results[0].requisition", is(firstRequisition));
    }

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    void error_row_message_is_sanitized() {
        seedPn("REQF-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("REQF-LOC-3", "Y", "N");

        String body =
                """
                {
                  "runId": "run-reqf-6",
                  "transactionId": "tx-reqf-6",
                  "items": [
                    {"rowId": null, "partNo": "REQF-PN-3", "location": "REQF-LOC-3", "qty": 5},
                    {"rowId": 2, "partNo": "REQF-PN-3", "location": "REQF-LOC-3", "qty": 5}
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
                        is("internal error (run=run-reqf-6, row=null)"))
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
                        {"runId":"run-reqf-5","transactionId":"tx-reqf-5","items":null}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("runId", is("run-reqf-5"))
                .body("transactionId", is("tx-reqf-5"))
                .body("results.size()", is(0));
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
