package trax.io.writeback.domain;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.quarkus.runtime.StartupEvent;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.eclipse.microprofile.config.inject.ConfigProperty;
import org.jboss.logging.Logger;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Domain service backing {@code POST /traxio/v1/rollback}: reverts the latest {@code WRITTEN}
 * ledger entry for a {@code (tenant, pn, location)} key to its prior values by delegating a new
 * write through {@link StockLevelWriter#writeItemDedup(WritebackCommand)} — the reverting write
 * goes through the exact same validation/version-chaining/idempotency machinery as any other
 * write, it just carries the OLD values as its payload.
 *
 * <p>Mirrors {@code trax_io_spine.writeback.target.InMemoryWritebackTarget.rollback} (the Python
 * reference implementation): "latest written" is the highest-version ledger row with outcome
 * {@code WRITTEN} (shadowed rows are skipped when searching); a null {@code old_values} on that
 * row (i.e. it was the very first write) means there is nothing to revert to; and a request whose
 * {@code requested_at} is more than {@code writeback.rollback.window-days} days past that row's
 * {@code created_at} is rejected as {@link RollbackStatus#OUTSIDE_WINDOW} without any mutation.
 *
 * <h2>Idempotency of a repeated rollback request</h2>
 *
 * The reverting write's idempotency key is deterministically derived as {@code "rollback:" +
 * <reverted entry's idempotency key>} (never something request-time-derived, e.g. a timestamp) —
 * a second rollback request that targets the SAME reverted ledger entry therefore replays as
 * {@code SKIPPED_DUPLICATE} inside {@link StockLevelWriter}, and this service resolves that by
 * re-fetching the original rollback write's own ledger row (by that same derived key) and
 * reconstructing the {@code ROLLED_BACK} outcome from it — the caller sees the exact same
 * response as the first call. A rollback issued AFTER a subsequent new write is a different,
 * later "latest written" row and reverts THAT one instead (ping-pong is contract-conformant, not
 * a special case).
 *
 * <h2>Reverting-write failure (Java-only case; no Python precedent)</h2>
 *
 * {@code fake_emro}'s in-memory target can never fail the reverting write itself, so
 * agent-spine's contract has no precedent for this path. Contract decision: since the wire
 * {@code status} field only has the three contract values and the Python client
 * ({@code RestWritebackClient}) validates the response body with no status-code check, a writer
 * failure ({@code REJECTED_*}/{@code ERROR} from {@link StockLevelWriter#writeItemDedup}) is
 * reported as HTTP 200 with {@code status = NOTHING_TO_REVERT} and a non-null
 * {@code error_message} explaining the failure (sanitized to a generic message for {@code ERROR},
 * passed through as-is for a {@code REJECTED_*} validation message — mirroring
 * {@code TraxIoResource}'s existing ERROR-sanitization rule). No mutation has occurred in this
 * case beyond whatever {@code writeItemDedup} itself attempted and rolled back.
 */
@ApplicationScoped
public class RollbackService {

    private static final Logger LOG = Logger.getLogger(RollbackService.class);

    @Inject EntityManager em;

    @Inject StockLevelWriter writer;

    @Inject ObjectMapper objectMapper;

    @ConfigProperty(name = "writeback.rollback.window-days", defaultValue = "90")
    int windowDays;

    void onStart(@Observes StartupEvent ev) {
        if (windowDays <= 0) {
            throw new IllegalStateException(
                    "writeback.rollback.window-days must be > 0, got: " + windowDays);
        }
    }

    public RollbackOutcome rollback(RollbackCommand cmd) {
        Optional<WritebackLedger> latest = findLatestWritten(cmd.tenantId(), cmd.pn(), cmd.location());
        if (latest.isEmpty() || latest.get().getOldValuesJson() == null) {
            return RollbackOutcome.nothingToRevert();
        }

        WritebackLedger revert = latest.get();

        if (Duration.between(revert.getCreatedAt(), cmd.requestedAt()).compareTo(Duration.ofDays(windowDays)) > 0) {
            return RollbackOutcome.outsideWindow();
        }

        String revertingIdempotencyKey = "rollback:" + revert.getIdempotencyKey();
        String revertingProvenanceId = "rollback:" + revert.getProvenanceId();

        Map<String, Integer> toValues = fromJson(revert.getOldValuesJson());
        Map<String, Integer> fromValues = fromJson(revert.getNewValuesJson());

        LevelValues levels =
                new LevelValues(
                        toBigDecimal(toValues.get("rop")),
                        toBigDecimal(toValues.get("eoq")),
                        toBigDecimal(toValues.get("safety_stock")),
                        toBigDecimal(toValues.get("max_stock")),
                        null,
                        null,
                        null);

        Provenance provenance =
                new Provenance(
                        cmd.tenantId(),
                        "rollback",
                        null,
                        null,
                        revertingProvenanceId,
                        revertingIdempotencyKey,
                        revert.getTier(),
                        null,
                        cmd.principal());

        WritebackCommand writebackCommand =
                new WritebackCommand(cmd.pn(), cmd.location(), levels, provenance, false);

        ItemResult result = writer.writeItemDedup(writebackCommand);

        return switch (result.status()) {
            case ACCEPTED ->
                    new RollbackOutcome(
                            RollbackStatus.ROLLED_BACK,
                            fromValues,
                            toValues,
                            revert.getVersion(),
                            result.ledgerVersion(),
                            result.writtenAt(),
                            null);
            case SKIPPED_DUPLICATE -> reconstructFromDuplicate(cmd, revert, revertingIdempotencyKey);
            case REJECTED_VALIDATION, REJECTED_UNKNOWN_KEY ->
                    RollbackOutcome.nothingToRevert(
                            "reverting write failed: " + result.message());
            case ERROR -> {
                LOG.errorf(
                        "traxio rollback reverting-write error (tenant=%s, pn=%s, location=%s): %s",
                        cmd.tenantId(), cmd.pn(), cmd.location(), result.message());
                yield RollbackOutcome.nothingToRevert("reverting write failed: internal error");
            }
            case SHADOWED ->
                    // writeItemDedup is always invoked with shadow=false here; unreachable in
                    // practice, but handled explicitly rather than silently falling through.
                    RollbackOutcome.nothingToRevert("reverting write failed: unexpected shadow outcome");
        };
    }

    /**
     * A repeated rollback request for the same reverted entry: {@code writeItemDedup} replayed the
     * reverting write as {@code SKIPPED_DUPLICATE} rather than performing it again. Re-fetch the
     * ORIGINAL rollback write's own ledger row (by the same deterministic {@code "rollback:" + ...}
     * idempotency key) and reconstruct the exact {@code ROLLED_BACK} response the first call
     * returned, rather than exposing the internal dedup mechanics to the caller.
     */
    private RollbackOutcome reconstructFromDuplicate(
            RollbackCommand cmd, WritebackLedger revert, String revertingIdempotencyKey) {
        Optional<WritebackLedger> revertingRow =
                writer.findByIdempotencyKey(cmd.tenantId(), revertingIdempotencyKey);
        if (revertingRow.isEmpty()) {
            // Should not happen (writeItemDedup only returns SKIPPED_DUPLICATE when a ledger row
            // for this exact key already exists), but fail safely rather than throwing.
            return RollbackOutcome.nothingToRevert("reverting write failed: duplicate row not found");
        }
        WritebackLedger row = revertingRow.get();
        return new RollbackOutcome(
                RollbackStatus.ROLLED_BACK,
                fromJson(row.getOldValuesJson()),
                fromJson(row.getNewValuesJson()),
                revert.getVersion(),
                row.getVersion(),
                row.getCreatedAt(),
                null);
    }

    /**
     * Highest-version {@code STOCK_LEVEL} ledger row for {@code (tenant, pn, location)} whose
     * outcome is {@code WRITTEN} — shadowed rows are skipped entirely, mirroring the Python
     * reference's {@code next(e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN)}.
     *
     * <p>Requisition/transfer creates share this key's version chain (D10) but are a different kind
     * of ledger row entirely — they carry a {@code created_ref}, not before/after stock-level
     * values, and are never a valid rollback target. The {@code l.domain = :domain} filter below
     * excludes them so a requisition/transfer row that happens to be the highest-versioned entry for
     * the key never becomes the (nonsensical) rollback target; the search continues past it to the
     * latest actual {@code STOCK_LEVEL} row instead. A null {@code old_values} on THAT row still
     * means "nothing to revert to" (the Python first-write contract) — this filter only changes
     * which row is considered, not that null-old-values semantic.
     */
    private Optional<WritebackLedger> findLatestWritten(String tenantId, String pn, String location) {
        List<WritebackLedger> rows =
                em.createQuery(
                                "select l from WritebackLedger l"
                                        + " where l.tenantId = :tenantId and l.pn = :pn and l.location = :location"
                                        + " and l.outcome = 'WRITTEN'"
                                        + " and l.domain = :domain"
                                        + " order by l.version desc",
                                WritebackLedger.class)
                        .setParameter("tenantId", tenantId)
                        .setParameter("pn", pn)
                        .setParameter("location", location)
                        .setParameter("domain", StockLevelWriter.DOMAIN_STOCK_LEVEL)
                        .setMaxResults(1)
                        .getResultList();
        return rows.isEmpty() ? Optional.empty() : Optional.of(rows.get(0));
    }

    private static BigDecimal toBigDecimal(Integer value) {
        return value == null ? null : BigDecimal.valueOf(value);
    }

    private Map<String, Integer> fromJson(String json) {
        if (json == null) {
            return null;
        }
        try {
            return objectMapper.readValue(json, VALUES_JSON_TYPE);
        } catch (Exception e) {
            throw new IllegalStateException("failed to deserialize values map", e);
        }
    }

    private static final TypeReference<LinkedHashMap<String, Integer>> VALUES_JSON_TYPE =
            new TypeReference<>() {};
}
