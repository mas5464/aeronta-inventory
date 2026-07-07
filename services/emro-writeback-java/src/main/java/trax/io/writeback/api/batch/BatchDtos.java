package trax.io.writeback.api.batch;

import java.math.BigDecimal;
import java.util.List;

/** JSON wire records for the PRD batch REST facade (POST /api/v1/stock-levels). */
public final class BatchDtos {

    private BatchDtos() {}

    /**
     * @param tenantId Optional. Populated for Kafka-ingested batches (Task 10); the REST facade
     *     ({@link BatchResource}) ignores it and always uses the JWT {@code tenant_id} claim. A
     *     JSON body without this field still deserializes (records + Jackson: missing -&gt; null).
     */
    public record BatchRequest(
            String runId, String transactionId, List<BatchItem> items, String tenantId) {}

    public record BatchItem(
            Long rowId,
            String partNo,
            String location,
            BigDecimal reorderLevel,
            BigDecimal eoqLevel,
            BigDecimal stockMin,
            BigDecimal stockMax,
            BigDecimal orderMin,
            BigDecimal orderMax,
            BigDecimal replenishmentLeadTime,
            String source,
            String approver,
            Integer tier) {}

    public record BatchResponse(String runId, String transactionId, List<RowResult> results) {}

    public record RowResult(Long rowId, String status, int code, String message) {}
}
