package trax.io.writeback.domain;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * Request to create a single eMRO requisition (header + line-1 detail) for {@code (pn,
 * location)}. Mirrors {@link WritebackCommand}'s shape but has no {@code shadow} field: shadow
 * mode is a stock-level-writer concept only (see {@link RequisitionCreator} Javadoc) — creates
 * are not shadow-able in this slice.
 */
public record RequisitionCommand(
        String pn, String location, BigDecimal qty, LocalDate needBy, String remarks, Provenance provenance) {}
