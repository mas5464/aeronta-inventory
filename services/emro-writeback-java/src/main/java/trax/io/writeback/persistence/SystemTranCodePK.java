package trax.io.writeback.persistence;

import java.io.Serializable;

import jakarta.persistence.Column;
import jakarta.persistence.Embeddable;
import lombok.Getter;
import lombok.Setter;

@Embeddable
@Getter @Setter
public class SystemTranCodePK implements Serializable {
    private static final long serialVersionUID = 1L;

    @Column(name = "SYSTEM_TRANSACTION")
    private String systemTransaction;

    @Column(name = "SYSTEM_CODE")
    private String systemCode;

    @Column(name = "SYSTEM_TRAN_CODE_SUB")
    private String systemTranCodeSub;

    public SystemTranCodePK() {}

    public boolean equals(Object other) {
        if (this == other) {
            return true;
        }
        if (!(other instanceof SystemTranCodePK)) {
            return false;
        }
        SystemTranCodePK castOther = (SystemTranCodePK) other;
        return this.systemTransaction.equals(castOther.systemTransaction)
                && this.systemCode.equals(castOther.systemCode)
                && this.systemTranCodeSub.equals(castOther.systemTranCodeSub);
    }

    public int hashCode() {
        final int prime = 31;
        int hash = 17;
        hash = hash * prime + this.systemTransaction.hashCode();
        hash = hash * prime + this.systemCode.hashCode();
        hash = hash * prime + this.systemTranCodeSub.hashCode();

        return hash;
    }
}
