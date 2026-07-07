package trax.io.writeback.persistence;

import java.io.Serializable;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/**
 * The primary key class for the ORDER_DETAIL database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderDetailPK}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderDetailPK.java}).
 * Four mechanical changes: package renamed; no relationship fields on the PK itself to strip;
 * {@code orderType}/{@code orderNumber} made writable — ARMAC marks them {@code
 * insertable=false, updatable=false} because the owning {@code @ManyToOne OrderDetail
 * .orderHeader} association writes them instead, but that association is deleted here (see
 * {@link OrderDetail}'s Javadoc), so this PK must own writing both columns directly; Lombok
 * {@code @Getter}/{@code @Setter} kept, hand-written {@code equals}/{@code hashCode} preserved
 * verbatim.
 *
 * <p>{@code orderNumber} stays ARMAC's primitive {@code long}, matching {@link OrderHeaderPK}
 * (see that class's Javadoc) — the two must agree in type since {@link OrderDetail} logically
 * foreign-keys onto {@link OrderHeader} by {@code (ORDER_TYPE, ORDER_NUMBER)} even without a
 * mapped JPA relationship.
 */
@Setter
@Getter
@Embeddable
public class OrderDetailPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column(name = "ORDER_TYPE")
    private String orderType;

    @Column(name = "ORDER_NUMBER")
    private long orderNumber;

    @Column(name = "ORDER_LINE")
    private long orderLine;

    public OrderDetailPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof OrderDetailPK)) {
            return false;
        }
        OrderDetailPK castOther = (OrderDetailPK) other;
        return this.orderType.equals(castOther.orderType)
                && (this.orderNumber == castOther.orderNumber)
                && (this.orderLine == castOther.orderLine);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.orderType.hashCode();
        hash = hash * prime + ((int) (this.orderNumber ^ (this.orderNumber >>> 32)));
        hash = hash * prime + ((int) (this.orderLine ^ (this.orderLine >>> 32)));

        return hash;
    }
}
