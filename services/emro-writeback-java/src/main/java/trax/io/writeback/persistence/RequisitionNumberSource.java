package trax.io.writeback.persistence;

/**
 * Issues the next eMRO requisition number for {@code REQUISITION_HEADER.REQUISITION}.
 *
 * <p>The real ({@code %prod`/`%dev`/`%test`} CDI-default) implementation, {@link
 * EmroRequisitionNumberSource}, calls the eMRO sequence package exactly as ARMAC's {@code
 * RequisitionData.ReqSeq()} does. It is never exercised by the test suite: the {@code %test}
 * profile substitutes an {@code AtomicLong}-backed alternative bean (see {@code
 * src/test/java/.../TestRequisitionNumberSource.java}) because the eMRO PL/SQL package does not
 * exist in the Dev Services schema.
 */
public interface RequisitionNumberSource {
    String nextRequisitionNumber();
}
