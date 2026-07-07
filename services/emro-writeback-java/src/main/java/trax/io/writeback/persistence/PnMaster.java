package trax.io.writeback.persistence;

import jakarta.persistence.*;
import lombok.Getter;
import org.hibernate.annotations.Immutable;

@Entity @Immutable @Getter
@Table(name = "PN_MASTER")
public class PnMaster {
    @Id @Column(name = "PN") private String pn;
    @Column(name = "CATEGORY") private String category;
    @Column(name = "STATUS") private String status;
    @Column(name = "STOCK_UOM") private String stockUom;
}
