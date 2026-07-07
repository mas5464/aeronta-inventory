package trax.io.writeback.ingest;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
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
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;

/**
 * End-to-end Kafka ingestion tests for {@link WritebackConsumer}: produce onto {@code
 * optimizer.writeback.v1} and assert the DB + downstream topics ({@code
 * optimizer.writeback.results.v1}, {@code optimizer.writeback.dlq.v1}) reflect the outcome.
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

        companion.produceStrings().fromRecords(new org.apache.kafka.clients.producer.ProducerRecord<>(IN_TOPIC, payload));

        ConsumerTask<String, String> results = companion.consumeStrings().fromTopics(RESULTS_TOPIC, 1);
        results.awaitCompletion(Duration.ofSeconds(30));
        assertEquals(1, results.getRecords().size());
        String resultJson = results.getRecords().get(0).value();
        assertTrue(resultJson.contains("\"status\":\"ACCEPTED\""), "expected ACCEPTED status in: " + resultJson);

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
    void malformed_payload_lands_verbatim_on_dlq() {
        String payload = "{\"garbage\": true";

        companion.produceStrings().fromRecords(new org.apache.kafka.clients.producer.ProducerRecord<>(IN_TOPIC, payload));

        ConsumerTask<String, String> dlq = companion.consumeStrings().fromTopics(DLQ_TOPIC, 1);
        dlq.awaitCompletion(Duration.ofSeconds(30));
        assertEquals(1, dlq.getRecords().size());
        ConsumerRecord<String, String> record = dlq.getRecords().get(0);
        assertEquals(payload, record.value());
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
