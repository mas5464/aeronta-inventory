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
 * The persistent class for the ORDER_DETAIL database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderDetail}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderDetail.java}).
 * Four mechanical changes: package renamed; the bi-directional {@code @ManyToOne orderHeader}
 * association (with its {@code @JoinColumns} pinning {@code ORDER_NUMBER}/{@code ORDER_TYPE} as
 * {@code insertable=false, updatable=false}) deleted — matching {@link OrderHeader}'s dropped
 * {@code orderDetails} side, and forcing {@link OrderDetailPK}'s writable-column change (see that
 * class's Javadoc); no further ARMAC-restricted columns on this entity itself; Lombok {@code
 * @Getter}/{@code @Setter} kept (ARMAC's accessors were pure get/set).
 *
 * <p>Trimmed to the field set {@code StockTransferOrderData.createOrderDeatail} (sic — ARMAC's
 * own method name) actually sets plus the PK. ARMAC's original carries ~150 more columns
 * (repair/loan/warranty/SPEC2000/EDI bookkeeping columns irrelevant to a Trax IO stock
 * transfer); they are omitted, matching {@code RequisitionDetail}'s trim discipline.
 *
 * <p><b>Out of scope (documented, matches {@code RequisitionCreator}'s precedent):</b> ARMAC's
 * {@code createOrderDeatail} also reads {@code PnInventoryDetail} (to source {@code batch}/{@code
 * sn}/{@code roBin}/{@code qtyAvailable} etc.) and writes {@code PnInventoryHistory} rows — this
 * project has no {@code PnInventoryDetail}/{@code PnInventoryHistory} entities and {@link
 * trax.io.writeback.domain.TransferCommand} carries a caller-supplied {@code batch} string
 * rather than resolving one from inventory-detail lookups, so those steps are out of scope for
 * this slice (see {@link trax.io.writeback.domain.TransferCreator}'s Javadoc).
 *
 * <p><b>Deviation from ARMAC (type widen):</b> ARMAC's {@code batch} is {@code BigDecimal} —
 * ARMAC always sources it from a resolved {@code PnInventoryDetail.getBatch()} numeric ID. Since
 * this project has no {@code PnInventoryDetail} entity (see above), {@link
 * trax.io.writeback.domain.TransferCommand#batch()} is instead a caller-supplied opaque batch
 * label with no guaranteed numeric form, so {@code batch} is widened to {@code String} here to
 * persist it verbatim rather than force a lossy/failing numeric parse.
 */
@Setter
@Getter
@Entity
@Table(name = "ORDER_DETAIL")
@NamedQuery(name = "OrderDetail.findAll", query = "SELECT o FROM OrderDetail o")
public class OrderDetail implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private OrderDetailPK id;

    private String batch;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    @Column(name = "DELIVERY_DATE")
    private Date deliveryDate;

    @Column(name = "DELIVERY_HOUR")
    private BigDecimal deliveryHour;

    @Column(name = "DELIVERY_MINUTE")
    private BigDecimal deliveryMinute;

    @Column(name = "IN_USE")
    private String inUse;

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

    public OrderDetail() {}
}
