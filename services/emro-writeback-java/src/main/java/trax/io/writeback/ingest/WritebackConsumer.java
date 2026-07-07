package trax.io.writeback.ingest;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.smallrye.reactive.messaging.annotations.Blocking;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import org.eclipse.microprofile.reactive.messaging.Channel;
import org.eclipse.microprofile.reactive.messaging.Emitter;
import org.eclipse.microprofile.reactive.messaging.Incoming;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;
import trax.io.writeback.api.batch.BatchProcessor;

/**
 * Kafka ingestion entrypoint for the batch writeback facade (Task 10): consumes {@code
 * writeback-in} ({@code optimizer.writeback.v1}), reuses {@link BatchProcessor} verbatim (the same
 * core the PRD REST facade calls), and emits results ({@code writeback-results} /
 * {@code optimizer.writeback.results.v1}) or routes poison/failed payloads to the dead-letter topic
 * ({@code writeback-dlq} / {@code optimizer.writeback.dlq.v1}).
 *
 * <p>Two distinct failure modes are handled differently:
 *
 * <ul>
 *   <li><b>Malformed JSON</b> (cannot even parse into a {@link BatchRequest}) is not retried — the
 *       raw payload goes straight to the DLQ and the message is acked.
 *   <li><b>Batch-level infrastructure failures</b> (e.g. the DB is down, surfaced as a {@link
 *       RuntimeException} out of {@link BatchProcessor#process}) are retried up to 3 times with
 *       backoff (200ms / 800ms / 3200ms) before falling back to the DLQ. Per-row failures do NOT
 *       throw — {@link BatchProcessor} folds those into per-row {@code ERROR} results within a
 *       normal {@link BatchResponse}, which is still a "success" from this consumer's point of
 *       view.
 * </ul>
 */
@ApplicationScoped
public class WritebackConsumer {

    private static final Logger LOG = Logger.getLogger(WritebackConsumer.class);

    static final String KAFKA_PRINCIPAL = "kafka-ingest";
    static final String DEFAULT_TENANT = "default";
    static final String KAFKA_FACADE = "kafka";

    private static final int MAX_ATTEMPTS = 3;
    private static final long[] BACKOFF_MILLIS = {200L, 800L, 3200L};

    @Inject BatchProcessor processor;

    @Inject ObjectMapper mapper;

    @Channel("writeback-results")
    Emitter<String> results;

    @Channel("writeback-dlq")
    Emitter<String> dlq;

    @Incoming("writeback-in")
    @Blocking
    public void consume(String payload) {
        BatchRequest request;
        try {
            request = mapper.readValue(payload, BatchRequest.class);
        } catch (Exception parseFailure) {
            LOG.warnf(parseFailure, "malformed writeback payload, routing to DLQ: %s", payload);
            dlq.send(payload);
            return;
        }

        BatchResponse response = processWithRetry(request, payload);
        if (response == null) {
            // All retries exhausted; already sent to DLQ.
            return;
        }

        try {
            results.send(mapper.writeValueAsString(response));
        } catch (Exception impossible) {
            throw new RuntimeException(impossible);
        }
    }

    /**
     * Attempts {@link BatchProcessor#process} up to {@value #MAX_ATTEMPTS} times, backing off
     * 200ms/800ms/3200ms between attempts, for batch-level infrastructure failures ({@link
     * RuntimeException}). After the final failed attempt, the raw payload is routed to the DLQ and
     * {@code null} is returned.
     */
    private BatchResponse processWithRetry(BatchRequest request, String rawPayload) {
        String tenantId = request.tenantId() == null ? DEFAULT_TENANT : request.tenantId();

        RuntimeException lastFailure = null;
        for (int attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            try {
                return processor.process(request, tenantId, KAFKA_PRINCIPAL, KAFKA_FACADE);
            } catch (RuntimeException failure) {
                lastFailure = failure;
                LOG.warnf(
                        failure,
                        "writeback batch processing failed (attempt %d/%d, runId=%s)",
                        attempt,
                        MAX_ATTEMPTS,
                        request.runId());
                if (attempt < MAX_ATTEMPTS) {
                    sleep(BACKOFF_MILLIS[attempt - 1]);
                }
            }
        }

        LOG.warnf(
                lastFailure,
                "writeback batch processing failed after %d attempts (runId=%s), routing to DLQ",
                MAX_ATTEMPTS,
                request.runId());
        dlq.send(rawPayload);
        return null;
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
