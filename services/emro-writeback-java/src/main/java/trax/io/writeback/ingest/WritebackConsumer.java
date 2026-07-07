package trax.io.writeback.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.smallrye.reactive.messaging.annotations.Blocking;
import io.smallrye.reactive.messaging.kafka.Record;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.function.Function;
import org.eclipse.microprofile.reactive.messaging.Channel;
import org.eclipse.microprofile.reactive.messaging.Emitter;
import org.eclipse.microprofile.reactive.messaging.Incoming;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;
import trax.io.writeback.api.batch.BatchProcessor;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionBatchRequest;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionBatchResponse;
import trax.io.writeback.api.batch.RequisitionProcessor;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchRequest;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchResponse;
import trax.io.writeback.api.batch.TransferProcessor;

/**
 * Kafka ingestion entrypoint for all three batch writeback facades (Task 9/D14): consumes {@code
 * writeback-in} ({@code optimizer.writeback.v1}) and routes each message, by an OPTIONAL top-level
 * {@code domain} discriminator, to the matching processor — {@link BatchProcessor} (stock levels,
 * the default when {@code domain} is absent, preserving Task 10's original behavior verbatim),
 * {@link RequisitionProcessor} ({@code domain":"requisition"}), or {@link TransferProcessor}
 * ({@code "domain":"transfer"}) — the same cores the PRD REST facades call. Results are emitted
 * ({@code writeback-results} / {@code optimizer.writeback.results.v1}) or poison/failed payloads
 * are routed to the dead-letter topic ({@code writeback-dlq} / {@code optimizer.writeback.dlq.v1}).
 *
 * <p>One topic, one consumer: the {@code domain} field is peeked off a parsed {@link JsonNode}
 * before the payload is bound to the domain-specific request record, so an unrecognized value
 * never reaches a domain deserializer — it is treated as poison (DLQ, verbatim, null-keyed, WARN)
 * without ever attempting to parse the body as any particular DTO.
 *
 * <p>Results records are keyed by {@code runId} (the contract for {@code
 * optimizer.writeback.results.v1} — consumers partition/compact on it), via {@link
 * Record#of(Object, Object)} rather than a null-key send. DLQ records are keyed by {@code runId}
 * too when it's parseable off the request; a raw poison payload that never parsed into a request
 * (malformed JSON, or an unrecognized {@code domain}) has no runId to key by, so it goes out with
 * a null key — there is nothing else correct to key it on.
 *
 * <p>Two distinct failure modes are handled differently:
 *
 * <ul>
 *   <li><b>Malformed JSON / unrecognized domain</b> is not retried — the raw payload goes straight
 *       to the DLQ and the message is acked.
 *   <li><b>Batch-level infrastructure failures</b> (e.g. the DB is down, surfaced as a {@link
 *       RuntimeException} out of a processor's {@code process}) are retried up to 3 times with
 *       backoff (200ms / 800ms / 3200ms) before falling back to the DLQ. Per-row failures do NOT
 *       throw — each processor folds those into per-row {@code ERROR} results within a normal
 *       response, which is still a "success" from this consumer's point of view.
 * </ul>
 */
@ApplicationScoped
public class WritebackConsumer {

    private static final Logger LOG = Logger.getLogger(WritebackConsumer.class);

    static final String KAFKA_PRINCIPAL = "kafka-ingest";
    static final String DEFAULT_TENANT = "default";
    static final String KAFKA_FACADE = "kafka";

    static final String DOMAIN_FIELD = "domain";
    static final String DOMAIN_STOCK_LEVEL = "stock_level";
    static final String DOMAIN_REQUISITION = "requisition";
    static final String DOMAIN_TRANSFER = "transfer";

    private static final int MAX_ATTEMPTS = 3;
    private static final long[] BACKOFF_MILLIS = {200L, 800L, 3200L};

    @Inject BatchProcessor batchProcessor;

    @Inject RequisitionProcessor requisitionProcessor;

    @Inject TransferProcessor transferProcessor;

    @Inject ObjectMapper mapper;

    @Channel("writeback-results")
    Emitter<Record<String, String>> results;

    @Channel("writeback-dlq")
    Emitter<Record<String, String>> dlq;

    @Incoming("writeback-in")
    @Blocking
    public void consume(String payload) {
        JsonNode root;
        try {
            root = mapper.readTree(payload);
        } catch (Exception parseFailure) {
            LOG.warnf(parseFailure, "malformed writeback payload, routing to DLQ: %s", payload);
            // Poison payload never parsed, so there is no runId to key it by.
            dlq.send(Record.of(null, payload));
            return;
        }

        String domain =
                root.hasNonNull(DOMAIN_FIELD) ? root.get(DOMAIN_FIELD).asText() : DOMAIN_STOCK_LEVEL;

        switch (domain) {
            case DOMAIN_STOCK_LEVEL ->
                    route(
                            payload,
                            BatchRequest.class,
                            batchProcessor::process,
                            BatchRequest::runId,
                            BatchRequest::tenantId);
            case DOMAIN_REQUISITION ->
                    route(
                            payload,
                            RequisitionBatchRequest.class,
                            requisitionProcessor::process,
                            RequisitionBatchRequest::runId,
                            RequisitionBatchRequest::tenantId);
            case DOMAIN_TRANSFER ->
                    route(
                            payload,
                            TransferBatchRequest.class,
                            transferProcessor::process,
                            TransferBatchRequest::runId,
                            TransferBatchRequest::tenantId);
            default -> {
                LOG.warnf("unrecognized writeback domain '%s', routing to DLQ: %s", domain, payload);
                // Unknown domain is poison-class: never bound to any request type, so no runId.
                dlq.send(Record.of(null, payload));
            }
        }
    }

    /**
     * Domain-agnostic pipeline shared by all three routes: parse the payload to {@code
     * requestType}, run it through {@code processor} with retry, and emit the result (or DLQ on
     * exhaustion) — mirrors the pre-Task-9 stock-level-only behavior exactly, parameterized over
     * the request/response types.
     */
    private <RequestT, ResponseT> void route(
            String payload,
            Class<RequestT> requestType,
            DomainProcessor<RequestT, ResponseT> processor,
            Function<RequestT, String> runIdOf,
            Function<RequestT, String> tenantIdOf) {
        RequestT request;
        try {
            request = mapper.readValue(payload, requestType);
        } catch (Exception parseFailure) {
            LOG.warnf(parseFailure, "malformed writeback payload, routing to DLQ: %s", payload);
            // Poison payload never parsed, so there is no runId to key it by.
            dlq.send(Record.of(null, payload));
            return;
        }

        ResponseT response = processWithRetry(request, payload, processor, runIdOf, tenantIdOf);
        if (response == null) {
            // All retries exhausted; already sent to DLQ.
            return;
        }

        try {
            results.send(Record.of(runIdOf.apply(request), mapper.writeValueAsString(response)));
        } catch (Exception impossible) {
            throw new RuntimeException(impossible);
        }
    }

    /**
     * Attempts {@code processor.process} up to {@value #MAX_ATTEMPTS} times, backing off
     * 200ms/800ms/3200ms between attempts, for batch-level infrastructure failures ({@link
     * RuntimeException}). After the final failed attempt, the raw payload is routed to the DLQ and
     * {@code null} is returned.
     */
    private <RequestT, ResponseT> ResponseT processWithRetry(
            RequestT request,
            String rawPayload,
            DomainProcessor<RequestT, ResponseT> processor,
            Function<RequestT, String> runIdOf,
            Function<RequestT, String> tenantIdOf) {
        String tenantId = tenantIdOf.apply(request) == null ? DEFAULT_TENANT : tenantIdOf.apply(request);
        String runId = runIdOf.apply(request);

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
                        runId);
                if (attempt < MAX_ATTEMPTS) {
                    sleep(BACKOFF_MILLIS[attempt - 1]);
                }
            }
        }

        LOG.warnf(
                lastFailure,
                "writeback batch processing failed after %d attempts (runId=%s), routing to DLQ",
                MAX_ATTEMPTS,
                runId);
        // Unlike the malformed-JSON path, request parsed fine here, so its runId is available to key by.
        dlq.send(Record.of(runId, rawPayload));
        return null;
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    /** A domain processor's 4-arg {@code process(request, tenantId, principal, facadeTag)} shape. */
    @FunctionalInterface
    private interface DomainProcessor<RequestT, ResponseT> {
        ResponseT process(RequestT request, String tenantId, String principal, String facadeTag);
    }
}
