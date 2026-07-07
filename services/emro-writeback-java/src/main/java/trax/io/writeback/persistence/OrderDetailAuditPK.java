package trax.io.writeback.persistence;

import java.io.Serializable;
import java.util.Date;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/**
 * The primary key class for the ORDER_DETAIL_AUDIT database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderDetailAuditPK}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderDetailAuditPK.java}).
 * Four mechanical changes: package renamed; no relationship fields; no ARMAC-restricted columns
 * (this class has none); Lombok {@code @Getter}/{@code @Setter} kept, hand-written {@code
 * equals}/{@code hashCode} preserved verbatim. {@code orderNumber} widened to {@code String}
 * matching {@link OrderHeaderPK}/{@link OrderDetailPK} (same rationale — see {@link
 * OrderHeaderPK}'s Javadoc).
 */
@Setter
@Getter
@Embeddable
public class OrderDetailAuditPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column(name = "ORDER_TYPE")
    private String orderType;

    @Column(name = "ORDER_NUMBER")
    private String orderNumber;

    @Column(name = "ORDER_LINE")
    private long orderLine;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    public OrderDetailAuditPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderDetailAuditPK)) {
            return false;
        }
        OrderDetailAuditPK castOther = (OrderDetailAuditPK) other;
        return this.orderType.equals(castOther.orderType)
                && this.orderNumber.equals(castOther.orderNumber)
                && (this.orderLine == castOther.orderLine)
                && this.createdBy.equals(castOther.createdBy)
                && this.createdDate.equals(castOther.createdDate);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.orderType.hashCode();
        hash = hash * prime + this.orderNumber.hashCode();
        hash = hash * prime + ((int) (this.orderLine ^ (this.orderLine >>> 32)));
        hash = hash * prime + this.createdBy.hashCode();
        hash = hash * prime + this.createdDate.hashCode();

        return hash;
    }
}
