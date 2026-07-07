package trax.io.writeback.persistence;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

/** The persistent class for the PN_INVENTORY_LEVEL_AUDIT database table. */
@Setter
@Getter
@Entity
@Table(name = "PN_INVENTORY_LEVEL_AUDIT")
@NamedQuery(name = "PnInventoryLevelAudit.findAll", query = "SELECT p FROM PnInventoryLevelAudit p")
public class PnInventoryLevelAudit implements Serializable {
    private static final long serialVersionUID = 1L;

    @EmbeddedId private PnInventoryLevelAuditPK id;

    private String buyer;

    @Column(name = "EOQ_LEVEL")
    private BigDecimal eoqLevel;

    @Column(name = "\"GROUP\"")
    private String group;

    @Column(name = "MAXIMUM_ORDER")
    private BigDecimal maximumOrder;

    @Column(name = "MAXIMUM_STOCK")
    private BigDecimal maximumStock;

    @Column(name = "MINIMUM_ORDER")
    private BigDecimal minimumOrder;

    @Column(name = "MINIMUM_STOCK")
    private BigDecimal minimumStock;

    @Column(name = "MODIFIED_BY")
    private String modifiedBy;

    @Column(name = "MODIFIED_DATE")
    private Date modifiedDate;

    private BigDecimal notes;

    private String planner;

    private String pou;

    @Column(name = "REORDER_LEVEL")
    private BigDecimal reorderLevel;

    @Column(name = "REVIEW_DATE")
    private Date reviewDate;

    @Column(name = "TRANSACTION_TYPE")
    private String transactionType;

    @Column(name = "TRIGGER_REQUISITION")
    private String triggerRequisition;

    public PnInventoryLevelAudit() {}

}
