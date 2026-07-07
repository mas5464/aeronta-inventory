package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import org.junit.jupiter.api.Test;

/**
 * Proves both number-source seams resolve to their {@code @Alternative} test beans (never the
 * real eMRO impls, which would fail against the Dev Services schema) and produce distinct,
 * non-blank, correctly-prefixed values on successive calls.
 */
@QuarkusTest
class NumberSourceTest {

    @Inject RequisitionNumberSource requisitionNumberSource;

    @Inject OrderNumberSource orderNumberSource;

    @Test
    void requisition_numbers_are_unique_non_blank_and_prefixed() {
        String first = requisitionNumberSource.nextRequisitionNumber();
        String second = requisitionNumberSource.nextRequisitionNumber();

        assertFalse(first.isBlank());
        assertFalse(second.isBlank());
        assertNotEquals(first, second);
        assertTrue(first.startsWith("RTEST-"));
        assertTrue(second.startsWith("RTEST-"));
    }

    @Test
    void order_numbers_are_unique_non_blank_and_prefixed() {
        String first = orderNumberSource.nextOrderNumber();
        String second = orderNumberSource.nextOrderNumber();

        assertFalse(first.isBlank());
        assertFalse(second.isBlank());
        assertNotEquals(first, second);
        assertTrue(first.startsWith("TTEST-"));
        assertTrue(second.startsWith("TTEST-"));
    }

    @Test
    void test_alternatives_are_resolved_not_the_real_emro_impls() {
        assertTrue(requisitionNumberSource instanceof TestRequisitionNumberSource);
        assertTrue(orderNumberSource instanceof TestOrderNumberSource);
    }
}
