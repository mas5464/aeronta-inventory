package trax.io.writeback.persistence;

import java.io.Serializable;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/**
 * The primary key class for the ORDER_HEADER database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderHeaderPK}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderHeaderPK.java}).
 * Four mechanical changes: package renamed, no relationship fields to strip (this class has
 * none), no ARMAC-restricted columns to make writable (this class has none), Lombok
 * {@code @Getter}/{@code @Setter} kept alongside the hand-written {@code equals}/{@code
 * hashCode} verbatim — {@code orderNumber} stays ARMAC's primitive {@code long} (its own {@code
 * getTransactionNo("POSEQ")} always mints a numeric eMRO sequence value); this project's {@link
 * OrderNumberSource} seam mints numeric strings for the same reason {@link RequisitionHeader
 * #requisition} does (see {@code TestOrderNumberSource}'s Javadoc, the T5 precedent set by
 * {@code TestRequisitionNumberSource}).
 */
@Setter
@Getter
@Embeddable
public class OrderHeaderPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column(name = "ORDER_TYPE")
    private String orderType;

    @Column(name = "ORDER_NUMBER")
    private long orderNumber;

    public OrderHeaderPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderHeaderPK)) {
            return false;
        }
        OrderHeaderPK castOther = (OrderHeaderPK) other;
        return this.orderType.equals(castOther.orderType) && (this.orderNumber == castOther.orderNumber);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.orderType.hashCode();
        hash = hash * prime + ((int) (this.orderNumber ^ (this.orderNumber >>> 32)));

        return hash;
    }
}
