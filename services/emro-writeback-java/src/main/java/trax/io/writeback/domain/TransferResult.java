package trax.io.writeback.domain;

/**
 * Outcome of a {@link TransferCreator#createDedup(TransferCommand)} call.
 *
 * @param orderNumber the eMRO transfer order number (as minted by {@link
 *     trax.io.writeback.persistence.OrderNumberSource}) — populated on {@link
 *     ResultStatus#ACCEPTED} (the number this call minted) AND on {@link
 *     ResultStatus#SKIPPED_DUPLICATE} (the ORIGINAL winning call's number, from the ledger's
 *     {@code CREATED_REF}), mirroring {@link RequisitionResult#requisition()}. {@code null} for
 *     rejections/errors.
 * @param batch the caller-supplied {@link TransferCommand#batch()}, echoed back for
 *     confirmation — {@code null} for rejections/errors.
 */
public record TransferResult(
        ResultStatus status, int code, String message, Long rowId, String orderNumber, String batch) {}
