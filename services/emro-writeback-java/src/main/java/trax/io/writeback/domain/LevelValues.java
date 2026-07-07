package trax.io.writeback.domain;

import java.math.BigDecimal;

public record LevelValues(
    BigDecimal reorderLevel,
    BigDecimal eoqLevel,
    BigDecimal stockMin,
    BigDecimal stockMax,
    BigDecimal orderMin,
    BigDecimal orderMax,
    BigDecimal replenishmentLeadTime
) {
}
