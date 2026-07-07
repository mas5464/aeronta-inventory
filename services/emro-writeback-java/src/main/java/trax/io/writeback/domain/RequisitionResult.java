package trax.io.writeback.domain;

/**
 * Outcome of a {@link RequisitionCreator#createDedup(RequisitionCommand)} call.
 *
 * @param requisition the eMRO requisition number (as minted by {@code RequisitionNumberSource}) —
 *     populated on {@link ResultStatus#ACCEPTED} (the number this call minted) AND on {@link
 *     ResultStatus#SKIPPED_DUPLICATE} (the ORIGINAL winning call's number, from the ledger's
 *     {@code CREATED_REF}), so a replaying caller always learns which requisition exists. {@code
 *     null} for rejections/errors.
 * @param line the created detail line number — always {@code 1} for a successful create (this
 *     slice creates exactly one detail line per requisition); {@code null} otherwise.
 */
public record RequisitionResult(
        ResultStatus status, int code, String message, Long rowId, String requisition, Integer line) {}
