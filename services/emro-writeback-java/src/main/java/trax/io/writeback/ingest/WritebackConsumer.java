package trax.io.writeback.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.smallrye.reactive.messaging.annotations.Blocking;
import io.smallrye.reactive.messaging.kafka.Record;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Function;
import org.eclipse.microprofile.reactive.messaging.Channel;
import org.eclipse.microprofile.reactive.messaging.Emitter;
import org.eclipse.microprofile.reactive.messaging.Incoming;
import org.eclipse.microprofile.reactive.messaging.OnOverflow;
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
 *   <li><b>Batch-level infrastructure failures</b> (a connection-class DB outage, surfaced as an
 *       {@link trax.io.writeback.domain.InfrastructureException} out of a processor's {@code
 *       process}) are retried up to 3 times with backoff (200ms / 800ms / 3200ms) before falling
 *       back to the DLQ. Per-row failures do NOT throw — each processor folds those into per-row
 *       {@code ERROR} results within a normal response, which is still a "success" from this
 *       consumer's point of view.
 * </ul>
 *
 * <p><b>D15:</b> this retry path is only reachable because every processor is invoked here with
 * {@code failFastOnInfrastructure=true} (via the canonical 5-arg {@code process(...)} overload —
 * see {@link DomainProcessor}), which lets {@link trax.io.writeback.domain.InfrastructureException}
 * propagate out instead of being folded to a per-row {@code ERROR} the way the REST facades fold
 * it (they call the 3-/4-arg convenience overloads, which always pass {@code false}).
 *
 * <p><b>§5:</b> the {@code writeback-results}/{@code writeback-dlq} emitters are configured {@code
 * @OnOverflow(BUFFER, bufferSize=1024)} rather than the default unbounded/fail strategy (see the
 * field declarations below), and a failed {@code results.send(...)} is itself retried up to 3
 * times before the response JSON (not the original request) is routed to the DLQ — see {@link
 * #sendResult} — so neither an emitter backpressure spike nor a broken results topic causes
 * infinite redelivery of the inbound message.
 *
 * <p><b>Observing async emitter failures (PR #5 review):</b> {@link Emitter#send} returns a
 * {@link CompletionStage} — the broker ack/nack is asynchronous and does not surface as a thrown
 * exception from {@code send(...)} itself. Every {@code send(...)} call in this class (results,
 * and every DLQ send: the poison-payload path, the retry-exhausted path, and the response-DLQ
 * fallback) is therefore awaited via {@link #awaitSend} with a bounded timeout ({@link
 * #SEND_AWAIT_SECONDS}) so a broker-side nack surfaces synchronously as a failed attempt — for
 * {@code results.send(...)} this feeds {@link #sendResult}'s existing retry→DLQ-fallback loop
 * exactly as a synchronous failure would. A DLQ send itself failing (including its await) is a
 * different, terminal case: there is no further fallback queue to route to, so {@link
 * #sendToDlq} logs at ERROR and RETHROWS rather than swallowing it — the exception propagates out
 * of {@link #consume}, which nacks the inbound Kafka record and lets the broker redeliver it. The
 * message spins until the DLQ recovers; this is a deliberate no-silent-drop choice, not an
 * oversight — see {@link #sendToDlq}'s Javadoc.
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

    /** §5: bounded retry budget for a failed {@code results.send(...)} emit. */
    private static final int RESULTS_SEND_MAX_ATTEMPTS = 3;

    /** §5: short backoff between {@code results.send(...)} retry attempts. */
    private static final long RESULTS_SEND_BACKOFF_MILLIS = 200L;

    /** §5: bounded buffer size for both outgoing emitters' overflow strategy. */
    private static final int EMITTER_BUFFER_SIZE = 1024;

    /**
     * Bound on how long any single {@code Emitter.send(...)} is awaited (see {@link #awaitSend})
     * before its {@link CompletionStage} is treated as failed. Applies uniformly to the results
     * emitter and the DLQ emitter.
     */
    private static final long SEND_AWAIT_SECONDS = 30L;

    @Inject BatchProcessor batchProcessor;

    @Inject RequisitionProcessor requisitionProcessor;

    @Inject TransferProcessor transferProcessor;

    @Inject ObjectMapper mapper;

    // §5: BUFFER (bounded, EMITTER_BUFFER_SIZE) rather than the default UNBOUNDED_BUFFER/FAIL —
    // a burst of results/DLQ sends beyond downstream Kafka backpressure capacity is buffered up to
    // this bound instead of growing without limit or throwing immediately on the send() call.
    @Channel("writeback-results")
    @OnOverflow(value = OnOverflow.Strategy.BUFFER, bufferSize = EMITTER_BUFFER_SIZE)
    Emitter<Record<String, String>> results;

    @Channel("writeback-dlq")
    @OnOverflow(value = OnOverflow.Strategy.BUFFER, bufferSize = EMITTER_BUFFER_SIZE)
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
            sendToDlq(Record.of(null, payload), "malformed writeback payload");
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
                sendToDlq(Record.of(null, payload), "unrecognized writeback domain '" + domain + "'");
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
            sendToDlq(Record.of(null, payload), "malformed writeback payload");
            return;
        }

        ResponseT response = processWithRetry(request, payload, processor, runIdOf, tenantIdOf);
        if (response == null) {
            // All retries exhausted; already sent to DLQ.
            return;
        }

        String responseJson;
        try {
            responseJson = mapper.writeValueAsString(response);
        } catch (Exception impossible) {
            throw new RuntimeException(impossible);
        }
        sendResult(runIdOf.apply(request), responseJson);
    }

    /**
     * Attempts {@code processor.process} up to {@value #MAX_ATTEMPTS} times, backing off
     * 200ms/800ms/3200ms between attempts, for batch-level infrastructure failures ({@link
     * RuntimeException} — since D15, this is how an {@link
     * trax.io.writeback.domain.InfrastructureException} raised with {@code
     * failFastOnInfrastructure=true} actually reaches this loop instead of being folded to a
     * per-row {@code ERROR} inside the processor). After the final failed attempt, the raw payload
     * is routed to the DLQ and {@code null} is returned.
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
                return processor.process(request, tenantId, KAFKA_PRINCIPAL, KAFKA_FACADE, true);
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
        sendToDlq(Record.of(runId, rawPayload), "writeback batch processing retries exhausted (runId=" + runId + ")");
        return null;
    }

    /**
     * §5: attempts {@code results.send(...)} up to {@value #RESULTS_SEND_MAX_ATTEMPTS} times,
     * backing off {@value #RESULTS_SEND_BACKOFF_MILLIS}ms between attempts. Each attempt awaits the
     * send's {@link CompletionStage} (see {@link #awaitSend}) so an asynchronous broker-side nack
     * counts as a failed attempt just like a synchronously-thrown exception would. If every attempt
     * fails, the response JSON itself (not the original request payload) is routed to the DLQ, keyed
     * by {@code runId} like every other DLQ record, and a WARN is logged — then this method returns
     * normally. Returning normally (rather than rethrowing) lets {@link #consume} complete without
     * exception, which acks the inbound Kafka record, so a persistently broken results topic cannot
     * cause infinite redelivery of the same input message. (A DLQ send itself failing is a different
     * matter — see {@link #sendToDlq}.)
     */
    private void sendResult(String runId, String responseJson) {
        RuntimeException lastFailure = null;
        for (int attempt = 1; attempt <= RESULTS_SEND_MAX_ATTEMPTS; attempt++) {
            try {
                awaitSend(results.send(Record.of(runId, responseJson)));
                return;
            } catch (RuntimeException failure) {
                lastFailure = failure;
                LOG.warnf(
                        failure,
                        "writeback results emit failed (attempt %d/%d, runId=%s)",
                        attempt,
                        RESULTS_SEND_MAX_ATTEMPTS,
                        runId);
                if (attempt < RESULTS_SEND_MAX_ATTEMPTS) {
                    sleep(RESULTS_SEND_BACKOFF_MILLIS);
                }
            }
        }

        LOG.warnf(
                lastFailure,
                "writeback results emit failed after %d attempts (runId=%s), routing response to DLQ",
                RESULTS_SEND_MAX_ATTEMPTS,
                runId);
        sendToDlq(Record.of(runId, responseJson), "writeback results emit retries exhausted (runId=" + runId + ")");
    }

    /**
     * Awaits an {@code Emitter.send(...)}'s {@link CompletionStage}, bounded by {@link
     * #SEND_AWAIT_SECONDS}, converting any failure (an async broker nack surfaced via {@link
     * ExecutionException}, an await timeout, or interruption) into an unchecked {@link
     * RuntimeException} — {@code Emitter.send} itself never throws synchronously for a broker-side
     * nack, since the ack/nack is delivered asynchronously on the returned stage; without this await,
     * such a nack would silently bypass every retry/DLQ path below.
     */
    private static void awaitSend(CompletionStage<Void> send) {
        try {
            send.toCompletableFuture().get(SEND_AWAIT_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted while awaiting emitter send", interrupted);
        } catch (ExecutionException executionFailure) {
            Throwable cause = executionFailure.getCause();
            throw cause instanceof RuntimeException runtimeCause
                    ? runtimeCause
                    : new RuntimeException("emitter send failed", cause != null ? cause : executionFailure);
        } catch (TimeoutException timeout) {
            throw new RuntimeException(
                    "emitter send did not complete within " + SEND_AWAIT_SECONDS + "s", timeout);
        }
    }

    /**
     * Sends a record to the DLQ and awaits its {@link CompletionStage} (see {@link #awaitSend}) —
     * unlike {@link #sendResult}'s results-emit retry loop, a DLQ send has no further fallback queue
     * to route to on failure. If the (awaited) DLQ send itself fails, this logs at ERROR and
     * RETHROWS rather than swallowing the failure: the exception propagates out of {@link #consume},
     * which nacks the inbound Kafka record and lets the broker redeliver it. The inbound message
     * spins until the DLQ recovers — a deliberate no-silent-drop choice (better a stuck message than
     * a lost one) rather than an oversight.
     */
    private void sendToDlq(Record<String, String> record, String context) {
        try {
            awaitSend(dlq.send(record));
        } catch (RuntimeException failure) {
            LOG.errorf(
                    failure,
                    "DLQ send failed (%s) — rethrowing so the inbound record is nacked and redelivered",
                    context);
            throw failure;
        }
    }

    private void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    /**
     * A domain processor's canonical (D15) 5-arg {@code process(request, tenantId, principal,
     * facadeTag, failFastOnInfrastructure)} shape. This consumer always calls it with {@code
     * failFastOnInfrastructure=true} (see {@link #processWithRetry}) — the REST facades use their
     * respective 3-/4-arg convenience overloads instead, which always pass {@code false}.
     */
    @FunctionalInterface
    private interface DomainProcessor<RequestT, ResponseT> {
        ResponseT process(
                RequestT request,
                String tenantId,
                String principal,
                String facadeTag,
                boolean failFastOnInfrastructure);
    }
}
