package trax.io.writeback.persistence;

import jakarta.persistence.*;
import lombok.Getter;
import org.hibernate.annotations.Immutable;

@Entity @Immutable @Getter
@Table(name = "LOCATION_MASTER")
public class LocationMaster {
    @Id @Column(name = "LOCATION") private String location;
    @Column(name = "INVENTORY") private String inventory;
    @Column(name = "INVENTORY_QUARANTINE") private String inventoryQuarantine;
}
