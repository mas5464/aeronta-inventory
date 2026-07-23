package trax.io.writeback.domain;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * Request to create a single eMRO stock-transfer order (header + line-1 detail) moving {@code
 * qty} of {@code pn} from {@code fromLocation} to {@code toLocation}. Mirrors {@link
 * RequisitionCommand}'s shape; no {@code shadow} field for the same reason (creates are not
 * shadow-able in this slice).
 *
 * @param batch the numeric eMRO batch number (the {@code ORDER_DETAIL.BATCH}/{@code
 *     ORDER_DETAIL_AUDIT.BATCH} columns are {@code NUMBER}) persisted verbatim onto the detail
 *     row's {@code BATCH} column, nullable.
 */
public record TransferCommand(
        String pn,
        String fromLocation,
        String toLocation,
        BigDecimal qty,
        BigDecimal batch,
        LocalDate deliveryDate,
        Provenance provenance) {}
