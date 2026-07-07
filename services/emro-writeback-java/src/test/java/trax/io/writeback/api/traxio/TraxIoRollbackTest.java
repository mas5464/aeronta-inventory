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
import org.junit.jupiter.api.Test;

/**
 * Wire-contract-exact tests for the Trax IO rollback facade ({@code POST /traxio/v1/rollback}).
 * Named behaviors mirror agent-spine's {@code tests/writeback/test_rollback.py} against
 * {@code InMemoryWritebackTarget.rollback} (the Python reference implementation this facade must
 * conform to). Bodies are raw JSON strings so a naming-strategy regression fails loudly via
 * JsonPath key-presence assertions, matching {@code TraxIoApplyTest}'s style.
 */
@QuarkusTest
class TraxIoRollbackTest {

    @Inject EntityManager em;

    private static final String APPLY_ENDPOINT = "/traxio/v1/inventory-levels";
    private static final String ROLLBACK_ENDPOINT = "/traxio/v1/rollback";

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void rollback_reverts_latest_written_to_prior_values() {
        seedPn("RB-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-1", "Y", "N");

        apply("RB-PN-1", "RB-LOC-1", 5, "rb1-k1");
        apply("RB-PN-1", "RB-LOC-1", 7, "rb1-k2");

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-1", "RB-LOC-1", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("tenant_id", is("acme"))
                .body("pn", is("RB-PN-1"))
                .body("location", is("RB-LOC-1"))
                .body("status", is("rolled_back"))
                .body("from_values.rop", is(7))
                .body("to_values.rop", is(5))
                .body("reverted_from_version", is(2))
                .body("new_version", is(3))
                .body("rolled_back_at", notNullValue())
                .body("error_message", nullValue());

        // The level is back to 5: a subsequent write's old_values must reflect it.
        given()
                .contentType("application/json")
                .body(applyBody("RB-PN-1", "RB-LOC-1", 9, "rb1-k3"))
                .when()
                .post(APPLY_ENDPOINT)
                .then()
                .statusCode(200)
                .body("old_values.rop", is(5));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void rollback_with_no_prior_write_is_nothing_to_revert() {
        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-NONE", "RB-LOC-NONE", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("nothing_to_revert"))
                .body("new_version", nullValue())
                .body("reverted_from_version", nullValue());
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void rollback_of_only_first_write_is_nothing_to_revert() {
        seedPn("RB-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-2", "Y", "N");

        apply("RB-PN-2", "RB-LOC-2", 5, "rb2-k1"); // old_values is null -> nothing to revert to

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-2", "RB-LOC-2", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("nothing_to_revert"));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void rollback_outside_window() {
        seedPn("RB-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-3", "Y", "N");

        apply("RB-PN-3", "RB-LOC-3", 5, "rb3-k1");
        apply("RB-PN-3", "RB-LOC-3", 7, "rb3-k2");

        long ledgerCountBefore = ledgerRowCount("RB-PN-3", "RB-LOC-3");
        long levelCountBefore = levelRowCount("RB-PN-3", "RB-LOC-3");

        // Default window is 90 days; 200 days later is well outside it.
        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-3", "RB-LOC-3", "2026-10-18T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("outside_window"))
                .body("from_values", nullValue())
                .body("to_values", nullValue())
                .body("new_version", nullValue());

        org.junit.jupiter.api.Assertions.assertEquals(
                ledgerCountBefore, ledgerRowCount("RB-PN-3", "RB-LOC-3"), "no new ledger row");
        org.junit.jupiter.api.Assertions.assertEquals(
                levelCountBefore, levelRowCount("RB-PN-3", "RB-LOC-3"), "level row untouched");

        // The level must still hold the latest (unrolled-back) value.
        given()
                .contentType("application/json")
                .body(applyBody("RB-PN-3", "RB-LOC-3", 9, "rb3-k3"))
                .when()
                .post(APPLY_ENDPOINT)
                .then()
                .statusCode(200)
                .body("old_values.rop", is(7));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void subsequent_write_sees_rolled_back_values_as_old_values() {
        seedPn("RB-PN-4", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-4", "Y", "N");

        apply("RB-PN-4", "RB-LOC-4", 5, "rb4-k1");
        apply("RB-PN-4", "RB-LOC-4", 7, "rb4-k2");

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-4", "RB-LOC-4", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("rolled_back"));

        given()
                .contentType("application/json")
                .body(applyBody("RB-PN-4", "RB-LOC-4", 42, "rb4-k4"))
                .when()
                .post(APPLY_ENDPOINT)
                .then()
                .statusCode(200)
                .body("old_values.rop", is(5))
                .body("new_values.rop", is(42));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void shadowed_entries_are_skipped_when_finding_latest_written() {
        seedPn("RB-PN-5", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-5", "Y", "N");

        apply("RB-PN-5", "RB-LOC-5", 5, "rb5-k1");
        apply("RB-PN-5", "RB-LOC-5", 7, "rb5-k2");
        applyShadow("RB-PN-5", "RB-LOC-5", 999, "k3-shadow");

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-5", "RB-LOC-5", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("rolled_back"))
                .body("from_values.rop", is(7)) // the WRITTEN k2, not the SHADOWED k3
                .body("to_values.rop", is(5))
                .body("reverted_from_version", is(2)); // k1=1, k2=2, shadow=3 (still ledgered)
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void second_sequential_rollback_ping_pongs_to_the_next_latest_written() {
        seedPn("RB-PN-6", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-6", "Y", "N");

        apply("RB-PN-6", "RB-LOC-6", 5, "rb6-k1");
        apply("RB-PN-6", "RB-LOC-6", 7, "rb6-k2");

        String body = rollbackBody("RB-PN-6", "RB-LOC-6", "2026-04-01T00:00:00Z");

        given().contentType("application/json").body(body).when().post(ROLLBACK_ENDPOINT).then()
                .statusCode(200)
                .body("status", is("rolled_back"))
                .body("from_values.rop", is(7))
                .body("to_values.rop", is(5))
                .body("reverted_from_version", is(2))
                .body("new_version", is(3));

        // A second, identical rollback request (no intervening apply) is NOT a retry of the same
        // reverted entry: the first rollback's own reverting write is now itself the latest
        // WRITTEN row (version 3), so per the literal contract this call reverts THAT row back to
        // its own old_values (= version 2's values, 7) — ping-pong is contract-conformant, not a
        // special case the facade suppresses.
        given().contentType("application/json").body(body).when().post(ROLLBACK_ENDPOINT).then()
                .statusCode(200)
                .body("status", is("rolled_back"))
                .body("from_values.rop", is(5))
                .body("to_values.rop", is(7))
                .body("reverted_from_version", is(3))
                .body("new_version", is(4))
                .body("error_message", nullValue());

        org.junit.jupiter.api.Assertions.assertEquals(4L, ledgerRowCount("RB-PN-6", "RB-LOC-6"));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void reverting_write_failure_maps_to_nothing_to_revert_with_error_message() {
        seedPn("RB-PN-7", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-7", "Y", "N");

        apply("RB-PN-7", "RB-LOC-7", 5, "rb7-k1");
        apply("RB-PN-7", "RB-LOC-7", 7, "rb7-k2");

        // Retire the PN between the writes and the rollback so the reverting write's validation
        // fails (REJECTED_UNKNOWN_KEY-equivalent) — a Java-only failure path with no Python
        // precedent (fake_emro's in-memory target can never fail its own reverting write).
        retirePn("RB-PN-7");

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-7", "RB-LOC-7", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("nothing_to_revert"))
                .body("error_message", notNullValue())
                .body("new_version", nullValue());
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void snake_case_fidelity() {
        seedPn("RB-PN-8", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-8", "Y", "N");

        apply("RB-PN-8", "RB-LOC-8", 5, "rb8-k1");
        apply("RB-PN-8", "RB-LOC-8", 7, "rb8-k2");

        given()
                .contentType("application/json")
                .body(rollbackBody("RB-PN-8", "RB-LOC-8", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("$", org.hamcrest.Matchers.hasKey("tenant_id"))
                .body("$", org.hamcrest.Matchers.hasKey("pn"))
                .body("$", org.hamcrest.Matchers.hasKey("location"))
                .body("$", org.hamcrest.Matchers.hasKey("status"))
                .body("$", org.hamcrest.Matchers.hasKey("from_values"))
                .body("$", org.hamcrest.Matchers.hasKey("to_values"))
                .body("$", org.hamcrest.Matchers.hasKey("reverted_from_version"))
                .body("$", org.hamcrest.Matchers.hasKey("new_version"))
                .body("$", org.hamcrest.Matchers.hasKey("rolled_back_at"))
                .body("$", org.hamcrest.Matchers.hasKey("error_message"));
    }

    @Test
    @TestSecurity(user = "planner", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void default_principal_is_planner_when_absent() {
        seedPn("RB-PN-9", "SLW-ROTABLE", "ACTIVE");
        seedLocation("RB-LOC-9", "Y", "N");

        apply("RB-PN-9", "RB-LOC-9", 5, "rb9-k1");
        apply("RB-PN-9", "RB-LOC-9", 7, "rb9-k2");

        String bodyWithoutPrincipal =
                """
                {"tenant_id":"acme","pn":"RB-PN-9","location":"RB-LOC-9","reason":"bad rec","requested_at":"2026-04-01T00:00:00Z"}
                """;

        given()
                .contentType("application/json")
                .body(bodyWithoutPrincipal)
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(200)
                .body("status", is("rolled_back"));

        Number version =
                ledgerVersionForKey("acme", "RB-PN-9", "RB-LOC-9", "rollback:rb9-k2");
        String principal = ledgerPrincipalForVersion("acme", "RB-PN-9", "RB-LOC-9", version);
        org.junit.jupiter.api.Assertions.assertEquals("planner", principal);
    }

    @Test
    void no_token_is_401() {
        given()
                .contentType("application/json")
                .body(rollbackBody("X", "Y", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(401);
    }

    @Test
    @TestSecurity(user = "x", roles = {"writeback:read"})
    void wrong_role_is_403() {
        given()
                .contentType("application/json")
                .body(rollbackBody("X", "Y", "2026-04-01T00:00:00Z"))
                .when()
                .post(ROLLBACK_ENDPOINT)
                .then()
                .statusCode(403);
    }

    // ---- helpers ----

    private void apply(String pn, String location, int rop, String idempotencyKey) {
        given()
                .contentType("application/json")
                .body(applyBody(pn, location, rop, idempotencyKey))
                .when()
                .post(APPLY_ENDPOINT)
                .then()
                .statusCode(200);
    }

    private void applyShadow(String pn, String location, int rop, String idempotencyKey) {
        String body =
                "{\"tenant_id\":\"acme\",\"pn\":\""
                        + pn
                        + "\",\"location\":\""
                        + location
                        + "\",\"rop\":"
                        + rop
                        + ",\"eoq\":4,\"safety_stock\":2,\"max_stock\":12,"
                        + "\"provenance_id\":\"p-1\",\"idempotency_key\":\""
                        + idempotencyKey
                        + "\",\"tier\":null,\"shadow\":true}";
        given().contentType("application/json").body(body).when().post(APPLY_ENDPOINT).then().statusCode(200);
    }

    private static String applyBody(String pn, String location, int rop, String idempotencyKey) {
        return "{\"tenant_id\":\"acme\",\"pn\":\""
                + pn
                + "\",\"location\":\""
                + location
                + "\",\"rop\":"
                + rop
                + ",\"eoq\":4,\"safety_stock\":2,\"max_stock\":12,"
                + "\"provenance_id\":\"p-1\",\"idempotency_key\":\""
                + idempotencyKey
                + "\",\"tier\":null,\"shadow\":false}";
    }

    private static String rollbackBody(String pn, String location, String requestedAt) {
        return "{\"tenant_id\":\"acme\",\"pn\":\""
                + pn
                + "\",\"location\":\""
                + location
                + "\",\"reason\":\"bad rec\",\"principal\":\"planner\",\"requested_at\":\""
                + requestedAt
                + "\"}";
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

    private void retirePn(String pn) {
        QuarkusTransaction.requiringNew()
                .run(
                        () ->
                                em.createNativeQuery("UPDATE PN_MASTER SET STATUS = 'RETIRED' WHERE PN = ?1")
                                        .setParameter(1, pn)
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

    private long ledgerRowCount(String pn, String location) {
        Number count =
                (Number)
                        QuarkusTransaction.requiringNew()
                                .call(
                                        () ->
                                                em.createNativeQuery(
                                                                "SELECT COUNT(*) FROM WRITEBACK_LEDGER WHERE PN = ?1 AND LOCATION = ?2")
                                                        .setParameter(1, pn)
                                                        .setParameter(2, location)
                                                        .getSingleResult());
        return count.longValue();
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
                                            } catch (jakarta.persistence.NoResultException e) {
                                                return 0L;
                                            }
                                        });
        return count.longValue();
    }

    private Number ledgerVersionForKey(String tenantId, String pn, String location, String idempotencyKey) {
        return (Number)
                QuarkusTransaction.requiringNew()
                        .call(
                                () ->
                                        em.createNativeQuery(
                                                        "SELECT VERSION FROM WRITEBACK_LEDGER"
                                                                + " WHERE TENANT_ID = ?1 AND PN = ?2 AND LOCATION = ?3 AND IDEMPOTENCY_KEY = ?4")
                                                .setParameter(1, tenantId)
                                                .setParameter(2, pn)
                                                .setParameter(3, location)
                                                .setParameter(4, idempotencyKey)
                                                .getSingleResult());
    }

    private String ledgerPrincipalForVersion(
            String tenantId, String pn, String location, Number version) {
        return (String)
                QuarkusTransaction.requiringNew()
                        .call(
                                () ->
                                        em.createNativeQuery(
                                                        "SELECT PRINCIPAL FROM WRITEBACK_LEDGER"
                                                                + " WHERE TENANT_ID = ?1 AND PN = ?2 AND LOCATION = ?3 AND VERSION = ?4")
                                                .setParameter(1, tenantId)
                                                .setParameter(2, pn)
                                                .setParameter(3, location)
                                                .setParameter(4, version.longValue())
                                                .getSingleResult());
    }
}
