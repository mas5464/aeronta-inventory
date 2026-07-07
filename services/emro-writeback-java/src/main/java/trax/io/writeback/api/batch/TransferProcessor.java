package trax.io.writeback.api.batch;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.List;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchRequest;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchResponse;
import trax.io.writeback.api.batch.TransferDtos.TransferItem;
import trax.io.writeback.api.batch.TransferDtos.TransferRowResult;
import trax.io.writeback.domain.Provenance;
import trax.io.writeback.domain.TransferCommand;
import trax.io.writeback.domain.TransferCreator;
import trax.io.writeback.domain.TransferResult;

/**
 * Shared batch-processing core behind the transfers REST facade ({@link TransferResource}) and
 * Task 9's Kafka consumer. Mirrors {@link RequisitionProcessor} exactly: maps each {@link
 * TransferItem} to a {@link TransferCommand}, delegates to {@link
 * TransferCreator#createDedup(TransferCommand)} (the ONLY entry point facades may call), and folds
 * the per-item {@link TransferResult}s into a {@link TransferBatchResponse}.
 *
 * <p>Wire-safety: when an item comes back {@code ERROR}, the raw exception message is never put
 * on the wire — it is logged with run/row correlation and replaced with a generic message, via the
 * shared {@link WireSanitizer} used by {@link BatchProcessor} and {@link RequisitionProcessor}
 * too.
 *
 * <p>Observability: every processed item increments the {@code writeback.items} counter (tags
 * {@code status}, {@code facade}), and the batch loop is timed by the {@code
 * writeback.batch.duration} timer (tag {@code facade}) — same metric names as {@link
 * BatchProcessor}, tagged {@code facade=transfers}.
 */
@ApplicationScoped
public class TransferProcessor {

    private static final Logger LOG = Logger.getLogger(TransferProcessor.class);

    static final String DEFAULT_SOURCE = "optimizer";

    static final String TRANSFERS_FACADE = "transfers";

    private static final String METRIC_ITEMS = "writeback.items";
    private static final String METRIC_BATCH_DURATION = "writeback.batch.duration";

    @Inject TransferCreator creator;

    @Inject MeterRegistry meterRegistry;

    /**
     * Convenience overload for the REST facade ({@link TransferResource}); tags {@code
     * facade=transfers}.
     */
    public TransferBatchResponse process(TransferBatchRequest request, String tenantId, String principal) {
        return process(request, tenantId, principal, TRANSFERS_FACADE);
    }

    public TransferBatchResponse process(
            TransferBatchRequest request, String tenantId, String principal, String facadeTag) {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            List<TransferItem> items = request.items() == null ? List.<TransferItem>of() : request.items();
            List<TransferRowResult> results =
                    items.stream()
                            .map(item -> processItem(item, request.runId(), tenantId, principal, facadeTag))
                            .toList();
            return new TransferBatchResponse(request.runId(), request.transactionId(), results);
        } finally {
            sample.stop(meterRegistry.timer(METRIC_BATCH_DURATION, "facade", facadeTag));
        }
    }

    private TransferRowResult processItem(
            TransferItem item, String runId, String tenantId, String principal, String facadeTag) {
        TransferCommand cmd = toCommand(item, runId, tenantId, principal);
        TransferResult result = creator.createDedup(cmd);
        meterRegistry
                .counter(METRIC_ITEMS, "status", result.status().name(), "facade", facadeTag)
                .increment();
        return toRowResult(result, runId);
    }

    private TransferCommand toCommand(TransferItem item, String runId, String tenantId, String principal) {
        String source = item.source() != null ? item.source() : DEFAULT_SOURCE;

        Provenance provenance =
                new Provenance(
                        tenantId, source, runId, item.rowId(), null, null, item.tier(), item.approver(), principal);

        return new TransferCommand(
                item.partNo(),
                item.fromLocation(),
                item.toLocation(),
                item.qty(),
                item.batch(),
                item.deliveryDate(),
                provenance);
    }

    private TransferRowResult toRowResult(TransferResult result, String runId) {
        String message =
                WireSanitizer.sanitize(
                        LOG, "transfer item", result.status(), result.message(), runId, result.rowId());
        return new TransferRowResult(
                result.rowId(), result.status().name(), result.code(), message, result.orderNumber(),
                result.batch());
    }
}
