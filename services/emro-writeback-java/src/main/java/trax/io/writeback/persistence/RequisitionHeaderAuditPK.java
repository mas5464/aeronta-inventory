package trax.io.writeback.persistence;

import java.io.Serializable;
import java.util.Date;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

/** The primary key class for the REQUISITION_HEADER_AUDIT database table. */
@Setter
@Getter
@Embeddable
public class RequisitionHeaderAuditPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    private long requisition;

    @Column(name = "MODIFIED_DATE")
    private Date modifiedDate;

    @Column(name = "MODIFIED_BY")
    private String modifiedBy;

    public RequisitionHeaderAuditPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof RequisitionHeaderAuditPK castOther)) {
            return false;
        }
        return (this.requisition == castOther.requisition)
                && this.modifiedDate.equals(castOther.modifiedDate)
                && this.modifiedBy.equals(castOther.modifiedBy);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + ((int) (this.requisition ^ (this.requisition >>> 32)));
        hash = hash * prime + this.modifiedDate.hashCode();
        hash = hash * prime + this.modifiedBy.hashCode();

        return hash;
    }
}
