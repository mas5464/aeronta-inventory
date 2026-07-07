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
 * The persistent class for the ORDER_HEADER database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderHeader}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderHeader.java}).
 * Four mechanical changes: package renamed to {@code trax.io.writeback.persistence}; the
 * bi-directional {@code @OneToMany orderDetails} relationship (and its {@code
 * addOrderDetail}/{@code removeOrderDetail} helpers) deleted — no relationship graph in this
 * project's lifted entity set, matching {@code RequisitionHeader}'s precedent; no ARMAC-restricted
 * columns to make writable (ARMAC's original has none); Lombok {@code @Getter}/{@code @Setter}
 * kept (ARMAC's accessors were already pure get/set, safe to replace).
 *
 * <p>Trimmed to the field set {@code StockTransferOrderData.createOrderHeader} actually sets
 * (see that method's Javadoc on {@link trax.io.writeback.domain.TransferCreator} for which of
 * those are ported here vs. intentionally out of scope) plus the PK. ARMAC's original carries
 * ~90 more columns (freight/currency/warranty/SPEC2000/EDI bookkeeping) with no bearing on a
 * Trax IO-initiated stock transfer; they are omitted, matching {@code RequisitionHeader}'s
 * trim discipline.
 */
@Setter
@Getter
@Entity
@Table(name = "ORDER_HEADER")
@NamedQuery(name = "OrderHeader.findAll", query = "SELECT o FROM OrderHeader o")
public class OrderHeader implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private OrderHeaderPK id;

    @Column(name = "\"AUTHORIZATION\"")
    private String authorization;

    @Column(name = "AUTHORIZATION_BY")
    private String authorizationBy;

    @Column(name = "AUTHORIZATION_DATE")
    private Date authorizationDate;

    @Column(name = "BILL_TO_LOCATION")
    private String billToLocation;

    private String currency;

    @Column(name = "CURRENCY_EXCHANGE")
    private BigDecimal currencyExchange;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    @Column(name = "INTERFACE_CREATED_DATE")
    private Date interfaceCreatedDate;

    @Column(name = "INTERFACE_MODIFIED_DATE")
    private Date interfaceModifiedDate;

    @Column(name = "INVENTORY_TYPE")
    private String inventoryType;

    @Column(name = "MODIFIED_BY")
    private String modifiedBy;

    @Column(name = "MODIFIED_DATE")
    private Date modifiedDate;

    @Column(name = "NO_OF_PRINT")
    private BigDecimal noOfPrint;

    @Column(name = "OVERRIDE_ADDRESS")
    private String overrideAddress;

    private String priority;

    @Column(name = "REQUESTER_LOCATION")
    private String requesterLocation;

    @Column(name = "SHIPPED_FROM_LOCATION")
    private String shippedFromLocation;

    private String status;

    public OrderHeader() {}
}
