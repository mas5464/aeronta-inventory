package trax.io.writeback.persistence;

import java.io.Serializable;
import java.util.Date;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/**
 * The primary key class for the ORDER_HEADER_AUDIT database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderHeaderAuditPK}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderHeaderAuditPK.java}).
 * Four mechanical changes: package renamed; no relationship fields to strip; no ARMAC-restricted
 * columns (this class has none); Lombok {@code @Getter}/{@code @Setter} kept, hand-written
 * {@code equals}/{@code hashCode} preserved verbatim. {@code orderNumber} widened to {@code
 * String} matching {@link OrderHeaderPK} (same rationale — see that class's Javadoc).
 */
@Setter
@Getter
@Embeddable
public class OrderHeaderAuditPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column(name = "ORDER_TYPE")
    private String orderType;

    @Column(name = "ORDER_NUMBER")
    private String orderNumber;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    public OrderHeaderAuditPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderHeaderAuditPK)) {
            return false;
        }
        OrderHeaderAuditPK castOther = (OrderHeaderAuditPK) other;
        return this.orderType.equals(castOther.orderType)
                && this.orderNumber.equals(castOther.orderNumber)
                && this.createdBy.equals(castOther.createdBy)
                && this.createdDate.equals(castOther.createdDate);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.orderType.hashCode();
        hash = hash * prime + this.orderNumber.hashCode();
        hash = hash * prime + this.createdBy.hashCode();
        hash = hash * prime + this.createdDate.hashCode();

        return hash;
    }
}
