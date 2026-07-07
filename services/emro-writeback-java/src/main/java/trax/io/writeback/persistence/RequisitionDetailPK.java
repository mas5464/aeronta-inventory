package trax.io.writeback.persistence;

import java.io.Serializable;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/** The primary key class for the REQUISITION_DETAIL database table. */
@Setter
@Getter
@Embeddable
public class RequisitionDetailPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column
    private long requisition;

    @Column(name = "REQUISITION_LINE")
    private long requisitionLine;

    public RequisitionDetailPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof RequisitionDetailPK castOther)) {
            return false;
        }
        return (this.requisition == castOther.requisition)
                && (this.requisitionLine == castOther.requisitionLine);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + ((int) (this.requisition ^ (this.requisition >>> 32)));
        hash = hash * prime + ((int) (this.requisitionLine ^ (this.requisitionLine >>> 32)));

        return hash;
    }
}
