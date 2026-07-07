package trax.io.writeback.api.batch;

import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import java.util.List;
import org.eclipse.microprofile.jwt.JsonWebToken;
import trax.io.writeback.api.batch.RunResultsDtos.RunResultEntry;
import trax.io.writeback.persistence.TraxRepository;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Replay facade (D16, spec-thin by design): {@code GET /api/v1/runs/{runId}/results}. Returns a
 * top-level camelCase JSON array (the PRD batch surface's convention — see {@link BatchResource}
 * — not the Trax IO seam's) of every {@code WRITEBACK_LEDGER} row for {@code runId} scoped to the
 * caller's tenant (the {@code tenant_id} JWT claim, defaulting to {@code "default"} exactly like
 * {@link BatchResource}), ordered oldest-first by {@code createdAt} then {@code rowId}. An unknown
 * {@code runId} (or one with no rows for the caller's tenant) returns {@code []} with HTTP 200 —
 * there is no distinct "run not found" signal.
 *
 * <p><b>Scope note (read this before treating this as a full request replay):</b> this endpoint
 * reads the LEDGER, and the ledger only records rows that were actually APPLIED — written for
 * real, or shadow-written under an onboarding tenant (see {@code StockLevelWriter}'s {@code
 * ledger.setOutcome(shadow ? "SHADOWED" : "WRITTEN")}). Rows a processor REJECTED (unknown PN/
 * location, validation failure, etc.) or that errored before a ledger row could be written are
 * deliberately never ledgered (see {@code BatchProcessor}/{@code RequisitionProcessor}/{@code
 * TransferProcessor}), so this endpoint shows what was applied for a run — never the full
 * original request. A caller that needs the complete original request, including rejected rows,
 * must keep its own copy of what it submitted; this service does not retain one.
 *
 * <p>Full re-drive of a run (as opposed to reading back what happened) is NOT this endpoint's
 * job — it is a Kafka-level operation: replay the {@code writeback-in} topic for the relevant
 * offsets (bounded by topic retention) and reset the {@code emro-writeback-java} consumer group's
 * offset to before them. {@link trax.io.writeback.ingest.WritebackConsumer} makes this safe to do
 * more than once: every row still flows through the same idempotency-keyed, effectively-once
 * ledger write path (see {@link WritebackLedger}'s Javadoc and {@code StockLevelWriter}/{@code
 * RequisitionCreator}/{@code TransferCreator}'s {@code SKIPPED_DUPLICATE} handling), so
 * re-processing an already-applied row is a no-op rather than a double-write.
 */
@Path("/api/v1/runs/{runId}/results")
public class RunResultsResource {

    private static final String DEFAULT_TENANT = "default";

    @Inject TraxRepository repo;

    @Inject JsonWebToken jwt;

    @GET
    @RolesAllowed("writeback:read")
    @Produces(MediaType.APPLICATION_JSON)
    public List<RunResultEntry> results(@PathParam("runId") String runId) {
        String tenantId = jwt.getClaim("tenant_id");
        if (tenantId == null || tenantId.isBlank()) {
            tenantId = DEFAULT_TENANT;
        }
        return repo.findLedgerRowsForRun(tenantId, runId).stream().map(RunResultsResource::toDto).toList();
    }

    private static RunResultEntry toDto(WritebackLedger ledger) {
        return new RunResultEntry(
                ledger.getRowId(),
                ledger.getDomain(),
                ledger.getPn(),
                ledger.getLocation(),
                ledger.getOutcome(),
                ledger.getCreatedRef(),
                ledger.getVersion() == null ? null : ledger.getVersion().intValue(),
                ledger.getParentVersion() == null ? null : ledger.getParentVersion().intValue(),
                ledger.getMessage(),
                ledger.getCreatedAt());
    }
}
