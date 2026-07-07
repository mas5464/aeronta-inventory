package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
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
        // Numeric (9xx-prefixed), not "RTEST-..." — RequisitionHeader.requisition is a long PK,
        // so the number source must return a Long.parseLong-able string (see
        // TestRequisitionNumberSource's Javadoc for why).
        assertTrue(first.startsWith("9"));
        assertTrue(second.startsWith("9"));
        assertDoesNotThrow(() -> Long.parseLong(first));
        assertDoesNotThrow(() -> Long.parseLong(second));
    }

    @Test
    void order_numbers_are_unique_non_blank_and_prefixed() {
        String first = orderNumberSource.nextOrderNumber();
        String second = orderNumberSource.nextOrderNumber();

        assertFalse(first.isBlank());
        assertFalse(second.isBlank());
        assertNotEquals(first, second);
        // Numeric (92x-prefixed), not "TTEST-..." — OrderHeaderPK.orderNumber is a long PK, so
        // the number source must return a Long.parseLong-able string (see
        // TestOrderNumberSource's Javadoc for why).
        assertTrue(first.startsWith("92"));
        assertTrue(second.startsWith("92"));
        assertDoesNotThrow(() -> Long.parseLong(first));
        assertDoesNotThrow(() -> Long.parseLong(second));
    }

    @Test
    void test_alternatives_are_resolved_not_the_real_emro_impls() {
        assertTrue(requisitionNumberSource instanceof TestRequisitionNumberSource);
        assertTrue(orderNumberSource instanceof TestOrderNumberSource);
    }
}
