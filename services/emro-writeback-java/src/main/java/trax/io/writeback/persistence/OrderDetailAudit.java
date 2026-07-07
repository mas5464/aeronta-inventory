package trax.io.writeback.persistence;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.NamedQuery;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.Setter;

/**
 * The persistent class for the ORDER_DETAIL_AUDIT database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderDetailAudit}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderDetailAudit.java}).
 * Four mechanical changes: package renamed; no relationship fields; no ARMAC-restricted columns;
 * Lombok {@code @Getter}/{@code @Setter} kept (ARMAC's accessors were pure get/set).
 *
 * <p>Trimmed to the field set {@code StockTransferOrderData.insertAudit}'s detail-audit half
 * actually sets plus the PK, matching {@code RequisitionDetailAudit}'s trim discipline.
 *
 * <p>{@code batch} stays ARMAC's {@code BigDecimal}, matching {@link OrderDetail}'s field
 * (see that class's Javadoc for the rationale).
 */
@Setter
@Getter
@Entity
@Table(name = "ORDER_DETAIL_AUDIT")
@NamedQuery(name = "OrderDetailAudit.findAll", query = "SELECT o FROM OrderDetailAudit o")
public class OrderDetailAudit implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private OrderDetailAuditPK id;

    private BigDecimal batch;

    @Column(name = "DELIVERY_DATE")
    private Date deliveryDate;

    @Column(name = "DELIVERY_HOUR")
    private BigDecimal deliveryHour;

    @Column(name = "DELIVERY_MINUTE")
    private BigDecimal deliveryMinute;

    private String location;

    @Column(name = "MODIFIED_BY")
    private String modifiedBy;

    @Column(name = "MODIFIED_DATE")
    private Date modifiedDate;

    @Column(name = "NON_INVENTORY_FLAG")
    private String nonInventoryFlag;

    private String pn;

    @Column(name = "QTY_AVAILABLE")
    private BigDecimal qtyAvailable;

    @Column(name = "QTY_PENDING_RI")
    private BigDecimal qtyPendingRi;

    @Column(name = "QTY_RECEIVED")
    private BigDecimal qtyReceived;

    @Column(name = "QTY_REQUIRE")
    private BigDecimal qtyRequire;

    @Column(name = "QTY_US")
    private BigDecimal qtyUs;

    @Column(name = "RO_BIN")
    private String roBin;

    @Column(name = "RO_LOCATION")
    private String roLocation;

    private String sn;

    private String status;

    @Column(name = "TO_BIN")
    private String toBin;

    private String uom;

    public OrderDetailAudit() {}
}
