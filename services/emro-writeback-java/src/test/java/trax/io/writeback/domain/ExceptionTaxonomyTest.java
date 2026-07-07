package trax.io.writeback.domain;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.SQLIntegrityConstraintViolationException;
import java.sql.SQLNonTransientConnectionException;
import java.sql.SQLRecoverableException;
import java.sql.SQLTransientException;
import org.junit.jupiter.api.Test;

/**
 * Direct (non-{@code @QuarkusTest}, no Dev Services) unit tests for {@link
 * InfrastructureException#isInfrastructureFailure(Throwable)} — the D15 classifier that decides
 * whether a dedup wrapper's caught exception is a connection-class infrastructure failure (rethrow
 * as {@link InfrastructureException}) or anything else (keep the existing per-item {@code ERROR}
 * fold).
 *
 * <p>The classifier is exercised directly against hand-built exception chains rather than a real
 * DB outage — forcing an actual Oracle connection failure against the Dev Services container isn't
 * practical in a unit test, and the classifier's contract only depends on the exception TYPE chain,
 * not how it was produced.
 */
class ExceptionTaxonomyTest {

    @Test
    void sql_transient_exception_in_chain_is_infrastructure() {
        Exception e = new RuntimeException("wrapped", new SQLTransientException("connection lost"));
        assertTrue(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void sql_non_transient_connection_exception_in_chain_is_infrastructure() {
        Exception e =
                new RuntimeException(
                        "wrapped", new SQLNonTransientConnectionException("connection refused"));
        assertTrue(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void sql_recoverable_exception_in_chain_is_infrastructure() {
        Exception e = new RuntimeException("wrapped", new SQLRecoverableException("try again"));
        assertTrue(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void agroal_acquisition_timeout_matched_by_simple_class_name_is_infrastructure() {
        // Stand-in for io.agroal.api.exceptionsort.AcquisitionTimeoutException: the classifier
        // matches by simple class name (see InfrastructureException's Javadoc for why this module
        // avoids a hard compile dependency on the Agroal artifact just for this one check).
        Exception e = new RuntimeException("wrapped", new AcquisitionTimeoutException("pool exhausted"));
        assertTrue(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void deeply_nested_infrastructure_cause_is_still_found() {
        Exception e =
                new RuntimeException(
                        "outer", new RuntimeException("middle", new SQLTransientException("connection lost")));
        assertTrue(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void constraint_violation_is_not_infrastructure() {
        Exception e =
                new SQLIntegrityConstraintViolationException(
                        "ORA-00001: unique constraint (SCHEMA.UQ_WRITEBACK_IDEMPOTENCY) violated");
        assertFalse(InfrastructureException.isInfrastructureFailure(e));
    }

    @Test
    void illegal_state_exception_is_not_infrastructure() {
        assertFalse(InfrastructureException.isInfrastructureFailure(new IllegalStateException("boom")));
    }

    @Test
    void plain_validation_style_runtime_exception_is_not_infrastructure() {
        assertFalse(InfrastructureException.isInfrastructureFailure(new RuntimeException("qty must be > 0")));
    }

    /** Minimal stand-in for {@code io.agroal.api.exceptionsort.AcquisitionTimeoutException}. */
    private static final class AcquisitionTimeoutException extends RuntimeException {
        AcquisitionTimeoutException(String message) {
            super(message);
        }
    }
}
