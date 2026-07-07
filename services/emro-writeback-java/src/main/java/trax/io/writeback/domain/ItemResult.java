package trax.io.writeback.domain;

import java.time.Instant;
import java.util.Map;

/**
 * @param originalStatus For a {@link ResultStatus#SKIPPED_DUPLICATE} result only: the outcome the
 *     ORIGINAL (winning) write actually produced — {@link ResultStatus#ACCEPTED} or {@link
 *     ResultStatus#SHADOWED} — derived from the ledger row's {@code OUTCOME} column. {@code null}
 *     for every other status. Facades that replay a duplicate to the caller (e.g. the Trax IO REST
 *     facade) need this to report "written" vs. "shadowed" faithfully rather than always assuming
 *     the original was a real write.
 */
public record ItemResult(
    ResultStatus status,
    int code,
    String message,
    Long rowId,
    Map<String, Integer> oldValues,
    Map<String, Integer> newValues,
    Long ledgerVersion,
    Instant writtenAt,
    ResultStatus originalStatus
) {
}
