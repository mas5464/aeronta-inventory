package trax.io.writeback.persistence;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.NamedQuery;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.Setter;

/** The persistent class for the REQUISITION_HEADER database table. */
@Setter
@Getter
@Entity
@Table(name = "REQUISITION_HEADER")
@NamedQuery(name = "RequisitionHeader.findAll", query = "SELECT r FROM RequisitionHeader r")
public class RequisitionHeader implements Serializable {
    private static final long serialVersionUID = 1L;

    @Id private long requisition;

    private String ac;

    @Column(name = "ASSIGN_TO")
    private String assignTo;

    @Column(name = "\"AUTHORIZATION\"")
    private String authorization;

    @Column(name = "AUTHORIZED_BY")
    private String authorizedBy;

    @Column(name = "AUTHORIZED_DATE")
    private Date authorizedDate;

    @Column(name = "BLOB_NO")
    private BigDecimal blobNo;

    private String category;

    @Column(name = "CO_ORDER_LINE")
    private BigDecimal coOrderLine;

    @Column(name = "CO_ORDER_NUMBER")
    private BigDecimal coOrderNumber;

    @Column(name = "CO_ORDER_TYPE")
    private String coOrderType;

    private String company;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    private String defect;

    @Column(name = "DEFECT_ITEM")
    private BigDecimal defectItem;

    @Column(name = "DEFECT_TYPE")
    private String defectType;

    @Column(name = "DOCUMENT_NO")
    private BigDecimal documentNo;

    private String eo;

    private String evaluation;

    @Column(name = "INTERFACE_TRNSFR_REQ_AZUL")
    private Date interfaceTrnsfrReqAzul;

    @Column(name = "INVENTORY_TYPE")
    private String inventoryType;

    @Column(name = "MODIFIED_BY")
    private String modifiedBy;

    @Column(name = "MODIFIED_DATE")
    private Date modifiedDate;

    private BigDecimal notes;

    @Column(name = "ORDER_CATEGORY")
    private String orderCategory;

    private String owner;

    private String priority;

    @Column(name = "RELEASE_FOR_AUTHORIZATION")
    private String releaseForAuthorization;

    @Column(name = "RELEASE_FOR_AUTHORIZATION_ON")
    private Date releaseForAuthorizationOn;

    @Column(name = "REQUESTER_LOCATION")
    private String requesterLocation;

    @Column(name = "REQUISITION_DESCRIPTION")
    private String requisitionDescription;

    @Column(name = "REQUISITION_GENERATED_BY")
    private BigDecimal requisitionGeneratedBy;

    @Column(name = "REQUISITION_LINE_GENERATED_BY")
    private BigDecimal requisitionLineGeneratedBy;

    @Column(name = "REQUISTION_TYPE")
    private String requistionType;

    private String site;

    private BigDecimal so;

    private String status;

    @Column(name = "TASK_CARD")
    private String taskCard;

    @Column(name = "TASK_CARD_PN")
    private String taskCardPn;

    @Column(name = "TASK_CARD_SN")
    private String taskCardSn;

    private BigDecimal wo;

    public RequisitionHeader() {}
}
