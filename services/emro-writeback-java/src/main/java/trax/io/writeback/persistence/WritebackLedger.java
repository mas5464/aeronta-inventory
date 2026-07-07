package trax.io.writeback.persistence;

import java.io.Serializable;
import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.SequenceGenerator;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import jakarta.persistence.UniqueConstraint;
import lombok.Getter;
import lombok.Setter;

/**
 * The persistent class for the WRITEBACK_LEDGER database table.
 *
 * <p>This is the ONLY table this service creates/DDLs (via Flyway V1). It is the
 * service-owned idempotency ledger for eMRO writeback: every write attempt is recorded here,
 * keyed by a unique (TENANT_ID, IDEMPOTENCY_KEY) pair, so retries and duplicate deliveries
 * resolve to effectively-once semantics — scoped per tenant, since two different tenants may
 * legitimately compute (or explicitly supply) the same idempotency key.
 */
@Setter
@Getter
@Entity
@Table(
        name = "WRITEBACK_LEDGER",
        uniqueConstraints = {
            @UniqueConstraint(
                    name = "UQ_WRITEBACK_IDEMPOTENCY",
                    columnNames = {"TENANT_ID", "IDEMPOTENCY_KEY"}),
            @UniqueConstraint(
                    name = "UQ_WRITEBACK_KEY_VERSION",
                    columnNames = {"TENANT_ID", "PN", "LOCATION", "VERSION"})
        })
public class WritebackLedger implements Serializable {
    private static final long serialVersionUID = 1L;

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "writebackLedgerSeq")
    @SequenceGenerator(
            name = "writebackLedgerSeq",
            sequenceName = "WRITEBACK_LEDGER_SEQ",
            allocationSize = 50)
    @Column(name = "ID")
    private Long id;

    @Column(name = "IDEMPOTENCY_KEY", nullable = false)
    private String idempotencyKey;

    @Column(name = "TENANT_ID", nullable = false)
    private String tenantId;

    @Column(name = "RUN_ID")
    private String runId;

    @Column(name = "ROW_ID")
    private Long rowId;

    @Column(name = "PROVENANCE_ID")
    private String provenanceId;

    @Column(name = "PN", nullable = false)
    private String pn;

    @Column(name = "LOCATION", nullable = false)
    private String location;

    @Column(name = "SOURCE")
    private String source;

    @Column(name = "TIER")
    private Integer tier;

    @Column(name = "APPROVER")
    private String approver;

    @Column(name = "PRINCIPAL", nullable = false)
    private String principal;

    @Column(name = "AGENT_VERSION", nullable = false)
    private String agentVersion;

    @Column(name = "OUTCOME", nullable = false)
    private String outcome;

    /**
     * Which sub-domain this ledger row belongs to: {@code STOCK_LEVEL}, {@code REQUISITION}, or
     * {@code TRANSFER}. Only {@code STOCK_LEVEL} is written as of the current task; later
     * creator tasks (requisition/transfer create-domains) set the other two values.
     */
    @Column(name = "DOMAIN", nullable = false)
    private String domain;

    /**
     * Nullable back-reference to the eMRO record created by a create-domain write (e.g. a
     * requisition or transfer order number). Unused for {@code STOCK_LEVEL} rows, which update
     * an existing {@code PN_INVENTORY_LEVEL} row rather than creating a new eMRO record.
     */
    @Column(name = "CREATED_REF")
    private String createdRef;

    @Column(name = "VERSION")
    private Long version;

    @Column(name = "PARENT_VERSION")
    private Long parentVersion;

    @Column(name = "OLD_VALUES_JSON")
    private String oldValuesJson;

    @Column(name = "NEW_VALUES_JSON")
    private String newValuesJson;

    @Column(name = "MESSAGE")
    private String message;

    // Plain TIMESTAMP on the wire: Hibernate's default Instant mapping (TIMESTAMP_UTC) reads via
    // ResultSet.getObject(OffsetDateTime.class), which raises ORA-18716 on real Oracle 19c with
    // ojdbc 23 (found in live UAT; getTimestamp/LocalDateTime work fine). Run the service with
    // -Duser.timezone=UTC so the plain-timestamp normalization is stable.
    @JdbcTypeCode(SqlTypes.TIMESTAMP)
    @Column(name = "CREATED_AT", nullable = false)
    private Instant createdAt;

    public WritebackLedger() {
    }
}
