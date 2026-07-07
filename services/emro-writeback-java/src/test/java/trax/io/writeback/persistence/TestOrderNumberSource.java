package trax.io.writeback.persistence;

import java.util.concurrent.atomic.AtomicLong;

import jakarta.annotation.Priority;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Alternative;

/**
 * Test-only {@link OrderNumberSource}: an {@code @Alternative} of higher priority than the {@code
 * @DefaultBean} {@link EmroOrderNumberSource}, so CDI always resolves to this bean under {@code
 * @QuarkusTest}. No DDL, no sequence — just a per-JVM {@link AtomicLong} counter, which is
 * sufficient for uniqueness within a single test run since the real {@code POSEQ} eMRO package
 * does not exist in the Dev Services schema.
 */
@Alternative
@Priority(1)
@ApplicationScoped
public class TestOrderNumberSource implements OrderNumberSource {

    private final AtomicLong counter = new AtomicLong();

    @Override
    public String nextOrderNumber() {
        return String.format("TTEST-%06d", counter.incrementAndGet());
    }
}
