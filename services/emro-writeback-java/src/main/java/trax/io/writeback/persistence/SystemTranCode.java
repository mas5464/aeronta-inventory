package trax.io.writeback.persistence;

import jakarta.persistence.*;
import lombok.Getter;
import org.hibernate.annotations.Immutable;

@Entity @Immutable @Getter
@Table(name = "SYSTEM_TRAN_CODE")
public class SystemTranCode {
    @EmbeddedId private SystemTranCodePK id;
    @Column(name = "PN_TRANSACTION") private String pnTransaction;
}
