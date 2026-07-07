package trax.io.writeback;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.jwt.JwtSecurity;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;

/**
 * Verifies Task 11's Micrometer wiring: the {@code writeback.items} counter (tagged {@code
 * status}/{@code facade}) and the {@code writeback.batch.duration} timer (tagged {@code facade})
 * are incremented/recorded for the batch REST facade.
 */
@QuarkusTest
class MetricsTest {

    @Inject EntityManager em;

    @Inject MeterRegistry registry;

    private static final String ENDPOINT = "/api/v1/stock-levels";

    @Test
    @TestSecurity(user = "optimizer", roles = {"writeback:write"})
    @JwtSecurity(claims = {@Claim(key = "tenant_id", value = "acme")})
    void batch_facade_records_counter_and_timer() {
        seedPn("METRICS-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("METRICS-LOC-1", "Y", "N");

        String body =
                """
                {
                  "runId": "run-metrics-1",
                  "transactionId": "tx-metrics-1",
                  "items": [
                    {"rowId": 1, "partNo": "METRICS-PN-1", "location": "METRICS-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
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

        double count = registry.counter("writeback.items", "status", "ACCEPTED", "facade", "batch").count();
        assertTrue(count >= 1, "expected writeback.items{status=ACCEPTED,facade=batch} count >= 1, was " + count);

        Timer timer = registry.find("writeback.batch.duration").tag("facade", "batch").timer();
        assertNotNull(timer, "expected writeback.batch.duration{facade=batch} timer to be registered");
        assertTrue(timer.count() >= 1, "expected writeback.batch.duration{facade=batch} count >= 1");
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
