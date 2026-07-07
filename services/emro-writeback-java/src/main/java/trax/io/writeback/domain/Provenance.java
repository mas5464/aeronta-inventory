package trax.io.writeback.domain;

public record Provenance(
    String tenantId,
    String source,
    String runId,
    Long rowId,
    String provenanceId,
    String explicitIdempotencyKey,
    Integer tier,
    String approver,
    String principal
) {
    public String idempotencyKey() {
        if (explicitIdempotencyKey != null && !explicitIdempotencyKey.isBlank()) {
            return explicitIdempotencyKey;
        }
        if (runId != null && !runId.isBlank() && rowId != null) {
            return runId + ":" + rowId;
        }
        throw new IllegalStateException("no idempotency key: need explicitIdempotencyKey or runId+rowId");
    }
}
