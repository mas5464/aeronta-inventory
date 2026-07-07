package trax.io.writeback.api.batch;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.List;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionBatchRequest;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionBatchResponse;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionItem;
import trax.io.writeback.api.batch.RequisitionDtos.RequisitionRowResult;
import trax.io.writeback.domain.Provenance;
import trax.io.writeback.domain.RequisitionCommand;
import trax.io.writeback.domain.RequisitionCreator;
import trax.io.writeback.domain.RequisitionResult;
import trax.io.writeback.domain.ResultStatus;

/**
 * Shared batch-processing core behind the requisitions REST facade ({@link RequisitionResource})
 * and Task 9's Kafka consumer. Mirrors {@link BatchProcessor} exactly: maps each {@link
 * RequisitionItem} to a {@link RequisitionCommand}, delegates to {@link
 * RequisitionCreator#createDedup(RequisitionCommand)} (the ONLY entry point facades may call),
 * and folds the per-item {@link RequisitionResult}s into a {@link RequisitionBatchResponse}.
 *
 * <p>Wire-safety: when an item comes back {@code ERROR}, the raw exception message is never put
 * on the wire — it is logged with run/row correlation and replaced with a generic message,
 * copying {@link BatchProcessor#toRowResult}'s exact rule.
 *
 * <p>Observability: every processed item increments the {@code writeback.items} counter (tags
 * {@code status}, {@code facade}), and the batch loop is timed by the {@code
 * writeback.batch.duration} timer (tag {@code facade}) — same metric names as {@link
 * BatchProcessor}, tagged {@code facade=requisitions}.
 */
@ApplicationScoped
public class RequisitionProcessor {

    private static final Logger LOG = Logger.getLogger(RequisitionProcessor.class);

    static final String DEFAULT_SOURCE = "optimizer";

    static final String REQUISITIONS_FACADE = "requisitions";

    private static final String METRIC_ITEMS = "writeback.items";
    private static final String METRIC_BATCH_DURATION = "writeback.batch.duration";

    @Inject RequisitionCreator creator;

    @Inject MeterRegistry meterRegistry;

    /**
     * Convenience overload for the REST facade ({@link RequisitionResource}); tags {@code
     * facade=requisitions}.
     */
    public RequisitionBatchResponse process(
            RequisitionBatchRequest request, String tenantId, String principal) {
        return process(request, tenantId, principal, REQUISITIONS_FACADE);
    }

    public RequisitionBatchResponse process(
            RequisitionBatchRequest request, String tenantId, String principal, String facadeTag) {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            List<RequisitionItem> items =
                    request.items() == null ? List.<RequisitionItem>of() : request.items();
            List<RequisitionRowResult> results =
                    items.stream()
                            .map(
                                    item ->
                                            processItem(
                                                    item, request.runId(), tenantId, principal, facadeTag))
                            .toList();
            return new RequisitionBatchResponse(request.runId(), request.transactionId(), results);
        } finally {
            sample.stop(meterRegistry.timer(METRIC_BATCH_DURATION, "facade", facadeTag));
        }
    }

    private RequisitionRowResult processItem(
            RequisitionItem item, String runId, String tenantId, String principal, String facadeTag) {
        RequisitionCommand cmd = toCommand(item, runId, tenantId, principal);
        RequisitionResult result = creator.createDedup(cmd);
        meterRegistry
                .counter(METRIC_ITEMS, "status", result.status().name(), "facade", facadeTag)
                .increment();
        return toRowResult(result, runId);
    }

    private RequisitionCommand toCommand(
            RequisitionItem item, String runId, String tenantId, String principal) {
        String source = item.source() != null ? item.source() : DEFAULT_SOURCE;

        Provenance provenance =
                new Provenance(
                        tenantId,
                        source,
                        runId,
                        item.rowId(),
                        null,
                        null,
                        item.tier(),
                        item.approver(),
                        principal);

        return new RequisitionCommand(
                item.partNo(), item.location(), item.qty(), item.needBy(), item.remarks(), provenance);
    }

    private RequisitionRowResult toRowResult(RequisitionResult result, String runId) {
        String message = result.message();
        if (result.status() == ResultStatus.ERROR) {
            LOG.errorf(
                    "requisition item error (run=%s, row=%s): %s",
                    runId, result.rowId(), result.message());
            message = "internal error (run=" + runId + ", row=" + result.rowId() + ")";
        }
        return new RequisitionRowResult(
                result.rowId(), result.status().name(), result.code(), message, result.requisition(),
                result.line());
    }
}
