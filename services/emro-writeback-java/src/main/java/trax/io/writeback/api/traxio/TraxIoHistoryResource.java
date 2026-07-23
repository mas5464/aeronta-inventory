package trax.io.writeback.api.traxio;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import trax.io.writeback.api.traxio.TraxIoDtos.HistoryEntryDto;
import trax.io.writeback.api.traxio.TraxIoDtos.OutOfBandHistoryEntryDto;
import trax.io.writeback.domain.StockLevelWriter;
import trax.io.writeback.persistence.PnInventoryLevelAudit;
import trax.io.writeback.persistence.TraxRepository;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Trax IO history facade: {@code GET /traxio/v1/history?tenant_id=&pn=&location=}. Returns a
 * top-level JSON array (never an envelope) of {@link HistoryEntryDto}, ledger-backed
 * (ordered oldest-first, per {@link StockLevelWriter#history}) — the wire-contract-exact
 * counterpart to agent-spine's {@code RestWritebackClient.get_history} / {@code
 * HistoryEntry.model_validate(e) for e in resp.json()}.
 *
 * <p>A separate resource class from {@link TraxIoResource} (rather than a second method there)
 * because JAX-RS concatenates class- and method-level {@code @Path} segments — this endpoint's
 * path does not nest under {@code /traxio/v1/inventory-levels}.
 *
 * <p><b>Foreign-domain rows excluded (see {@link StockLevelWriter#history} for the full
 * rationale):</b> requisition/transfer creates share this key's ledger version chain (D10) but
 * are never returned by this endpoint — {@link StockLevelWriter#history} scopes its query to
 * {@code STOCK_LEVEL}-domain rows only. This can make the returned {@code version} sequence show
 * gaps and a row's {@code parent_version} reference a version number that belongs to an excluded
 * row; both are contract-valid (plain ints) and a documented consequence of the filter.
 */
@Path("/traxio/v1/history")
public class TraxIoHistoryResource {

    private static final TypeReference<LinkedHashMap<String, Integer>> VALUES_JSON_TYPE =
            new TypeReference<>() {};

    @Inject StockLevelWriter writer;

    @Inject ObjectMapper objectMapper;

    @Inject TraxRepository repo;

    @GET
    @RolesAllowed("writeback:read")
    @Produces(MediaType.APPLICATION_JSON)
    public List<HistoryEntryDto> history(
            @QueryParam("tenant_id") String tenantId,
            @QueryParam("pn") String pn,
            @QueryParam("location") String location) {
        List<WritebackLedger> ledgerRows = writer.history(tenantId, pn, location);
        return ledgerRows.stream().map(this::toHistoryEntryDto).toList();
    }

    /**
     * Out-of-band history: {@code GET /traxio/v1/history/out-of-band?tenant_id=&pn=&location=}.
     * Returns {@code PN_INVENTORY_LEVEL_AUDIT} rows for {@code (pn, location)} whose {@code
     * MODIFIED_BY} is NOT one of this service's own writing principals — i.e. edits made by some
     * OTHER eMRO writer (a planner, another integration), newest-first. This is a completely
     * separate surface from {@link #history}: no {@code version} field, not ledger-backed, and
     * NOT the {@link HistoryEntryDto} shape (spec D13) — fabricating a monotonic version for an
     * out-of-band edit would corrupt the ledger's own version sequence.
     *
     * <p>{@code tenant_id} is accepted here only for interface symmetry with {@link #history}.
     * {@code PN_INVENTORY_LEVEL_AUDIT} itself carries no tenant column (eMRO is a single-tenant
     * database per install) — the parameter is used solely to scope the ledger-principals
     * subquery ({@link TraxRepository#findOutOfBandAudits}) that determines which {@code
     * MODIFIED_BY} values count as "this service's own", not to filter the audit rows themselves.
     */
    @GET
    @Path("out-of-band")
    @RolesAllowed("writeback:read")
    @Produces(MediaType.APPLICATION_JSON)
    public List<OutOfBandHistoryEntryDto> outOfBandHistory(
            @QueryParam("tenant_id") String tenantId,
            @QueryParam("pn") String pn,
            @QueryParam("location") String location) {
        return repo.findOutOfBandAudits(tenantId, pn, location).stream()
                .map(TraxIoHistoryResource::toOutOfBandHistoryEntryDto)
                .toList();
    }

    private static OutOfBandHistoryEntryDto toOutOfBandHistoryEntryDto(PnInventoryLevelAudit audit) {
        return new OutOfBandHistoryEntryDto(
                audit.getId().getPn(),
                audit.getId().getLocation(),
                audit.getModifiedBy(),
                audit.getModifiedDate(),
                audit.getReorderLevel(),
                audit.getEoqLevel(),
                audit.getMinimumStock(),
                audit.getMaximumStock(),
                audit.getMinimumOrder(),
                audit.getMaximumOrder());
    }

    private HistoryEntryDto toHistoryEntryDto(WritebackLedger ledger) {
        // Every ledgered row has a non-null NEW_VALUES_JSON by construction: StockLevelWriter's
        // writeItem always computes and persists a "would-be resulting values" map (falling back
        // to the pre-existing row's values, or nulls-within-the-map for a brand-new row) for
        // BOTH real and shadow writes — the map itself is never null, only individual entries
        // within it may be. Later create-domain writers (requisition/transfer) must preserve this
        // invariant. The defensive check below exists so a future violation fails loudly with the
        // offending row identified, rather than silently propagating a null into the wire DTO.
        Map<String, Integer> newValues =
                Objects.requireNonNull(
                        parseValues(ledger, ledger.getNewValuesJson()),
                        "ledger row "
                                + ledger.getId()
                                + " (pn="
                                + ledger.getPn()
                                + ", location="
                                + ledger.getLocation()
                                + ", version="
                                + ledger.getVersion()
                                + ") has null new_values");
        return new HistoryEntryDto(
                ledger.getTenantId(),
                ledger.getPn(),
                ledger.getLocation(),
                ledger.getVersion().intValue(),
                TraxIoDtos.wireStatusForOutcome(ledger.getOutcome()),
                parseValues(ledger, ledger.getOldValuesJson()),
                newValues,
                ledger.getProvenanceId() == null ? "" : ledger.getProvenanceId(),
                ledger.getTier(),
                ledger.getAgentVersion(),
                ledger.getPrincipal(),
                ledger.getIdempotencyKey(),
                ledger.getParentVersion() == null ? null : ledger.getParentVersion().intValue(),
                ledger.getCreatedAt());
    }

    private Map<String, Integer> parseValues(WritebackLedger ledger, String json) {
        if (json == null) {
            return null;
        }
        try {
            return objectMapper.readValue(json, VALUES_JSON_TYPE);
        } catch (Exception e) {
            throw new IllegalStateException(
                    "failed to deserialize values map (ledger id="
                            + ledger.getId()
                            + ", pn="
                            + ledger.getPn()
                            + ", location="
                            + ledger.getLocation()
                            + ", version="
                            + ledger.getVersion()
                            + ")",
                    e);
        }
    }
}
