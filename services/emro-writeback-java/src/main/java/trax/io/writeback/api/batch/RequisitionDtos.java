package trax.io.writeback.api.batch;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** JSON wire records for the requisitions batch REST facade (POST /api/v1/requisitions). */
public final class RequisitionDtos {

    private RequisitionDtos() {}

    /**
     * @param tenantId Optional. Populated for Kafka-ingested batches (Task 9); the REST facade
     *     ({@link RequisitionResource}) ignores it and always uses the JWT {@code tenant_id}
     *     claim. A JSON body without this field still deserializes (records + Jackson: missing ->
     *     null).
     */
    public record RequisitionBatchRequest(
            String runId, String transactionId, List<RequisitionItem> items, String tenantId) {}

    public record RequisitionItem(
            Long rowId,
            String partNo,
            String location,
            BigDecimal qty,
            LocalDate needBy,
            String remarks,
            String source,
            Integer tier,
            String approver) {}

    public record RequisitionBatchResponse(
            String runId, String transactionId, List<RequisitionRowResult> results) {}

    public record RequisitionRowResult(
            Long rowId, String status, int code, String message, String requisition, Integer line) {}
}
