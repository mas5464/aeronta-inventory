package trax.io.writeback.ingest;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Alternative;
import java.sql.SQLTransientException;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;
import trax.io.writeback.api.batch.BatchProcessor;
import trax.io.writeback.domain.InfrastructureException;

/**
 * Test-only {@code @Alternative} for {@link BatchProcessor}, used ONLY by {@link
 * WritebackConsumerFailFastTest} (via {@code quarkus.arc.selected-alternatives}, NOT {@code
 * @Priority} — see that class's {@code FailFastProfile} — so it is scoped to that one dedicated
 * test app instance and never affects any other test, including {@code BatchResourceTest} and
 * {@link WritebackConsumerTest}).
 *
 * <p>Forcing a real Oracle connection outage against the Dev Services container isn't practical in
 * a test, so this bean stands in for one: it unconditionally throws an {@link
 * InfrastructureException} wrapping a {@link SQLTransientException}, simulating exactly what {@link
 * trax.io.writeback.domain.StockLevelWriter#writeItemDedup} would throw if the DB were down. This
 * pins the Kafka fail-fast path (D15) end-to-end: {@link WritebackConsumer#processWithRetry} must
 * retry 3 times, then route the raw request payload to the DLQ.
 */
@Alternative
@ApplicationScoped
public class FailFastBatchProcessor extends BatchProcessor {

    @Override
    public BatchResponse process(
            BatchRequest request,
            String tenantId,
            String principal,
            String facadeTag,
            boolean failFastOnInfrastructure) {
        throw new InfrastructureException(
                "simulated DB outage", new SQLTransientException("simulated connection loss"));
    }
}
