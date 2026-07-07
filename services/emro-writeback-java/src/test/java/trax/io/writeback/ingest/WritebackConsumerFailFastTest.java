package trax.io.writeback.ingest;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.common.QuarkusTestResource;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.junit.QuarkusTestProfile;
import io.quarkus.test.junit.TestProfile;
import io.quarkus.test.kafka.InjectKafkaCompanion;
import io.quarkus.test.kafka.KafkaCompanionResource;
import io.smallrye.reactive.messaging.kafka.companion.ConsumerTask;
import io.smallrye.reactive.messaging.kafka.companion.KafkaCompanion;
import java.time.Duration;
import java.util.Map;
import java.util.function.Predicate;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.junit.jupiter.api.Test;

/**
 * Pins the D15 Kafka fail-fast path end-to-end: an {@link
 * trax.io.writeback.domain.InfrastructureException} out of a processor's {@code process(...)}
 * must survive {@link WritebackConsumer#processWithRetry}'s 3 attempts and land the raw request
 * payload on the DLQ, keyed by {@code runId}.
 *
 * <p><b>Test-seam choice (documented per the Task 10 brief):</b> forcing a REAL Oracle connection
 * outage against the Dev Services container isn't practical in a unit/integration test. Rather than
 * introduce Mockito (not currently a dependency) or hand-roll a stub of a concrete, non-interface
 * class, this test reuses the codebase's OWN established pattern for swapping test doubles into CDI
 * — see {@code TestOrderNumberSource}/{@code TestRequisitionNumberSource} in {@code
 * trax.io.writeback.persistence}, which are {@code @Alternative} beans. Those two use {@code
 * @Priority} to enable themselves GLOBALLY (appropriate there, since every test should use a fake
 * number source). A fail-fast override must NOT be global — it would corrupt every other test that
 * touches {@link trax.io.writeback.api.batch.BatchProcessor} (REST facade tests included) if it
 * were active app-wide. Instead, {@link FailFastBatchProcessor} is a plain {@code @Alternative}
 * (no {@code @Priority}) selectively enabled ONLY for this test class's own Quarkus application
 * instance via {@code quarkus.arc.selected-alternatives} in {@link FailFastProfile} — the same
 * {@code QuarkusTestProfile} technique {@code FlywayMigrationTest} already uses in this module to
 * boot an isolated app instance with different config. Every other test class (default profile)
 * keeps using the real {@code BatchProcessor}.
 */
@QuarkusTest
@TestProfile(WritebackConsumerFailFastTest.FailFastProfile.class)
@QuarkusTestResource(KafkaCompanionResource.class)
class WritebackConsumerFailFastTest {

    @InjectKafkaCompanion KafkaCompanion companion;

    private static final String IN_TOPIC = "optimizer.writeback.v1";
    private static final String DLQ_TOPIC = "optimizer.writeback.dlq.v1";

    @Test
    void infrastructure_failure_retries_then_lands_on_dlq() {
        String payload =
                """
                {
                  "runId": "run-failfast-1",
                  "transactionId": "tx-failfast-1",
                  "items": [
                    {"rowId": 1, "partNo": "ANY-PN", "location": "ANY-LOC", "reorderLevel": 10}
                  ]
                }
                """;

        companion.produceStrings().fromRecords(new ProducerRecord<>(IN_TOPIC, payload));

        try (ConsumerTask<String, String> task = companion.consumeStrings().fromTopics(DLQ_TOPIC)) {
            Predicate<ConsumerRecord<String, String>> matchesRun =
                    rec -> "run-failfast-1".equals(rec.key());
            // MAX_ATTEMPTS=3 with 200ms/800ms/3200ms backoff -> ~4.2s of retrying before the DLQ
            // send; 30s leaves ample margin.
            await().atMost(Duration.ofSeconds(30))
                    .untilAsserted(() -> assertTrue(task.getRecords().stream().anyMatch(matchesRun)));
            ConsumerRecord<String, String> record =
                    task.getRecords().stream().filter(matchesRun).findFirst().orElseThrow();
            assertEquals(
                    payload,
                    record.value(),
                    "batch-level infrastructure failure DLQs the raw request payload after retry exhaustion");
        }
    }

    public static class FailFastProfile implements QuarkusTestProfile {
        @Override
        public Map<String, String> getConfigOverrides() {
            return Map.of("quarkus.arc.selected-alternatives", FailFastBatchProcessor.class.getName());
        }
    }
}
