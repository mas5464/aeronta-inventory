package trax.io.writeback.domain;

import java.math.BigDecimal;
import java.math.RoundingMode;

public final class NumericPolicy {
    private NumericPolicy() {}
    public static BigDecimal apply(BigDecimal value, boolean consumable) {
        if (value == null || consumable) return value;
        return value.setScale(0, RoundingMode.DOWN);
    }
}
