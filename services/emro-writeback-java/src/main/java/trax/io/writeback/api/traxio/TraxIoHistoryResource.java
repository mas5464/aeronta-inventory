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
import trax.io.writeback.api.traxio.TraxIoDtos.HistoryEntryDto;
import trax.io.writeback.domain.StockLevelWriter;
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
 */
@Path("/traxio/v1/history")
public class TraxIoHistoryResource {

    private static final TypeReference<LinkedHashMap<String, Integer>> VALUES_JSON_TYPE =
            new TypeReference<>() {};

    @Inject StockLevelWriter writer;

    @Inject ObjectMapper objectMapper;

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

    private HistoryEntryDto toHistoryEntryDto(WritebackLedger ledger) {
        return new HistoryEntryDto(
                ledger.getTenantId(),
                ledger.getPn(),
                ledger.getLocation(),
                ledger.getVersion().intValue(),
                TraxIoDtos.wireStatusForOutcome(ledger.getOutcome()),
                parseValues(ledger, ledger.getOldValuesJson()),
                parseValues(ledger, ledger.getNewValuesJson()),
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
