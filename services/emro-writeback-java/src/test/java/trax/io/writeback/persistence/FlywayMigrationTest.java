package trax.io.writeback.persistence;

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
            insertLedgerRow(conn, "IDEMP-KEY-1");

            SQLException ex = assertThrows(SQLException.class, () -> insertLedgerRow(conn, "IDEMP-KEY-1"));
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

    private void insertLedgerRow(Connection conn, String idempotencyKey) throws SQLException {
        String sql =
                "INSERT INTO WRITEBACK_LEDGER "
                        + "(ID, IDEMPOTENCY_KEY, TENANT_ID, PN, LOCATION, PRINCIPAL, AGENT_VERSION, OUTCOME, CREATED_AT) "
                        + "VALUES (WRITEBACK_LEDGER_SEQ.NEXTVAL, ?, ?, ?, ?, ?, ?, ?, ?)";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            ps.setString(1, idempotencyKey);
            ps.setString(2, "acme");
            ps.setString(3, "PN-1");
            ps.setString(4, "JFK");
            ps.setString(5, "planner");
            ps.setString(6, "v1");
            ps.setString(7, "WRITTEN");
            ps.setTimestamp(8, Timestamp.from(Instant.now()));
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
