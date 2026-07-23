package trax.io.writeback.domain;

import java.time.Instant;

/**
 * Wire-contract-exact counterpart to {@code trax_io_spine.contracts.RollbackRequest}:
 * {@code (tenant_id, pn, location, reason, principal, requested_at)}.
 */
public record RollbackCommand(
        String tenantId, String pn, String location, String reason, String principal, Instant requestedAt) {
}
