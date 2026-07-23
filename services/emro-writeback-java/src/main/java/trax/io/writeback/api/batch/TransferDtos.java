package trax.io.writeback.api.batch;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** JSON wire records for the transfers batch REST facade (POST /api/v1/transfers). */
public final class TransferDtos {

    private TransferDtos() {}

    /**
     * @param tenantId Optional. Populated for Kafka-ingested batches (Task 9); the REST facade
     *     ({@link TransferResource}) ignores it and always uses the JWT {@code tenant_id} claim. A
     *     JSON body without this field still deserializes (records + Jackson: missing -> null).
     */
    public record TransferBatchRequest(
            String runId, String transactionId, List<TransferItem> items, String tenantId) {}

    public record TransferItem(
            Long rowId,
            String partNo,
            String fromLocation,
            String toLocation,
            BigDecimal qty,
            BigDecimal batch,
            LocalDate deliveryDate,
            String source,
            Integer tier,
            String approver) {}

    public record TransferBatchResponse(String runId, String transactionId, List<TransferRowResult> results) {}

    public record TransferRowResult(
            Long rowId, String status, int code, String message, String orderNumber, String batch) {}
}
