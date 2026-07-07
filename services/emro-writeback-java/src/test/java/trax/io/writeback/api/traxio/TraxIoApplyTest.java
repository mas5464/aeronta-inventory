package trax.io.writeback.api.traxio;

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
import jakarta.persistence.NoResultException;
import org.junit.jupiter.api.Test;

/**
 * Wire-contract-exact tests for the Trax IO apply facade ({@code POST /traxio/v1/inventory-levels}).
 * Bodies are raw JSON strings copied verbatim from the Task 8 brief so a naming-strategy
 * regression (e.g. accidentally inheriting the batch facade's camelCase) fails loudly via
 * JsonPath key-presence assertions.
 */
@QuarkusTest
class TraxIoApplyTest {

    @Inject EntityManager em;

    private static final String ENDPOINT = "/traxio/v1/inventory-levels";

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void apply_writes_and_returns_written_with_values() {
        seedPn("TRAXIO-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-1", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-1","location":"TRAXIO-LOC-1","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-1:TRAXIO-LOC-1","tier":null,"shadow":false}
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("tenant_id", is("acme"))
                .body("pn", is("TRAXIO-PN-1"))
                .body("location", is("TRAXIO-LOC-1"))
                .body("status", is("written"))
                .body("old_values", nullValue())
                .body("new_values.rop", is(5))
                .body("new_values.eoq", is(4))
                .body("new_values.safety_stock", is(2))
                .body("new_values.max_stock", is(12))
                .body("written_at", notNullValue())
                .body("error_message", nullValue());
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void replay_same_idempotency_key_returns_original_result_not_error() {
        seedPn("TRAXIO-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-2", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-2","location":"TRAXIO-LOC-2","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-2:TRAXIO-LOC-2","tier":null,"shadow":false}
                """;

        given().contentType("application/json").body(body).when().post(ENDPOINT).then()
                .statusCode(200)
                .body("status", is("written"))
                .body("new_values.rop", is(5));

        String replayBody =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-2","location":"TRAXIO-LOC-2","rop":999,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-2:TRAXIO-LOC-2","tier":null,"shadow":false}
                """;

        given()
                .contentType("application/json")
                .body(replayBody)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("written"))
                .body("new_values.rop", is(5)) // original write's value, not the replay's 999
                .body("error_message", nullValue());
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void shadow_true_returns_shadowed_and_does_not_apply() {
        seedPn("TRAXIO-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-3", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-3","location":"TRAXIO-LOC-3","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-3:TRAXIO-LOC-3","tier":"bounded","shadow":true}
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("shadowed"))
                .body("new_values.rop", is(5));

        // Shadow mode must not touch PN_INVENTORY_LEVEL.
        long count = levelRowCount("TRAXIO-PN-3", "TRAXIO-LOC-3");
        org.junit.jupiter.api.Assertions.assertEquals(0L, count);
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void replay_of_shadowed_write_returns_shadowed() {
        seedPn("TRAXIO-PN-6", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-6", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-6","location":"TRAXIO-LOC-6","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-6:TRAXIO-LOC-6","tier":null,"shadow":true}
                """;

        given().contentType("application/json").body(body).when().post(ENDPOINT).then()
                .statusCode(200)
                .body("status", is("shadowed"));

        // The replay must reflect the ORIGINAL winner's ledger outcome — "shadowed", never a
        // blanket "written" (this is what ItemResult.originalStatus exists for).
        given().contentType("application/json").body(body).when().post(ENDPOINT).then()
                .statusCode(200)
                .body("status", is("shadowed"))
                .body("new_values.rop", is(5))
                .body("error_message", nullValue());
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void integer_tier_as_sent_by_the_real_python_client_is_accepted() {
        seedPn("TRAXIO-PN-7", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-7", "Y", "N");

        // AutonomyTier is an IntEnum: model_dump(mode="json") puts a bare integer on the wire.
        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-7","location":"TRAXIO-LOC-7","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-7:TRAXIO-LOC-7","tier":2,"shadow":false}
                """;

        given().contentType("application/json").body(body).when().post(ENDPOINT).then()
                .statusCode(200)
                .body("status", is("written"));

        Number tier = ledgerTier("2026-04-01:acme:TRAXIO-PN-7:TRAXIO-LOC-7");
        org.junit.jupiter.api.Assertions.assertEquals(2, tier.intValue());
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void unrecognized_tier_returns_failed_422() {
        given()
                .contentType("application/json")
                .body("""
                        {"tenant_id":"acme","pn":"TRAXIO-PN-8","location":"TRAXIO-LOC-8","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                         "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-8:TRAXIO-LOC-8","tier":"turbo","shadow":false}
                        """)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(422)
                .body("status", is("failed"))
                .body("error_message", notNullValue());
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void unknown_pn_returns_failed_with_error_message() {
        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-UNKNOWN","location":"TRAXIO-LOC-4","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-UNKNOWN:TRAXIO-LOC-4","tier":null,"shadow":false}
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(422)
                .body("status", is("failed"))
                .body("error_message", notNullValue())
                .body("tenant_id", is("acme"))
                .body("pn", is("TRAXIO-PN-UNKNOWN"))
                .body("location", is("TRAXIO-LOC-4"));
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void snake_case_fidelity() {
        seedPn("TRAXIO-PN-5", "SLW-ROTABLE", "ACTIVE");
        seedLocation("TRAXIO-LOC-5", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"TRAXIO-PN-5","location":"TRAXIO-LOC-5","rop":5,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-1","idempotency_key":"2026-04-01:acme:TRAXIO-PN-5:TRAXIO-LOC-5","tier":null,"shadow":false}
                """;

        given()
                .contentType("application/json")
                .body(body)
                .when()
                .post(ENDPOINT)
                .then()
                .statusCode(200)
                .body("$", org.hamcrest.Matchers.hasKey("tenant_id"))
                .body("$", org.hamcrest.Matchers.hasKey("old_values"))
                .body("$", org.hamcrest.Matchers.hasKey("new_values"))
                .body("$", org.hamcrest.Matchers.hasKey("written_at"))
                .body("$", org.hamcrest.Matchers.hasKey("error_message"));
    }

    @Test
    void no_token_is_401() {
        given()
                .contentType("application/json")
                .body("""
                        {"tenant_id":"acme","pn":"X","location":"Y","rop":1,"eoq":1,"safety_stock":1,"max_stock":1,
                         "provenance_id":"p","idempotency_key":"k","tier":null,"shadow":false}
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
                        {"tenant_id":"acme","pn":"X","location":"Y","rop":1,"eoq":1,"safety_stock":1,"max_stock":1,
                         "provenance_id":"p","idempotency_key":"k","tier":null,"shadow":false}
                        """)
                .when()
                .post(ENDPOINT)
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

    private Number ledgerTier(String idempotencyKey) {
        return (Number)
                QuarkusTransaction.requiringNew()
                        .call(
                                () ->
                                        em.createNativeQuery(
                                                        "SELECT TIER FROM WRITEBACK_LEDGER WHERE IDEMPOTENCY_KEY = ?1")
                                                .setParameter(1, idempotencyKey)
                                                .getSingleResult());
    }

    private long levelRowCount(String pn, String location) {
        Number count =
                (Number)
                        QuarkusTransaction.requiringNew()
                                .call(
                                        () -> {
                                            try {
                                                return em.createNativeQuery(
                                                                "SELECT COUNT(*) FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                                                        .setParameter(1, pn)
                                                        .setParameter(2, location)
                                                        .getSingleResult();
                                            } catch (NoResultException e) {
                                                return 0L;
                                            }
                                        });
        return count.longValue();
    }
}
