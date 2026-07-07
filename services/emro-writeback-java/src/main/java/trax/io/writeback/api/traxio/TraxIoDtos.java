package trax.io.writeback.api.traxio;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.time.Instant;
import java.util.Map;
import trax.io.writeback.domain.ResultStatus;

/**
 * Wire-contract-exact JSON records for the Trax IO apply facade ({@code POST
 * /traxio/v1/inventory-levels}). Field names must match {@code
 * trax_io_spine.writeback.contracts.WritebackRequest}/{@code WritebackResult} exactly
 * (snake_case) — every field carries an explicit {@link JsonProperty} rather than relying on a
 * global naming strategy, since the sibling batch facade uses camelCase and this module must not
 * inherit that.
 */
public final class TraxIoDtos {

    private TraxIoDtos() {}

    public record TraxIoRequest(
            @JsonProperty("tenant_id") String tenantId,
            @JsonProperty("pn") String pn,
            @JsonProperty("location") String location,
            @JsonProperty("rop") Integer rop,
            @JsonProperty("eoq") Integer eoq,
            @JsonProperty("safety_stock") Integer safetyStock,
            @JsonProperty("max_stock") Integer maxStock,
            @JsonProperty("provenance_id") String provenanceId,
            @JsonProperty("idempotency_key") String idempotencyKey,
            @JsonProperty("tier") String tier,
            @JsonProperty("shadow") boolean shadow) {}

    public record TraxIoResult(
            @JsonProperty("tenant_id") String tenantId,
            @JsonProperty("pn") String pn,
            @JsonProperty("location") String location,
            @JsonProperty("status") String status,
            @JsonProperty("old_values") Map<String, Integer> oldValues,
            @JsonProperty("new_values") Map<String, Integer> newValues,
            @JsonProperty("written_at") Instant writtenAt,
            @JsonProperty("error_message") String errorMessage) {}

    /**
     * Wire-contract-exact counterpart to {@code trax_io_spine.contracts.HistoryEntry} (all 14
     * fields, snake_case). Returned as a top-level JSON array by {@code GET /traxio/v1/history}
     * (never wrapped in an envelope) so {@code RestWritebackClient.get_history} can {@code
     * HistoryEntry.model_validate(e) for e in resp.json()} directly.
     *
     * <p>{@code tier} is the bare {@code Integer} (1|2|3|null) — {@code AutonomyTier} is a Python
     * {@code IntEnum}, so the wire form is the int, NOT the {@code TraxIoRequest.tier} name string
     * this facade's apply side accepts. Use {@link TierMapper#toWire} to convert the ledger's
     * domain {@code Integer} tier for this field.
     *
     * <p>{@code provenance_id} is a required {@code str} on the Python side (never {@code null}):
     * the ledger table has no {@code PROVENANCE_ID} column, so this is always the empty string
     * {@code ""} here.
     */
    public record HistoryEntryDto(
            @JsonProperty("tenant_id") String tenantId,
            @JsonProperty("pn") String pn,
            @JsonProperty("location") String location,
            @JsonProperty("version") int version,
            @JsonProperty("status") String status,
            @JsonProperty("old_values") Map<String, Integer> oldValues,
            @JsonProperty("new_values") Map<String, Integer> newValues,
            @JsonProperty("provenance_id") String provenanceId,
            @JsonProperty("tier") Integer tier,
            @JsonProperty("agent_version") String agentVersion,
            @JsonProperty("changed_by_principal") String changedByPrincipal,
            @JsonProperty("idempotency_key") String idempotencyKey,
            @JsonProperty("parent_version") Integer parentVersion,
            @JsonProperty("changed_at") Instant changedAt) {}

    /**
     * Maps the Python {@code AutonomyTier} wire value to the domain's {@code Integer} tier.
     * Defined once here so a future facade (e.g. a reverse mapping back to the wire form) can
     * reuse it rather than re-deriving the mapping inline.
     *
     * <p>The wire accepts BOTH forms, because they both exist in the field: the task brief pins
     * the tier names {@code "advisor"|"bounded"|"autonomous"}, but the actual Python client
     * ({@code RestWritebackClient} posting {@code WritebackRequest.model_dump(mode="json")})
     * serializes {@code AutonomyTier} — an {@code IntEnum} — as the bare integers {@code 1|2|3}
     * (verified empirically against agent-spine). Jackson coerces a JSON integer bound to a
     * {@code String} record component into {@code "1"|"2"|"3"}, so both spellings arrive here as
     * strings and both map to the same domain tier.
     */
    public static final class TierMapper {
        private TierMapper() {}

        /** Thrown when a non-null tier value is not a recognized {@code AutonomyTier} spelling. */
        public static final class UnknownTierException extends RuntimeException {
            public UnknownTierException(String tier) {
                super("unrecognized tier: " + tier);
            }
        }

        public static Integer toDomain(String tier) {
            if (tier == null) {
                return null;
            }
            return switch (tier) {
                case "advisor", "1" -> 1;
                case "bounded", "2" -> 2;
                case "autonomous", "3" -> 3;
                default -> throw new UnknownTierException(tier);
            };
        }

        /** Inverse mapping (domain int → wire tier name); Task 9 reads history back to the wire. */
        public static String toWire(Integer tier) {
            if (tier == null) {
                return null;
            }
            return switch (tier) {
                case 1 -> "advisor";
                case 2 -> "bounded";
                case 3 -> "autonomous";
                default -> throw new IllegalArgumentException("unrecognized domain tier: " + tier);
            };
        }
    }

    /**
     * Maps a {@code WritebackLedger.getOutcome()} value ({@code "WRITTEN"|"SHADOWED"}, set once at
     * the original winning write) to the lowercase wire {@code status} string the history facade
     * emits — matching the Python {@code WritebackStatus} enum values exactly.
     */
    public static String wireStatusForOutcome(String outcome) {
        return "SHADOWED".equals(outcome) ? "shadowed" : "written";
    }

    /** Maps a domain {@link ResultStatus} to the wire {@code status} string on this facade. */
    public static String wireStatusFor(ResultStatus status) {
        return switch (status) {
            case ACCEPTED -> "written";
            case SHADOWED -> "shadowed";
            case SKIPPED_DUPLICATE ->
                    throw new IllegalArgumentException(
                            "SKIPPED_DUPLICATE must be resolved via originalStatus before mapping to wire status");
            case REJECTED_VALIDATION, REJECTED_UNKNOWN_KEY, ERROR -> "failed";
        };
    }
}
