package trax.io.writeback.api.traxio;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.jwt.JwtSecurity;
import io.restassured.response.Response;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.time.Instant;
import java.util.Map;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;

/**
 * Wire-contract-exact tests for the Trax IO history facade ({@code GET /traxio/v1/history}).
 * Mirrors {@code trax_io_spine.writeback.contracts.HistoryEntry} field-for-field (snake_case, 14
 * fields) so {@code RestWritebackClient.get_history} can {@code model_validate} the response
 * array directly.
 */
@QuarkusTest
class TraxIoHistoryTest {

    @Inject EntityManager em;

    private static final String APPLY_ENDPOINT = "/traxio/v1/inventory-levels";
    private static final String HISTORY_ENDPOINT = "/traxio/v1/history";

    // Matches Instant's ISO-8601 trailing-Z form Jackson emits, e.g. 2026-04-01T12:34:56.789Z.
    private static final Pattern ISO_INSTANT_Z =
            Pattern.compile("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d+)?Z");

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void history_empty_for_unknown_key() {
        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "TRAXIO-HIST-UNKNOWN")
                .queryParam("location", "TRAXIO-HIST-UNKNOWN")
                .when()
                .get(HISTORY_ENDPOINT)
                .then()
                .statusCode(200)
                .body("size()", is(0));
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write", "writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void two_writes_chain_versions_and_old_values() {
        seedPn("TRAXIO-HIST-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-HIST-LOC-1", "Y", "N");

        String v1Body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-HIST-1","location":"TRAXIO-HIST-LOC-1","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-HIST-1:TRAXIO-HIST-LOC-1:v1","tier":null,"shadow":false}
                """;
        given().contentType("application/json").body(v1Body).when().post(APPLY_ENDPOINT).then().statusCode(200);

        String v2Body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-HIST-1","location":"TRAXIO-HIST-LOC-1","rop":9,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-2","idempotency_key":"2026-04-01:acme:TRAXIO-HIST-1:TRAXIO-HIST-LOC-1:v2","tier":null,"shadow":false}
                """;
        given().contentType("application/json").body(v2Body).when().post(APPLY_ENDPOINT).then().statusCode(200);

        Response resp =
                given()
                        .queryParam("tenant_id", "acme")
                        .queryParam("pn", "TRAXIO-HIST-1")
                        .queryParam("location", "TRAXIO-HIST-LOC-1")
                        .when()
                        .get(HISTORY_ENDPOINT);

        resp.then()
                .statusCode(200)
                .body("size()", is(2))
                .body("[0].tenant_id", is("acme"))
                .body("[0].pn", is("TRAXIO-HIST-1"))
                .body("[0].location", is("TRAXIO-HIST-LOC-1"))
                .body("[0].version", is(1))
                .body("[0].status", is("written"))
                .body("[0].old_values", org.hamcrest.CoreMatchers.nullValue())
                .body("[0].new_values.rop", is(5))
                .body("[0].parent_version", org.hamcrest.CoreMatchers.nullValue())
                .body("[1].version", is(2))
                .body("[1].status", is("written"))
                .body("[1].new_values.rop", is(9))
                .body("[1].parent_version", is(1));

        Map<String, Object> newValues0 = resp.jsonPath().getMap("[0].new_values");
        Map<String, Object> oldValues1 = resp.jsonPath().getMap("[1].old_values");
        Assertions.assertEquals(newValues0, oldValues1, "[1].old_values must equal [0].new_values");

        // Load-bearing datetime check: changed_at must parse as a UTC Instant (read by pydantic).
        String changedAt0 = resp.jsonPath().getString("[0].changed_at");
        String changedAt1 = resp.jsonPath().getString("[1].changed_at");
        Assertions.assertTrue(
                ISO_INSTANT_Z.matcher(changedAt0).matches(),
                "changed_at must match ISO-8601 UTC form: " + changedAt0);
        Assertions.assertDoesNotThrow(() -> Instant.parse(changedAt0));
        Assertions.assertTrue(
                ISO_INSTANT_Z.matcher(changedAt1).matches(),
                "changed_at must match ISO-8601 UTC form: " + changedAt1);
        Assertions.assertDoesNotThrow(() -> Instant.parse(changedAt1));
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write", "writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void shadow_entries_appear_with_status_shadowed() {
        seedPn("TRAXIO-HIST-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-HIST-LOC-2", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-HIST-2","location":"TRAXIO-HIST-LOC-2","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-HIST-2:TRAXIO-HIST-LOC-2","tier":"bounded","shadow":true}
                """;
        given().contentType("application/json").body(body).when().post(APPLY_ENDPOINT).then().statusCode(200);

        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "TRAXIO-HIST-2")
                .queryParam("location", "TRAXIO-HIST-LOC-2")
                .when()
                .get(HISTORY_ENDPOINT)
                .then()
                .statusCode(200)
                .body("size()", is(1))
                .body("[0].status", is("shadowed"))
                .body("[0].tier", is(2))
                .body("[0].provenance_id", is(""))
                .body("[0].agent_version", is("emro-writeback-java/1.0"))
                .body("[0].changed_by_principal", is("agent-spine"))
                .body("[0].idempotency_key", is("2026-04-01:acme:TRAXIO-HIST-2:TRAXIO-HIST-LOC-2"));
    }

    @Test
    @TestSecurity(user = "x", roles = {"writeback:write"})
    void read_role_required() {
        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "X")
                .queryParam("location", "Y")
                .when()
                .get(HISTORY_ENDPOINT)
                .then()
                .statusCode(403);
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
