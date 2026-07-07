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
 *
 * <p>Mints numeric strings in a {@code 92x000000}-prefixed range (e.g. {@code "920000001"}), NOT
 * an earlier {@code "TTEST-000001"} alphanumeric form. {@link OrderHeaderPK#getOrderNumber()} is
 * a primitive {@code long} (mirroring ARMAC's own {@code long getTransactionNo("POSEQ")}), so the
 * number this source returns must be {@code Long.parseLong}-able — {@link
 * trax.io.writeback.domain.TransferCreator} parses it directly into the header's {@code @Id}.
 * The {@code 92xxxxxxx} range keeps test-minted order numbers visually distinct from both real
 * eMRO sequence values and {@link TestRequisitionNumberSource}'s {@code 91xxxxxxx} requisition
 * range, without needing a non-numeric prefix (mirrors {@link TestRequisitionNumberSource}'s
 * rationale verbatim).
 */
@Alternative
@Priority(1)
@ApplicationScoped
public class TestOrderNumberSource implements OrderNumberSource {

    private static final long BASE = 920_000_000L;

    private final AtomicLong counter = new AtomicLong();

    @Override
    public String nextOrderNumber() {
        return Long.toString(BASE + counter.incrementAndGet());
    }
}
