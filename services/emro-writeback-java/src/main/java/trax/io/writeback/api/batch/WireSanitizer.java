package trax.io.writeback.api.batch;

import org.jboss.logging.Logger;
import trax.io.writeback.domain.ResultStatus;

/**
 * Single source of truth for the wire-sanitization rule shared by {@link BatchProcessor} and
 * {@link RequisitionProcessor}: when a row comes back {@code ERROR}, the raw exception message is
 * never put on the wire — it is logged once with run/row correlation and replaced with a generic
 * {@code "internal error (run=..., row=...)"} message. Every other status passes the writer's
 * message through unchanged.
 */
final class WireSanitizer {

    private WireSanitizer() {}

    /**
     * Returns the wire-safe message for a row result, logging the raw message (with {@code
     * logPrefix} + run/row correlation) when {@code status} is {@link ResultStatus#ERROR}.
     *
     * @param log the caller's logger (kept distinct per facade so log lines are attributable)
     * @param logPrefix short description of the row kind, e.g. {@code "writeback item"} or {@code
     *     "requisition item"}
     */
    static String sanitize(
            Logger log,
            String logPrefix,
            ResultStatus status,
            String rawMessage,
            String runId,
            Long rowId) {
        if (status != ResultStatus.ERROR) {
            return rawMessage;
        }
        log.errorf("%s error (run=%s, row=%s): %s", logPrefix, runId, rowId, rawMessage);
        return "internal error (run=" + runId + ", row=" + rowId + ")";
    }
}
