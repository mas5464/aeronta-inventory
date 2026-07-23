package trax.io.writeback.api.traxio;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.Matchers.everyItem;
import static org.hamcrest.Matchers.not;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.jwt.JwtSecurity;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.math.BigDecimal;
import java.sql.Timestamp;
import org.junit.jupiter.api.Test;

/**
 * Tests for the out-of-band history facade ({@code GET /traxio/v1/history/out-of-band}) — a
 * separate surface from {@link TraxIoHistoryTest}'s ledger-backed {@code /traxio/v1/history}.
 * Confirms {@code PN_INVENTORY_LEVEL_AUDIT} rows written by some OTHER eMRO writer (not this
 * service) are visible here, while rows this service itself wrote are excluded, data-driven off
 * {@code WRITEBACK_LEDGER.PRINCIPAL} — never a hardcoded principal list (spec D13).
 */
@QuarkusTest
class OutOfBandHistoryTest {

    @Inject EntityManager em;

    private static final String APPLY_ENDPOINT = "/traxio/v1/inventory-levels";
    private static final String OOB_ENDPOINT = "/traxio/v1/history/out-of-band";

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void out_of_band_edit_appears() {
        seedPn("OOB-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("OOB-LOC-1", "Y", "N");
        insertAuditRow("OOB-PN-1", "OOB-LOC-1", "SOMEONE_ELSE", new BigDecimal("42"));

        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "OOB-PN-1")
                .queryParam("location", "OOB-LOC-1")
                .when()
                .get(OOB_ENDPOINT)
                .then()
                .statusCode(200)
                .body("size()", is(1))
                .body("[0].pn", is("OOB-PN-1"))
                .body("[0].location", is("OOB-LOC-1"))
                .body("[0].modified_by", is("SOMEONE_ELSE"))
                .body("[0].reorder_level", is(42))
                .body("[0]", org.hamcrest.Matchers.not(org.hamcrest.Matchers.hasKey("version")));
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write", "writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void service_writes_are_excluded() {
        seedPn("OOB-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("OOB-LOC-2", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"OOB-PN-2","location":"OOB-LOC-2","rop":7,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-oob-2","idempotency_key":"2026-04-01:acme:OOB-PN-2:OOB-LOC-2:v1","tier":null,"shadow":false}
                """;
        given().contentType("application/json").body(body).when().post(APPLY_ENDPOINT).then().statusCode(200);

        // Confirm the audit table DOES contain a row for this service's own write.
        long auditCount = countAuditRows("OOB-PN-2", "OOB-LOC-2");
        org.junit.jupiter.api.Assertions.assertEquals(
                1L, auditCount, "service write should have produced exactly one audit row");

        // But the out-of-band endpoint must not surface it: its MODIFIED_BY ("agent-spine") is
        // present in WRITEBACK_LEDGER.PRINCIPAL for this (tenant, pn, location).
        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "OOB-PN-2")
                .queryParam("location", "OOB-LOC-2")
                .when()
                .get(OOB_ENDPOINT)
                .then()
                .statusCode(200)
                .body("size()", is(0));
    }

    @Test
    @TestSecurity(user = "agent-spine", roles = {"writeback:write", "writeback:read"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void mixed_rows_only_out_of_band_ones_returned() {
        seedPn("OOB-PN-3", "SLW-ROTABLE", "ACTIVE");
        seedLocation("OOB-LOC-3", "Y", "N");

        String body =
                """
                {"tenant_id":"acme","pn":"OOB-PN-3","location":"OOB-LOC-3","rop":7,"eoq":4,"safety_stock":2,"max_stock":12,
                 "provenance_id":"p-oob-3","idempotency_key":"2026-04-01:acme:OOB-PN-3:OOB-LOC-3:v1","tier":null,"shadow":false}
                """;
        given().contentType("application/json").body(body).when().post(APPLY_ENDPOINT).then().statusCode(200);

        insertAuditRow("OOB-PN-3", "OOB-LOC-3", "PLANNER_JANE", new BigDecimal("99"));

        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "OOB-PN-3")
                .queryParam("location", "OOB-LOC-3")
                .when()
                .get(OOB_ENDPOINT)
                .then()
                .statusCode(200)
                .body("size()", is(1))
                .body("[0].modified_by", is("PLANNER_JANE"))
                .body("modified_by", everyItem(is("PLANNER_JANE")))
                .body("modified_by", everyItem(not(is("agent-spine"))));
    }

    @Test
    @TestSecurity(user = "x", roles = {"writeback:write"})
    void read_role_required() {
        given()
                .queryParam("tenant_id", "acme")
                .queryParam("pn", "X")
                .queryParam("location", "Y")
                .when()
                .get(OOB_ENDPOINT)
                .then()
                .statusCode(403);
    }

    private void insertAuditRow(String pn, String location, String modifiedBy, BigDecimal reorderLevel) {
        QuarkusTransaction.requiringNew()
                .run(
                        () ->
                                em.createNativeQuery(
                                                "INSERT INTO PN_INVENTORY_LEVEL_AUDIT"
                                                        + " (PN, LOCATION, CREATED_BY, CREATED_DATE, COMPANY,"
                                                        + " MODIFIED_BY, MODIFIED_DATE, REORDER_LEVEL)"
                                                        + " VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)")
                                        .setParameter(1, pn)
                                        .setParameter(2, location)
                                        .setParameter(3, modifiedBy)
                                        .setParameter(4, new Timestamp(System.currentTimeMillis()))
                                        .setParameter(5, "TRAX")
                                        .setParameter(6, modifiedBy)
                                        .setParameter(7, new Timestamp(System.currentTimeMillis()))
                                        .setParameter(8, reorderLevel)
                                        .executeUpdate());
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
