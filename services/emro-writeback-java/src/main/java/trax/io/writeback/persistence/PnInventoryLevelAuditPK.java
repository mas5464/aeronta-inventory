package trax.io.writeback.persistence;

import java.io.Serializable;
import java.util.Date;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

/** The primary key class for the PN_INVENTORY_LEVEL_AUDIT database table. */
@Setter
@Getter
@Embeddable
public class PnInventoryLevelAuditPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    private String pn;

    private String location;

    @Column(name = "CREATED_BY")
    private String createdBy;

    @Column(name = "CREATED_DATE")
    private Date createdDate;

    private String company;

    public PnInventoryLevelAuditPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof PnInventoryLevelAuditPK)) {
            return false;
        }
        PnInventoryLevelAuditPK castOther = (PnInventoryLevelAuditPK) other;
        return this.pn.equals(castOther.pn)
                && this.location.equals(castOther.location)
                && this.createdBy.equals(castOther.createdBy)
                && this.createdDate.equals(castOther.createdDate)
                && this.company.equals(castOther.company);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.pn.hashCode();
        hash = hash * prime + this.location.hashCode();
        hash = hash * prime + this.createdBy.hashCode();
        hash = hash * prime + this.createdDate.hashCode();
        hash = hash * prime + this.company.hashCode();

        return hash;
    }
}
