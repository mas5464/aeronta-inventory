package trax.io.writeback.domain;

import java.time.Instant;
import java.util.Map;

public record ItemResult(
    ResultStatus status,
    int code,
    String message,
    Long rowId,
    Map<String, Integer> oldValues,
    Map<String, Integer> newValues,
    Long ledgerVersion,
    Instant writtenAt
) {
}
