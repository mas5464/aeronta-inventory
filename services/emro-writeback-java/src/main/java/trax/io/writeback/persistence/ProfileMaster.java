package trax.io.writeback.persistence;

import jakarta.persistence.*;
import lombok.Getter;
import org.hibernate.annotations.Immutable;

@Entity @Immutable @Getter
@Table(name = "PROFILE_MASTER")
public class ProfileMaster {
    @Id @Column(name = "\"PROFILE\"") private String profile;
}
