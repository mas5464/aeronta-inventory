package trax.io.writeback.smoke;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * Opt-in smoke test validating this module's entity/SQL assumptions against a REAL eMRO Oracle
 * schema. Plain JDBC ({@link DriverManager}) — no Quarkus boot, no JPA — so it exercises exactly
 * what a real target's SQL dialect and column set look like, independent of Hibernate mapping.
 *
 * <p><b>Opt-in only.</b> Runs only when {@code EMRO_SMOKE_DB_URL} is set to a non-blank value
 * (see {@link EnabledIfEnvironmentVariable}); Surefire's default {@code excludedGroups} in the
 * module {@code pom.xml} additionally excludes the {@code emro-smoke} tag from {@code mvn test},
 * so this is belt-and-braces gated: both the tag exclusion and the env-var guard must be
 * bypassed on purpose to actually run against a real database.
 *
 * <p><b>Safety contract:</b>
 *
 * <ul>
 *   <li>No DDL, ever.
 *   <li>DML only against the single designated {@code (EMRO_SMOKE_PN, EMRO_SMOKE_LOCATION)} key
 *       in {@code PN_INVENTORY_LEVEL}, and only a transient {@code REORDER_LEVEL} bump that is
 *       restored to its original value within the same test, over a single connection with
 *       explicit commit points. The restore is attempted on <b>any</b> failure of the
 *       update/verify sequence, including JUnit assertion failures (which are {@link Error}s, not
 *       {@link RuntimeException}s) — the surrounding {@code catch} widens to {@link Throwable} for
 *       exactly this reason. If the restore attempt itself fails, that failure is attached via
 *       {@link Throwable#addSuppressed} and logged to {@code System.err} with the affected
 *       PN/LOCATION/original value so an operator can restore manually.
 *   <li>Every other check is read-only.
 *   <li>Never touches Docker / the {@code oracle19c} container — this test only opens a JDBC
 *       connection to whatever URL the environment supplies.
 * </ul>
 *
 * <p>Required environment variables:
 *
 * <ul>
 *   <li>{@code EMRO_SMOKE_DB_URL} — JDBC URL, e.g. {@code jdbc:oracle:thin:@localhost:1521/XEPDB1}
 *   <li>{@code EMRO_SMOKE_DB_USER} — DB user
 *   <li>{@code EMRO_SMOKE_DB_PASSWORD} — DB password
 *   <li>{@code EMRO_SMOKE_PN} — a real {@code PN} to probe in {@code PN_INVENTORY_LEVEL}
 *   <li>{@code EMRO_SMOKE_LOCATION} — the matching {@code LOCATION}
 * </ul>
 *
 * <p>Example invocation (never run automatically; requires the user's local {@code oracle19c}):
 *
 * <pre>{@code
 * EMRO_SMOKE_DB_URL=jdbc:oracle:thin:@localhost:1521/XEPDB1 \
 * EMRO_SMOKE_DB_USER=... \
 * EMRO_SMOKE_DB_PASSWORD=... \
 * EMRO_SMOKE_PN=<test-pn> \
 * EMRO_SMOKE_LOCATION=<test-loc> \
 * mvn test -Dgroups=emro-smoke -DexcludedGroups= -Dtest=EmroSchemaSmokeTest
 * }</pre>
 */
@Tag("emro-smoke")
@EnabledIfEnvironmentVariable(named = "EMRO_SMOKE_DB_URL", matches = ".+")
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class EmroSchemaSmokeTest {

    private static String url() {
        return System.getenv("EMRO_SMOKE_DB_URL");
    }

    private static String user() {
        return System.getenv("EMRO_SMOKE_DB_USER");
    }

    private static String password() {
        return System.getenv("EMRO_SMOKE_DB_PASSWORD");
    }

    private static String pn() {
        return System.getenv("EMRO_SMOKE_PN");
    }

    private static String location() {
        return System.getenv("EMRO_SMOKE_LOCATION");
    }

    private static Connection connect() throws SQLException {
        return DriverManager.getConnection(url(), user(), password());
    }

    /** #1 — PN_MASTER reachable and its expected columns exist. */
    @Test
    @Order(1)
    void pn_master_is_reachable_with_expected_columns() throws SQLException {
        try (Connection conn = connect();
                Statement stmt = conn.createStatement();
                ResultSet rs =
                        stmt.executeQuery("SELECT PN, CATEGORY, STATUS FROM PN_MASTER WHERE ROWNUM = 1")) {
            ResultSetMetaData meta = rs.getMetaData();
            Set<String> columns = new HashSet<>();
            for (int i = 1; i <= meta.getColumnCount(); i++) {
                columns.add(meta.getColumnLabel(i).toUpperCase(Locale.ROOT));
            }
            assertTrue(columns.contains("PN"), "PN_MASTER.PN column should be visible");
            assertTrue(columns.contains("CATEGORY"), "PN_MASTER.CATEGORY column should be visible");
            assertTrue(columns.contains("STATUS"), "PN_MASTER.STATUS column should be visible");
        }
    }

    /**
     * #2 — {@link trax.io.writeback.persistence.TraxRepository#company()} assumes eMRO is
     * effectively single-tenant per install (exactly one {@code PROFILE_MASTER} row). If this
     * ever fails against a real target, that assumption is violated and {@code company()} needs
     * a config override (e.g. an explicit tenant/company property) instead of the
     * single-row query it runs today.
     */
    @Test
    @Order(2)
    void profile_master_has_exactly_one_row() throws SQLException {
        try (Connection conn = connect();
                Statement stmt = conn.createStatement();
                ResultSet rs = stmt.executeQuery("SELECT COUNT(*) FROM PROFILE_MASTER")) {
            assertTrue(rs.next(), "PROFILE_MASTER count query should return a row");
            long count = rs.getLong(1);
            assertEquals(
                    1L,
                    count,
                    "PROFILE_MASTER has "
                            + count
                            + " rows, not 1 — TraxRepository.company() assumes eMRO is"
                            + " single-tenant per install (exactly one PROFILE_MASTER row)."
                            + " This assumption is violated against this target; company()"
                            + " needs a config override (explicit tenant/company property)"
                            + " instead of relying on a single-row PROFILE_MASTER lookup.");
        }
    }

    /**
     * #3 — designated key's {@code PN_INVENTORY_LEVEL} row: bump {@code REORDER_LEVEL} by 1,
     * verify, then restore the original value and verify the restore — all over one connection
     * with explicit commit points. If the key doesn't exist, abort (not fail) with an
     * informative message: this is an environment/fixture gap, not a schema-assumption failure.
     */
    @Test
    @Order(3)
    void pn_inventory_level_round_trip_update_and_restore() throws Throwable {
        String pn = pn();
        String location = location();

        try (Connection conn = connect()) {
            conn.setAutoCommit(false);

            BigDecimal originalReorderLevel;
            try (PreparedStatement select =
                    conn.prepareStatement(
                            "SELECT REORDER_LEVEL FROM PN_INVENTORY_LEVEL WHERE PN = ? AND LOCATION = ?")) {
                select.setString(1, pn);
                select.setString(2, location);
                try (ResultSet rs = select.executeQuery()) {
                    if (!rs.next()) {
                        Assumptions.abort(
                                "No PN_INVENTORY_LEVEL row for PN="
                                        + pn
                                        + ", LOCATION="
                                        + location
                                        + " — designated smoke-test key is absent from this"
                                        + " target. Set EMRO_SMOKE_PN/EMRO_SMOKE_LOCATION to a"
                                        + " PN/LOCATION pair known to have a PN_INVENTORY_LEVEL"
                                        + " row, or seed one, then re-run.");
                        return;
                    }
                    originalReorderLevel = rs.getBigDecimal("REORDER_LEVEL");
                }
            }

            BigDecimal bumped =
                    (originalReorderLevel == null ? BigDecimal.ZERO : originalReorderLevel)
                            .add(BigDecimal.ONE);

            try {
                updateReorderLevel(conn, pn, location, bumped);
                conn.commit();

                assertEquals(
                        0,
                        bumped.compareTo(readReorderLevel(conn, pn, location)),
                        "REORDER_LEVEL did not reflect the bumped value after commit");

                updateReorderLevel(conn, pn, location, originalReorderLevel);
                conn.commit();

                BigDecimal restored = readReorderLevel(conn, pn, location);
                boolean restoredMatches =
                        (originalReorderLevel == null && restored == null)
                                || (originalReorderLevel != null
                                        && restored != null
                                        && originalReorderLevel.compareTo(restored) == 0);
                assertTrue(
                        restoredMatches,
                        "REORDER_LEVEL restore failed to reproduce the original value for PN="
                                + pn
                                + ", LOCATION="
                                + location
                                + " — original="
                                + originalReorderLevel
                                + ", restored="
                                + restored);
            } catch (Throwable e) {
                // Restore-on-any-failure, including JUnit 5 assertion failures: AssertionFailedError
                // extends AssertionError -> Error, NOT RuntimeException, so this must catch Throwable
                // or a failed mid-test verify would skip the restore and leave the REAL eMRO row's
                // REORDER_LEVEL bumped. The restore attempt itself calls a helper with its own
                // assertion (updateReorderLevel -> assertEquals), so it too can throw an Error, not
                // just SQLException — catch Throwable there as well so a restore failure never
                // escapes uncaught and always gets attached + logged instead.
                try {
                    updateReorderLevel(conn, pn, location, originalReorderLevel);
                    conn.commit();
                } catch (Throwable restoreFailure) {
                    e.addSuppressed(restoreFailure);
                    System.err.println(
                            "CRITICAL: PN_INVENTORY_LEVEL restore FAILED after a mid-test failure —"
                                    + " manual operator intervention required. PN="
                                    + pn
                                    + ", LOCATION="
                                    + location
                                    + ", original REORDER_LEVEL="
                                    + originalReorderLevel
                                    + ". Restore this value by hand against the real eMRO schema.");
                }
                throw e;
            }
        }
    }

    private static void updateReorderLevel(
            Connection conn, String pn, String location, BigDecimal value) throws SQLException {
        try (PreparedStatement update =
                conn.prepareStatement(
                        "UPDATE PN_INVENTORY_LEVEL SET REORDER_LEVEL = ? WHERE PN = ? AND LOCATION = ?")) {
            update.setBigDecimal(1, value);
            update.setString(2, pn);
            update.setString(3, location);
            int updated = update.executeUpdate();
            assertEquals(1, updated, "Expected exactly one PN_INVENTORY_LEVEL row to be updated");
        }
    }

    private static BigDecimal readReorderLevel(Connection conn, String pn, String location)
            throws SQLException {
        try (PreparedStatement select =
                conn.prepareStatement(
                        "SELECT REORDER_LEVEL FROM PN_INVENTORY_LEVEL WHERE PN = ? AND LOCATION = ?")) {
            select.setString(1, pn);
            select.setString(2, location);
            try (ResultSet rs = select.executeQuery()) {
                assertTrue(rs.next(), "Expected PN_INVENTORY_LEVEL row to still be present");
                return rs.getBigDecimal("REORDER_LEVEL");
            }
        }
    }

    /**
     * #4 — {@code PN_INVENTORY_LEVEL_AUDIT} and its expected columns exist, cross-checked
     * against {@link trax.io.writeback.persistence.PnInventoryLevelAudit} and
     * {@link trax.io.writeback.persistence.PnInventoryLevelAuditPK}'s {@code @Column}-mapped
     * fields: PN, LOCATION, CREATED_BY, CREATED_DATE, COMPANY (from the embedded PK) and
     * REORDER_LEVEL, EOQ_LEVEL, MINIMUM_STOCK, MAXIMUM_STOCK, MINIMUM_ORDER, MAXIMUM_ORDER
     * (from the entity itself).
     *
     * <p>Note: unlike {@code PnInventoryLevel}, the {@code PnInventoryLevelAudit} entity does
     * <b>not</b> map a {@code REPLENISHMENT_LEAD_TIME} field — confirmed by reading
     * {@code PnInventoryLevelAudit.java} — so it is deliberately not asserted here.
     */
    @Test
    @Order(4)
    void pn_inventory_level_audit_has_expected_columns() throws SQLException {
        try (Connection conn = connect();
                Statement stmt = conn.createStatement();
                ResultSet rs =
                        stmt.executeQuery("SELECT * FROM PN_INVENTORY_LEVEL_AUDIT WHERE ROWNUM = 1")) {
            ResultSetMetaData meta = rs.getMetaData();
            Set<String> columns = new HashSet<>();
            for (int i = 1; i <= meta.getColumnCount(); i++) {
                columns.add(meta.getColumnLabel(i).toUpperCase(Locale.ROOT));
            }

            String[] expectedColumns = {
                "PN",
                "LOCATION",
                "CREATED_BY",
                "CREATED_DATE",
                "COMPANY",
                "REORDER_LEVEL",
                "EOQ_LEVEL",
                "MINIMUM_STOCK",
                "MAXIMUM_STOCK",
                "MINIMUM_ORDER",
                "MAXIMUM_ORDER"
            };
            for (String expected : expectedColumns) {
                assertTrue(
                        columns.contains(expected),
                        "PN_INVENTORY_LEVEL_AUDIT is missing expected column "
                                + expected
                                + " (mapped by PnInventoryLevelAudit/PnInventoryLevelAuditPK) —"
                                + " actual columns: "
                                + columns);
            }
        }
    }
}
