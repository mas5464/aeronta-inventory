package trax.io.writeback.api.batch;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import java.util.List;
import org.jboss.logging.Logger;
import trax.io.writeback.api.batch.BatchDtos.BatchItem;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;
import trax.io.writeback.api.batch.BatchDtos.RowResult;
import trax.io.writeback.domain.ItemResult;
import trax.io.writeback.domain.LevelValues;
import trax.io.writeback.domain.Provenance;
import trax.io.writeback.domain.ResultStatus;
import trax.io.writeback.domain.StockLevelWriter;
import trax.io.writeback.domain.WritebackCommand;

/**
 * Shared batch-processing core behind the PRD REST facade ({@link BatchResource}) and, later,
 * Task 10's Kafka consumer. Maps each {@link BatchItem} to a {@link WritebackCommand}, delegates
 * to {@link StockLevelWriter#writeItemDedup(WritebackCommand)} (the ONLY writer entry point
 * facades may call), and folds the per-item {@link ItemResult}s into a {@link BatchResponse}.
 *
 * <p>Wire-safety: when an item comes back {@code ERROR}, the raw exception message is never put
 * on the wire — it is logged with run/row correlation and replaced with a generic message.
 */
@ApplicationScoped
public class BatchProcessor {

    private static final Logger LOG = Logger.getLogger(BatchProcessor.class);

    static final String DEFAULT_SOURCE = "optimizer";

    @Inject StockLevelWriter writer;

    public BatchResponse process(BatchRequest request, String tenantId, String principal) {
        List<BatchItem> items = request.items() == null ? List.<BatchItem>of() : request.items();
        List<RowResult> results =
                items.stream()
                        .map(item -> processItem(item, request.runId(), tenantId, principal))
                        .toList();
        return new BatchResponse(request.runId(), request.transactionId(), results);
    }

    private RowResult processItem(BatchItem item, String runId, String tenantId, String principal) {
        WritebackCommand cmd = toCommand(item, runId, tenantId, principal);
        ItemResult result = writer.writeItemDedup(cmd);
        return toRowResult(result, runId);
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
        String message = result.message();
        if (result.status() == ResultStatus.ERROR) {
            LOG.errorf(
                    "writeback item error (run=%s, row=%s): %s",
                    runId, result.rowId(), result.message());
            message = "internal error (run=" + runId + ", row=" + result.rowId() + ")";
        }
        return new RowResult(result.rowId(), result.status().name(), result.code(), message);
    }
}
