package trax.io.writeback.domain;

import java.time.Instant;
import java.util.Map;

/**
 * Wire-contract-exact counterpart to {@code trax_io_spine.contracts.RollbackResult}:
 * {@code (tenant_id, pn, location, status, from_values, to_values, reverted_from_version,
 * new_version, rolled_back_at, error_message)} — tenant/pn/location are carried by the
 * caller-supplied {@link RollbackCommand} rather than duplicated here; the facade re-attaches
 * them when building the wire DTO.
 */
public record RollbackOutcome(
        RollbackStatus status,
        Map<String, Integer> fromValues,
        Map<String, Integer> toValues,
        Long revertedFromVersion,
        Long newVersion,
        Instant rolledBackAt,
        String errorMessage) {

    static RollbackOutcome nothingToRevert() {
        return new RollbackOutcome(RollbackStatus.NOTHING_TO_REVERT, null, null, null, null, null, null);
    }

    static RollbackOutcome nothingToRevert(String errorMessage) {
        return new RollbackOutcome(RollbackStatus.NOTHING_TO_REVERT, null, null, null, null, null, errorMessage);
    }

    static RollbackOutcome outsideWindow() {
        return new RollbackOutcome(RollbackStatus.OUTSIDE_WINDOW, null, null, null, null, null, null);
    }
}
