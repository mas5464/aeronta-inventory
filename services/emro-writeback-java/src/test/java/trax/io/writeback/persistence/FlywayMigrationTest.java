package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.junit.QuarkusTestProfile;
import io.quarkus.test.junit.TestProfile;
import jakarta.inject.Inject;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.SQLIntegrityConstraintViolationException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Map;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;

/**
 * Verifies the Flyway-managed V1 migration actually creates WRITEBACK_LEDGER with its
 * unique idempotency constraint. Runs with Hibernate schema generation OFF and Flyway
 * migration ON, so this is the only test proving the SQL migration file itself (not
 * Hibernate's drop-and-create) stands up the table.
 */
@QuarkusTest
@TestProfile(FlywayMigrationTest.FlywayOnlyProfile.class)
class FlywayMigrationTest {

    @Inject DataSource dataSource;

    @Test
    void migration_creates_writeback_ledger_table() throws SQLException {
        try (Connection conn = dataSource.getConnection()) {
            try (ResultSet rs = conn.getMetaData().getTables(null, null, "WRITEBACK_LEDGER", null)) {
                assertTrue(rs.next(), "WRITEBACK_LEDGER table should exist after Flyway migration");
            }
        }
    }

    @Test
    void duplicate_idempotency_key_violates_unique_constraint() throws SQLException {
        try (Connection conn = dataSource.getConnection()) {
            // Both rows use the SAME tenant ("acme", the insertLedgerRow default) — this is the
            // composite (TENANT_ID, IDEMPOTENCY_KEY) constraint (UQ_WRITEBACK_IDEMPOTENCY, Finding
            // 1) still tripping for a genuine same-tenant duplicate.
            insertLedgerRow(conn, "IDEMP-KEY-1", "PN-1", "JFK", 1L);

            SQLException ex =
                    assertThrows(SQLException.class, () -> insertLedgerRow(conn, "IDEMP-KEY-1", "PN-1", "JFK", 2L));
            SQLException chained = ex;
            boolean foundConstraintViolation = false;
            while (chained != null) {
                if (chained instanceof SQLIntegrityConstraintViolationException) {
                    foundConstraintViolation = true;
                    break;
                }
                chained = chained.getNextException();
            }
            assertTrue(
                    foundConstraintViolation,
                    "Expected SQLIntegrityConstraintViolationException in the exception chain, got: " + ex);
        }
    }

    @Test
    void same_idempotency_key_different_tenants_does_not_violate_unique_constraint() throws SQLException {
        // The composite (TENANT_ID, IDEMPOTENCY_KEY) uniqueness (Finding 1) must NOT trip when two
        // DIFFERENT tenants happen to derive (or explicitly supply) the exact same key.
        try (Connection conn = dataSource.getConnection()) {
            insertLedgerRow(conn, "acme", "IDEMP-KEY-XTENANT", "PN-XT", "JFK-XT", 1L);
            assertDoesNotThrow(
                    () -> insertLedgerRow(conn, "beta", "IDEMP-KEY-XTENANT", "PN-XT", "JFK-XT", 1L),
                    "same idempotency key under a different tenant must be allowed");
        }
    }

    @Test
    void duplicate_tenant_pn_location_version_violates_unique_constraint() throws SQLException {
        try (Connection conn = dataSource.getConnection()) {
            // Two DIFFERENT idempotency keys, but the SAME (tenant, pn, location, version) — this
            // is the version-chain race guarded by UQ_WRITEBACK_KEY_VERSION (Finding 2).
            insertLedgerRow(conn, "IDEMP-KEY-VCHAIN-1", "PN-VCHAIN", "JFK-VCHAIN", 1L);

            SQLException ex =
                    assertThrows(
                            SQLException.class,
                            () -> insertLedgerRow(conn, "IDEMP-KEY-VCHAIN-2", "PN-VCHAIN", "JFK-VCHAIN", 1L));
            SQLException chained = ex;
            boolean foundConstraintViolation = false;
            while (chained != null) {
                if (chained instanceof SQLIntegrityConstraintViolationException) {
                    foundConstraintViolation = true;
                    break;
                }
                chained = chained.getNextException();
            }
            assertTrue(
                    foundConstraintViolation,
                    "Expected SQLIntegrityConstraintViolationException in the exception chain, got: " + ex);
        }
    }

    @Test
    void null_domain_violates_not_null_constraint() throws SQLException {
        try (Connection conn = dataSource.getConnection()) {
            String sql =
                    "INSERT INTO WRITEBACK_LEDGER "
                            + "(ID, IDEMPOTENCY_KEY, TENANT_ID, PN, LOCATION, PRINCIPAL, AGENT_VERSION, OUTCOME, DOMAIN, VERSION, CREATED_AT) "
                            + "VALUES (WRITEBACK_LEDGER_SEQ.NEXTVAL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
            SQLException ex =
                    assertThrows(
                            SQLException.class,
                            () -> {
                                try (PreparedStatement ps = conn.prepareStatement(sql)) {
                                    ps.setString(1, "IDEMP-KEY-NULL-DOMAIN");
                                    ps.setString(2, "acme");
                                    ps.setString(3, "PN-NULL-DOMAIN");
                                    ps.setString(4, "JFK-NULL-DOMAIN");
                                    ps.setString(5, "planner");
                                    ps.setString(6, "v1");
                                    ps.setString(7, "WRITTEN");
                                    ps.setNull(8, java.sql.Types.VARCHAR);
                                    ps.setLong(9, 1L);
                                    ps.setTimestamp(10, Timestamp.from(Instant.now()));
                                    ps.executeUpdate();
                                }
                            });
            SQLException chained = ex;
            boolean foundConstraintViolation = false;
            while (chained != null) {
                if (chained instanceof SQLIntegrityConstraintViolationException) {
                    foundConstraintViolation = true;
                    break;
                }
                chained = chained.getNextException();
            }
            assertTrue(
                    foundConstraintViolation,
                    "Expected SQLIntegrityConstraintViolationException (NOT NULL DOMAIN) in the exception chain, got: "
                            + ex);
        }
    }

    @Test
    void created_ref_column_exists() throws SQLException {
        try (Connection conn = dataSource.getConnection()) {
            try (ResultSet rs = conn.getMetaData().getColumns(null, null, "WRITEBACK_LEDGER", "CREATED_REF")) {
                assertTrue(rs.next(), "WRITEBACK_LEDGER.CREATED_REF column should exist after Flyway migration");
            }
        }
    }

    private void insertLedgerRow(Connection conn, String idempotencyKey, String pn, String location, long version)
            throws SQLException {
        insertLedgerRow(conn, "acme", idempotencyKey, pn, location, version);
    }

    private void insertLedgerRow(
            Connection conn, String tenantId, String idempotencyKey, String pn, String location, long version)
            throws SQLException {
        String sql =
                "INSERT INTO WRITEBACK_LEDGER "
                        + "(ID, IDEMPOTENCY_KEY, TENANT_ID, PN, LOCATION, PRINCIPAL, AGENT_VERSION, OUTCOME, DOMAIN, VERSION, CREATED_AT) "
                        + "VALUES (WRITEBACK_LEDGER_SEQ.NEXTVAL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, idempotencyKey);
            ps.setString(2, tenantId);
            ps.setString(3, pn);
            ps.setString(4, location);
            ps.setString(5, "planner");
            ps.setString(6, "v1");
            ps.setString(7, "WRITTEN");
            ps.setString(8, "STOCK_LEVEL");
            ps.setLong(9, version);
            ps.setTimestamp(10, Timestamp.from(Instant.now()));
            ps.executeUpdate();
        }
    }

    public static class FlywayOnlyProfile implements QuarkusTestProfile {
        @Override
        public Map<String, String> getConfigOverrides() {
            return Map.of(
                    "quarkus.flyway.migrate-at-start", "true",
                    // Dev Services reuses the same Testcontainers Oracle container across test
                    // JVM runs in this module (testcontainers.reuse.enable=true), and other
                    // profiles' Hibernate drop-and-create leaves objects behind in the schema.
                    // clean-at-start guarantees Flyway always sees an empty schema here, so this
                    // test proves the V1 migration itself (not container freshness) creates the
                    // table.
                    "quarkus.flyway.clean-at-start", "true",
                    "quarkus.hibernate-orm.database.generation", "none");
        }
    }
}
