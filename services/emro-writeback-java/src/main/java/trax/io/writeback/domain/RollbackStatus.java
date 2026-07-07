package trax.io.writeback.domain;

/** Outcome of a {@link RollbackService#rollback(RollbackCommand)} call. */
public enum RollbackStatus {
    ROLLED_BACK,
    OUTSIDE_WINDOW,
    NOTHING_TO_REVERT
}
