package trax.io.writeback.ingest;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.narayana.jta.QuarkusTransaction;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.kafka.InjectKafkaCompanion;
import io.quarkus.test.kafka.KafkaCompanionResource;
import io.quarkus.test.common.QuarkusTestResource;
import io.smallrye.reactive.messaging.kafka.companion.ConsumerTask;
import io.smallrye.reactive.messaging.kafka.companion.KafkaCompanion;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.time.Duration;
import java.util.function.Predicate;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;

/**
 * End-to-end Kafka ingestion tests for {@link WritebackConsumer}: produce onto {@code
 * optimizer.writeback.v1} and assert the DB + downstream topics ({@code
 * optimizer.writeback.results.v1}, {@code optimizer.writeback.dlq.v1}) reflect the outcome.
 *
 * <p>{@code RESULTS_TOPIC}/{@code DLQ_TOPIC} are shared across every test method in this class (a
 * single embedded broker for the whole class), so each test subscribes a fresh consumer group
 * (earliest offset) and filters for ITS OWN record among everything ever published — rather than
 * assuming it is the only or first record on the topic — to stay correct regardless of JUnit's
 * test execution order.
 */
@QuarkusTest
@QuarkusTestResource(KafkaCompanionResource.class)
class WritebackConsumerTest {

    @InjectKafkaCompanion KafkaCompanion companion;

    @Inject EntityManager em;

    private static final String IN_TOPIC = "optimizer.writeback.v1";
    private static final String RESULTS_TOPIC = "optimizer.writeback.results.v1";
    private static final String DLQ_TOPIC = "optimizer.writeback.dlq.v1";

    @Test
    void valid_batch_lands_in_db_and_results_topic() {
        seedPn("KAFKA-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("KAFKA-LOC-1", "Y", "N");

        String payload =
                """
                {
                  "runId": "run-kafka-1",
                  "transactionId": "tx-kafka-1",
                  "items": [
                    {"rowId": 1, "partNo": "KAFKA-PN-1", "location": "KAFKA-LOC-1", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        produce(payload);

        ConsumerRecord<String, String> resultRecord =
                awaitResultRecord(record -> "run-kafka-1".equals(record.key()));
        String resultJson = resultRecord.value();
        assertTrue(resultJson.contains("\"status\":\"ACCEPTED\""), "expected ACCEPTED status in: " + resultJson);
        assertEquals("run-kafka-1", resultRecord.key(), "results record must be keyed by runId");

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> {
                            Number count =
                                    (Number)
                                            QuarkusTransaction.requiringNew()
                                                    .call(
                                                            () ->
                                                                    em.createNativeQuery(
                                                                                    "SELECT COUNT(*) FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                                                                            .setParameter(1, "KAFKA-PN-1")
                                                                            .setParameter(2, "KAFKA-LOC-1")
                                                                            .getSingleResult());
                            assertEquals(1L, count.longValue());
                        });
    }

    @Test
    void message_without_domain_routes_to_stock_level() {
        seedPn("KDOM-PN-0", "SLW-ROTABLE", "ACTIVE");
        seedLocation("KDOM-LOC-0", "Y", "N");

        String payload =
                """
                {
                  "runId": "run-kdom-0",
                  "transactionId": "tx-kdom-0",
                  "items": [
                    {"rowId": 1, "partNo": "KDOM-PN-0", "location": "KDOM-LOC-0", "reorderLevel": 10, "eoqLevel": 20, "stockMin": 5, "stockMax": 50}
                  ]
                }
                """;

        produce(payload);

        ConsumerRecord<String, String> resultRecord =
                awaitResultRecord(record -> "run-kdom-0".equals(record.key()));
        String resultJson = resultRecord.value();
        assertTrue(resultJson.contains("\"status\":\"ACCEPTED\""), "expected ACCEPTED status in: " + resultJson);
        assertEquals("run-kdom-0", resultRecord.key(), "results record must be keyed by runId");

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> {
                            Number count =
                                    (Number)
                                            QuarkusTransaction.requiringNew()
                                                    .call(
                                                            () ->
                                                                    em.createNativeQuery(
                                                                                    "SELECT COUNT(*) FROM PN_INVENTORY_LEVEL WHERE PN = ?1 AND LOCATION = ?2")
                                                                            .setParameter(1, "KDOM-PN-0")
                                                                            .setParameter(2, "KDOM-LOC-0")
                                                                            .getSingleResult());
                            assertEquals(1L, count.longValue());
                        });
    }

    @Test
    void requisition_domain_routes_and_creates() {
        seedPn("KDOM-PN-1", "SLW-ROTABLE", "ACTIVE");
        seedLocation("KDOM-LOC-1", "Y", "N");

        String payload =
                """
                {
                  "domain": "requisition",
                  "runId": "run-kdom-1",
                  "transactionId": "tx-kdom-1",
                  "items": [
                    {"rowId": 1, "partNo": "KDOM-PN-1", "location": "KDOM-LOC-1", "qty": 5, "needBy": "2026-08-01"}
                  ]
                }
                """;

        produce(payload);

        ConsumerRecord<String, String> resultRecord =
                awaitResultRecord(record -> "run-kdom-1".equals(record.key()));
        String resultJson = resultRecord.value();
        assertTrue(resultJson.contains("\"status\":\"ACCEPTED\""), "expected ACCEPTED status in: " + resultJson);
        assertTrue(resultJson.contains("\"requisition\":"), "expected a requisition field in: " + resultJson);
        assertEquals("run-kdom-1", resultRecord.key(), "results record must be keyed by runId");

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> {
                            Number count =
                                    (Number)
                                            QuarkusTransaction.requiringNew()
                                                    .call(
                                                            () ->
                                                                    em.createNativeQuery(
                                                                                    "SELECT COUNT(*) FROM REQUISITION_HEADER WHERE REQUESTER_LOCATION = ?1")
                                                                            .setParameter(1, "KDOM-LOC-1")
                                                                            .getSingleResult());
                            assertEquals(1L, count.longValue());
                        });
    }

    @Test
    void transfer_domain_routes_and_creates() {
        seedPn("KDOM-PN-2", "SLW-ROTABLE", "ACTIVE");
        seedLocation("KDOM-FROM-2", "Y", "N");
        seedLocation("KDOM-TO-2", "Y", "N");

        String payload =
                """
                {
                  "domain": "transfer",
                  "runId": "run-kdom-2",
                  "transactionId": "tx-kdom-2",
                  "items": [
                    {"rowId": 1, "partNo": "KDOM-PN-2", "fromLocation": "KDOM-FROM-2", "toLocation": "KDOM-TO-2", "qty": 5, "batch": 1001, "deliveryDate": "2026-08-01"}
                  ]
                }
                """;

        produce(payload);

        ConsumerRecord<String, String> resultRecord =
                awaitResultRecord(record -> "run-kdom-2".equals(record.key()));
        String resultJson = resultRecord.value();
        assertTrue(resultJson.contains("\"status\":\"ACCEPTED\""), "expected ACCEPTED status in: " + resultJson);
        assertTrue(resultJson.contains("\"orderNumber\":"), "expected an orderNumber field in: " + resultJson);
        assertEquals("run-kdom-2", resultRecord.key(), "results record must be keyed by runId");

        await().atMost(Duration.ofSeconds(30))
                .untilAsserted(
                        () -> {
                            Number count =
                                    (Number)
                                            QuarkusTransaction.requiringNew()
                                                    .call(
                                                            () ->
                                                                    em.createNativeQuery(
                                                                                    "SELECT COUNT(*) FROM ORDER_HEADER WHERE ORDER_TYPE = 'TS' AND SHIPPED_FROM_LOCATION = ?1")
                                                                            .setParameter(1, "KDOM-FROM-2")
                                                                            .getSingleResult());
                            assertEquals(1L, count.longValue());
                        });
    }

    @Test
    void unknown_domain_lands_on_dlq() {
        String payload =
                """
                {
                  "domain": "nonsense",
                  "runId": "run-kdom-3",
                  "transactionId": "tx-kdom-3",
                  "items": []
                }
                """;

        produce(payload);

        ConsumerRecord<String, String> record = awaitDlqRecord(rec -> payload.equals(rec.value()));
        assertEquals(payload, record.value(), "unrecognized-domain payload lands on DLQ verbatim");
        assertNull(record.key(), "unrecognized domain never bound to a request type, so it has no runId to key by");
    }

    @Test
    void malformed_payload_lands_verbatim_on_dlq() {
        String payload = "{\"garbage\": true";

        produce(payload);

        ConsumerRecord<String, String> record = awaitDlqRecord(rec -> payload.equals(rec.value()));
        assertEquals(payload, record.value());
        assertNull(record.key(), "raw poison payload never parsed, so it has no runId to key by");
    }

    private void produce(String payload) {
        companion
                .produceStrings()
                .fromRecords(new org.apache.kafka.clients.producer.ProducerRecord<>(IN_TOPIC, payload));
    }

    /**
     * Subscribes a fresh consumer group (earliest offset) to {@code topic} and polls until a
     * record matching {@code predicate} shows up, returning it. Safe against every other test
     * method's records already sitting on the shared topic — it never assumes position or count.
     */
    private ConsumerRecord<String, String> awaitRecord(
            String topic, Predicate<ConsumerRecord<String, String>> predicate) {
        try (ConsumerTask<String, String> task = companion.consumeStrings().fromTopics(topic)) {
            await().atMost(Duration.ofSeconds(30))
                    .untilAsserted(
                            () -> assertTrue(task.getRecords().stream().anyMatch(predicate)));
            return task.getRecords().stream().filter(predicate).findFirst().orElseThrow();
        }
    }

    private ConsumerRecord<String, String> awaitResultRecord(
            Predicate<ConsumerRecord<String, String>> predicate) {
        return awaitRecord(RESULTS_TOPIC, predicate);
    }

    private ConsumerRecord<String, String> awaitDlqRecord(
            Predicate<ConsumerRecord<String, String>> predicate) {
        return awaitRecord(DLQ_TOPIC, predicate);
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
