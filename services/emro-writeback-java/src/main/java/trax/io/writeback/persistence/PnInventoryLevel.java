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
 * The persistent class for the PN_INVENTORY_LEVEL database table.
 *
 */
@Setter
@Getter
@Entity
@Table(name="PN_INVENTORY_LEVEL")
@NamedQuery(name="PnInventoryLevel.findAll", query="SELECT p FROM PnInventoryLevel p")
public class PnInventoryLevel implements Serializable {
	private static final long serialVersionUID = 1L;

	@EmbeddedId
	private PnInventoryLevelPK id;



	private String buyer;

	@Column(name="CREATED_BY")
	private String createdBy;

	@Column(name="CREATED_DATE")
	private Date createdDate;

	@Column(name="EOQ_LEVEL")
	private BigDecimal eoqLevel;

	@Column(name="\"GROUP\"")
	private String group;

	@Column(name="MAXIMUM_ORDER")
	private BigDecimal maximumOrder;

	@Column(name="MAXIMUM_STOCK")
	private BigDecimal maximumStock;

	@Column(name="MINIMUM_ORDER")
	private BigDecimal minimumOrder;

	@Column(name="MINIMUM_STOCK")
	private BigDecimal minimumStock;

	@Column(name="MODIFIED_BY")
	private String modifiedBy;

	@Column(name="MODIFIED_DATE")
	private Date modifiedDate;

	@Column(name="COMPANY")
	private String company;

	private BigDecimal notes;

	private String planner;

	private String pou;

	@Column(name="REORDER_LEVEL")
	private BigDecimal reorderLevel;

	@Column(name="REPLENISHMENT_LEAD_TIME")
	private BigDecimal replenishmentLeadTime;



	@Column(name="SERVICE_LEVEL")
	private BigDecimal serviceLevel;

	@Column(name="TRIGGER_REQUISITION")
	private String triggerRequisition;

	public PnInventoryLevel() {
	}


}
