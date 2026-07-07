package trax.io.writeback.domain;

public enum ResultStatus {
    ACCEPTED,
    SHADOWED,
    REJECTED_VALIDATION,
    REJECTED_UNKNOWN_KEY,
    SKIPPED_DUPLICATE,
    ERROR
}
