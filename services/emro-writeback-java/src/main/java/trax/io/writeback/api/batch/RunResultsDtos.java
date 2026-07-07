package trax.io.writeback.api.batch;

import java.time.Instant;

/** JSON wire records for the run-results replay facade (GET /api/v1/runs/{runId}/results). */
public final class RunResultsDtos {

    private RunResultsDtos() {}

    /**
     * One ledger row for a run, camelCase (this is the PRD batch surface's convention — see
     * {@link BatchDtos} — not the Trax IO seam's snake_case). {@code status} is the ledger {@code
     * OUTCOME} string verbatim (e.g. {@code WRITTEN}, {@code SHADOWED}) — NOT the PRD batch
     * facade's per-row {@code RowResult.status} ({@code ACCEPTED}/{@code REJECTED_*}/{@code
     * ERROR}), since rejected/errored rows are never ledgered in the first place (see {@link
     * RunResultsResource}).
     */
    public record RunResultEntry(
            Long rowId,
            String domain,
            String pn,
            String location,
            String status,
            String createdRef,
            Integer version,
            Integer parentVersion,
            String message,
            Instant createdAt) {}
}
