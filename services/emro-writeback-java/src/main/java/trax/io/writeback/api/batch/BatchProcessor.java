package trax.io.writeback.api.batch;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.List;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.BatchDtos.BatchItem;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;
import trax.io.writeback.api.batch.BatchDtos.RowResult;
import trax.io.writeback.domain.InfrastructureException;
import trax.io.writeback.domain.ItemResult;
import trax.io.writeback.domain.LevelValues;
import trax.io.writeback.domain.Provenance;
import trax.io.writeback.domain.ResultStatus;
import trax.io.writeback.domain.StockLevelWriter;
import trax.io.writeback.domain.WritebackCommand;

/**
 * Shared batch-processing core behind the PRD REST facade ({@link BatchResource}) and Task 10's
 * Kafka consumer ({@link trax.io.writeback.ingest.WritebackConsumer}). Maps each {@link
 * BatchItem} to a {@link WritebackCommand}, delegates to {@link
 * StockLevelWriter#writeItemDedup(WritebackCommand)} (the ONLY writer entry point facades may
 * call), and folds the per-item {@link ItemResult}s into a {@link BatchResponse}.
 *
 * <p>Wire-safety: when an item comes back {@code ERROR}, the raw exception message is never put
 * on the wire — it is logged with run/row correlation and replaced with a generic message.
 *
 * <p>Observability (Task 11): every processed item increments the {@code writeback.items} counter
 * (tags {@code status}, {@code facade}), and the batch loop is timed by the {@code
 * writeback.batch.duration} timer (tag {@code facade}).
 */
@ApplicationScoped
public class BatchProcessor {

    private static final Logger LOG = Logger.getLogger(BatchProcessor.class);

    static final String DEFAULT_SOURCE = "optimizer";

    static final String BATCH_FACADE = "batch";

    private static final String METRIC_ITEMS = "writeback.items";
    private static final String METRIC_BATCH_DURATION = "writeback.batch.duration";

    @Inject StockLevelWriter writer;

    @Inject MeterRegistry meterRegistry;

    /** Convenience overload for the PRD REST facade ({@link BatchResource}); tags {@code facade=batch}. */
    public BatchResponse process(BatchRequest request, String tenantId, String principal) {
        return process(request, tenantId, principal, BATCH_FACADE);
    }

    /**
     * Convenience overload preserving the pre-D15 4-arg shape: never fails fast on an {@link
     * InfrastructureException} — it is caught per item and folded to a per-row {@code ERROR}
     * result, exactly as before. REST facades (direct or via the 3-arg overload above) always go
     * through this path, so REST responses stay byte-identical to before D15.
     */
    public BatchResponse process(
            BatchRequest request, String tenantId, String principal, String facadeTag) {
        return process(request, tenantId, principal, facadeTag, false);
    }

    /**
     * Canonical entry point (D15): {@code failFastOnInfrastructure=true} (used only by {@link
     * trax.io.writeback.ingest.WritebackConsumer}) lets an {@link InfrastructureException} from
     * {@link StockLevelWriter#writeItemDedup} propagate out of this method instead of being folded
     * to a per-row {@code ERROR}, so the consumer's batch-level retry→DLQ loop can react to it.
     */
    public BatchResponse process(
            BatchRequest request, String tenantId, String principal, String facadeTag,
            boolean failFastOnInfrastructure) {
        Timer.Sample sample = Timer.start(meterRegistry);
        try {
            List<BatchItem> items = request.items() == null ? List.<BatchItem>of() : request.items();
            List<RowResult> results =
                    items.stream()
                            .map(
                                    item ->
                                            processItem(
                                                    item,
                                                    request.runId(),
                                                    tenantId,
                                                    principal,
                                                    facadeTag,
                                                    failFastOnInfrastructure))
                            .toList();
            return new BatchResponse(request.runId(), request.transactionId(), results);
        } finally {
            sample.stop(meterRegistry.timer(METRIC_BATCH_DURATION, "facade", facadeTag));
        }
    }

    private RowResult processItem(
            BatchItem item,
            String runId,
            String tenantId,
            String principal,
            String facadeTag,
            boolean failFastOnInfrastructure) {
        WritebackCommand cmd = toCommand(item, runId, tenantId, principal);
        ItemResult result;
        try {
            result = writer.writeItemDedup(cmd);
        } catch (InfrastructureException e) {
            if (failFastOnInfrastructure) {
                throw e;
            }
            result = infrastructureErrorResult(e, cmd.provenance().rowId());
        }
        meterRegistry.counter(METRIC_ITEMS, "status", result.status().name(), "facade", facadeTag).increment();
        return toRowResult(result, runId);
    }

    /**
     * Folds an {@link InfrastructureException} into the same shape {@link
     * StockLevelWriter#writeItemDedup} would have produced pre-D15 — an {@code ERROR} result with
     * {@code code=500} (the invariant enforced by the writer's own {@code codeFor}) — so the REST
     * facade's response is unchanged.
     */
    private static ItemResult infrastructureErrorResult(InfrastructureException e, Long rowId) {
        return new ItemResult(ResultStatus.ERROR, 500, e.getMessage(), rowId, null, null, null, null, null);
    }

    private WritebackCommand toCommand(BatchItem item, String runId, String tenantId, String principal) {
        LevelValues levels =
                new LevelValues(
                        item.reorderLevel(),
                        item.eoqLevel(),
                        item.stockMin(),
                        item.stockMax(),
                        item.orderMin(),
                        item.orderMax(),
                        item.replenishmentLeadTime());

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

        return new WritebackCommand(item.partNo(), item.location(), levels, provenance, false);
    }

    private RowResult toRowResult(ItemResult result, String runId) {
        String message =
                WireSanitizer.sanitize(
                        LOG, "writeback item", result.status(), result.message(), runId, result.rowId());
        return new RowResult(result.rowId(), result.status().name(), result.code(), message);
    }
}
