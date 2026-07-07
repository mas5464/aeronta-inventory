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
 * The persistent class for the ORDER_HEADER_AUDIT database table.
 *
 * <p>Lifted from ARMAC's {@code trax.aero.model.OrderHeaderAudit}
 * ({@code /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/OrderHeaderAudit.java}).
 * Four mechanical changes: package renamed; no relationship fields; no ARMAC-restricted columns;
 * Lombok {@code @Getter}/{@code @Setter} kept. ARMAC's original also carries a {@code
 * OrderHeaderAudit(OrderHeader)} copy-constructor — dropped here (not pure, and not used: {@link
 * trax.io.writeback.domain.TransferCreator} builds the audit row field-by-field itself, mirroring
 * {@code RequisitionCreator}'s discipline rather than ARMAC's constructor-based copy).
 *
 * <p>Trimmed to the field set {@code StockTransferOrderData.insertAudit}'s header-audit half
 * actually sets plus the PK, matching {@code RequisitionHeaderAudit}'s trim discipline.
 */
@Setter
@Getter
@Entity
@Table(name = "ORDER_HEADER_AUDIT")
@NamedQuery(name = "OrderHeaderAudit.findAll", query = "SELECT o FROM OrderHeaderAudit o")
public class OrderHeaderAudit implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private OrderHeaderAuditPK id;

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

    @Column(name = "INTERFACE_CREATED_DATE")
    private Date interfaceCreatedDate;

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

    public OrderHeaderAudit() {}
}
