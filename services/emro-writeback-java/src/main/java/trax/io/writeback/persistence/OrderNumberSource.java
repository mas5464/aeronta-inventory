package trax.io.writeback.persistence;

/**
 * Issues the next eMRO order number for stock-transfer order headers.
 *
 * <p>The real ({@code %prod`/`%dev`/`%test`} CDI-default) implementation, {@link
 * EmroOrderNumberSource}, calls the eMRO sequence package exactly as ARMAC's {@code
 * StockTransferOrderData.getTransactionNo("POSEQ")} does. It is never exercised by the test
 * suite: the {@code %test} profile substitutes an {@code AtomicLong}-backed alternative bean (see
 * {@code src/test/java/.../TestOrderNumberSource.java}) because the eMRO PL/SQL package does not
 * exist in the Dev Services schema.
 */
public interface OrderNumberSource {
    String nextOrderNumber();
}
