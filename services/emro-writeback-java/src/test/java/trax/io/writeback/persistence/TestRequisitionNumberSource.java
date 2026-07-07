package trax.io.writeback.persistence;

import java.util.concurrent.atomic.AtomicLong;

import jakarta.annotation.Priority;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Alternative;

/**
 * Test-only {@link RequisitionNumberSource}: an {@code @Alternative} of higher priority than the
 * {@code @DefaultBean} {@link EmroRequisitionNumberSource}, so CDI always resolves to this bean
 * under {@code @QuarkusTest}. No DDL, no sequence — just a per-JVM {@link AtomicLong} counter,
 * which is sufficient for uniqueness within a single test run since the real {@code REQSEQ}
 * eMRO package does not exist in the Dev Services schema.
 *
 * <p>Mints numeric strings in a {@code 9xx000000}-prefixed range (e.g. {@code "910000001""}),
 * NOT the earlier {@code "RTEST-000001"} alphanumeric form. {@link RequisitionHeader#requisition}
 * is a primitive {@code long} (mirroring ARMAC's own {@code long ReqSeq()}), so the number this
 * source returns must be {@code Long.parseLong}-able — {@link RequisitionCreator} parses it
 * directly into the header's {@code @Id}. The {@code 9xxxxxxxx} range keeps test-minted numbers
 * visually distinct from any real eMRO sequence value without needing a non-numeric prefix.
 */
@Alternative
@Priority(1)
@ApplicationScoped
public class TestRequisitionNumberSource implements RequisitionNumberSource {

    private static final long BASE = 910_000_000L;

    private final AtomicLong counter = new AtomicLong();

    @Override
    public String nextRequisitionNumber() {
        return Long.toString(BASE + counter.incrementAndGet());
    }
}
