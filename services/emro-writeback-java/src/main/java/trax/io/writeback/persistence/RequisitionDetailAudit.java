package trax.io.writeback.persistence;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

/** The persistent class for the REQUISITION_DETAIL_AUDIT database table. */
@Setter
@Getter
@Entity
@Table(name = "REQUISITION_DETAIL_AUDIT")
@NamedQuery(
        name = "RequisitionDetailAudit.findAll",
        query = "SELECT r FROM RequisitionDetailAudit r")
public class RequisitionDetailAudit implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private RequisitionDetailAuditPK id;

    @Column(name = "ASSIGN_TO")
    private String assignTo;

    @Column(name = "AUTHORIZATION_CONTROL")
    private BigDecimal authorizationControl;

    @Column(name = "BLOB_NO")
    private BigDecimal blobNo;

    @Column(name = "CANCEL_REASON")
    private String cancelReason;

    @Column(name = "CAPITAL_EXPENDITURE")
    private String capitalExpenditure;

    private String condition;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    @Column(name = "DOCUMENT_NO")
    private BigDecimal documentNo;

    @Column(name = "ESTIMATED_COST")
    private BigDecimal estimatedCost;

    @Column(name = "ESTIMATED_CURRENCY")
    private String estimatedCurrency;

    @Column(name = "EXPECTED_DELIVERY_DATE")
    private Date expectedDeliveryDate;

    @Column(name = "EXTERNAL_POSITION")
    private String externalPosition;

    @Column(name = "EXTERNAL_REFERENCE")
    private String externalReference;

    private String gl;

    @Column(name = "GL_COMPANY")
    private String glCompany;

    @Column(name = "GL_COST_CENTER")
    private String glCostCenter;

    @Column(name = "GL_EXPENDITURE")
    private String glExpenditure;

    private String ipc;

    @Column(name = "LHT_REFERENCE")
    private String lhtReference;

    private String location;

    @Column(name = "MAT_RQST_STATUS")
    private String matRqstStatus;

    @Column(name = "NON_INVENTORY_FLAG")
    private String nonInventoryFlag;

    private BigDecimal notes;

    private String owner;

    private String pn;

    @Column(name = "PN_DESCRIPTION")
    private String pnDescription;

    @Column(name = "PN_GROUP")
    private String pnGroup;

    @Column(name = "PREFER_VENDOR")
    private String preferVendor;

    @Column(name = "QTY_RECEIVED")
    private BigDecimal qtyReceived;

    @Column(name = "QTY_REQUIRE")
    private BigDecimal qtyRequire;

    private String recommended;

    @Column(name = "REQUIRE_DATE")
    private Date requireDate;

    @Column(name = "REQUIRE_HOUR")
    private BigDecimal requireHour;

    @Column(name = "REQUIRE_MINUTE")
    private BigDecimal requireMinute;

    private String status;

    @Column(name = "TAX_INCENTIVE")
    private String taxIncentive;

    @Column(name = "TAX_INCENTIVE_NBR")
    private String taxIncentiveNbr;

    @Column(name = "TRANSACTION_TYPE")
    private String transactionType;

    private String uom;

    public RequisitionDetailAudit() {}
}
