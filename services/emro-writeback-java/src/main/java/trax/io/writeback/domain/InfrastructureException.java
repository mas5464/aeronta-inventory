package trax.io.writeback.domain;

import java.sql.SQLNonTransientConnectionException;
import java.sql.SQLRecoverableException;
import java.sql.SQLTransientException;

/**
 * Unchecked exception marking a connection-class infrastructure failure (DB down, network
 * partition, connection-pool exhaustion) as distinct from an application-level failure (a
 * constraint violation, a validation rejection, or any other unexpected persistence error).
 *
 * <p>Thrown ONLY by the three domain dedup wrappers — {@link
 * StockLevelWriter#writeItemDedup(WritebackCommand)}, {@link
 * RequisitionCreator#createDedup(RequisitionCommand)}, {@link
 * TransferCreator#createDedup(TransferCommand)} — when {@link #isInfrastructureFailure(Throwable)}
 * positively identifies the underlying cause chain. Every other failure keeps those wrappers'
 * existing behavior of folding to a per-item {@code ERROR} result rather than throwing.
 *
 * <p>This split is what makes the Kafka retry path (spec D15) reachable: previously every failure
 * — including a DB outage — was folded into a per-row {@code ERROR} result by the dedup wrappers,
 * so {@link trax.io.writeback.ingest.WritebackConsumer#processWithRetry} never saw an exception to
 * retry. The two call sites now diverge:
 *
 * <ul>
 *   <li><b>REST facades</b> ({@code BatchProcessor}/{@code RequisitionProcessor}/{@code
 *       TransferProcessor}, invoked with {@code failFastOnInfrastructure=false}) catch this
 *       exception per item and fold it to the same per-row {@code ERROR} result any other failure
 *       would have produced — REST responses are byte-identical to before this change.
 *   <li><b>The Kafka consumer</b> (invoked with {@code failFastOnInfrastructure=true}) lets this
 *       exception propagate out of {@code process(...)}, where {@code processWithRetry}'s existing
 *       3-attempt backoff loop catches it, retries, and routes to the DLQ on exhaustion.
 * </ul>
 */
public class InfrastructureException extends RuntimeException {

    private static final String AGROAL_ACQUISITION_TIMEOUT_SIMPLE_NAME = "AcquisitionTimeoutException";

    public InfrastructureException(String message, Throwable cause) {
        super(message, cause);
    }

    /**
     * True when {@code e}'s cause chain contains a connection-class JDBC failure: a {@link
     * SQLTransientException}, a {@link SQLNonTransientConnectionException}, a {@link
     * SQLRecoverableException}, or an Agroal connection-pool acquisition-timeout failure.
     *
     * <p>The Agroal case is matched by the cause's simple class name (deliberately not {@code
     * instanceof io.agroal.api.exceptionsort.AcquisitionTimeoutException}) so this module does not
     * need a compile-time dependency on the Agroal artifact just to classify one exception type —
     * Quarkus's default datasource already pulls Agroal in transitively at runtime, but naming the
     * type directly here would be an unnecessary coupling.
     *
     * <p>Deliberately does NOT match {@link java.sql.SQLIntegrityConstraintViolationException} (a
     * constraint violation — handled by {@link StockLevelWriter#classifyConstraintViolation}) or
     * any other application-level failure (validation, {@link IllegalStateException}, etc.) — those
     * keep the existing per-item {@code ERROR} fold.
     */
    public static boolean isInfrastructureFailure(Throwable e) {
        Throwable cause = e;
        while (cause != null) {
            if (cause instanceof SQLTransientException
                    || cause instanceof SQLNonTransientConnectionException
                    || cause instanceof SQLRecoverableException
                    || AGROAL_ACQUISITION_TIMEOUT_SIMPLE_NAME.equals(cause.getClass().getSimpleName())) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }
}
