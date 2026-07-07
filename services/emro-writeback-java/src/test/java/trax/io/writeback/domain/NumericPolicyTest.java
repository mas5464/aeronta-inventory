package trax.io.writeback.domain;

import org.junit.jupiter.api.Test;
import java.math.BigDecimal;
import static org.junit.jupiter.api.Assertions.*;

class NumericPolicyTest {
    @Test void consumable_keeps_decimals() {
        assertEquals(new BigDecimal("5.7"), NumericPolicy.apply(new BigDecimal("5.7"), true));
    }
    @Test void non_consumable_truncates_toward_zero() {
        assertEquals(new BigDecimal("5"), NumericPolicy.apply(new BigDecimal("5.7"), false));
    }
    @Test void null_passes_through() {
        assertNull(NumericPolicy.apply(null, false));
    }
}
