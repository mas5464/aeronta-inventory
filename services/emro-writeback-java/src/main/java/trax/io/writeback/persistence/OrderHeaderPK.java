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
 * hashCode} verbatim.
 *
 * <p><b>Deviation from ARMAC (type widen, not a missing column):</b> ARMAC's {@code orderNumber}
 * is a primitive {@code long} (its {@code getTransactionNo("POSEQ")} always mints a numeric
 * eMRO sequence value). This project's {@link OrderNumberSource} seam (established in Task 4)
 * is contractually {@code String}-typed, and its test double, {@code TestOrderNumberSource},
 * mints {@code "TTEST-######"} — a non-numeric, prefixed value (see {@code NumberSourceTest
 * .order_numbers_are_unique_non_blank_and_prefixed}, a pre-existing passing test this class must
 * not break). Unlike {@code RequisitionHeader.requisition} (whose test source, {@code
 * TestRequisitionNumberSource}, was fixed in Task 5 to mint {@code Long.parseLong}-able numeric
 * strings so it could stay a primitive {@code long}), the order-number seam was never given that
 * treatment and has an existing test locked to the alphanumeric format. Rather than parse a
 * non-numeric string as a long (which would throw), {@code orderNumber} is widened to {@code
 * String} here — a verbatim ARMAC column name, differently typed to match what the actual seam
 * produces.
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
    private String orderNumber;

    public OrderHeaderPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderHeaderPK)) {
            return false;
        }
        OrderHeaderPK castOther = (OrderHeaderPK) other;
        return this.orderType.equals(castOther.orderType) && this.orderNumber.equals(castOther.orderNumber);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.orderType.hashCode();
        hash = hash * prime + this.orderNumber.hashCode();

        return hash;
    }
}
