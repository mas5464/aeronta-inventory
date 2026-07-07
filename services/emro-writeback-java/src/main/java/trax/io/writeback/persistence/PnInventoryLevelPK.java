package trax.io.writeback.persistence;

import java.io.Serializable;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

/** The primary key class for the PN_INVENTORY_LEVEL database table. */
@Setter
@Getter
@Embeddable
public class PnInventoryLevelPK implements Serializable {
    // default serial version id, required for serializable classes.
    private static final long serialVersionUID = 1L;

    @Column
    private String pn;

    @Column
    private String location;

    public PnInventoryLevelPK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof PnInventoryLevelPK)) {
            return false;
        }
        PnInventoryLevelPK castOther = (PnInventoryLevelPK) other;
        return this.pn.equals(castOther.pn) && this.location.equals(castOther.location);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.pn.hashCode();
        hash = hash * prime + this.location.hashCode();

        return hash;
    }
}
