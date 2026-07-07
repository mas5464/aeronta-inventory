package trax.io.writeback.api.traxio;

import io.micrometer.core.instrument.MeterRegistry;
import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import java.math.BigDecimal;
import org.eclipse.microprofile.jwt.JsonWebToken;
import org.jboss.logging.Logger;
import trax.io.writeback.api.traxio.TraxIoDtos.TierMapper;
import trax.io.writeback.api.traxio.TraxIoDtos.TraxIoRequest;
import trax.io.writeback.api.traxio.TraxIoDtos.TraxIoResult;
import trax.io.writeback.domain.ItemResult;
import trax.io.writeback.domain.LevelValues;
import trax.io.writeback.domain.Provenance;
import trax.io.writeback.domain.ResultStatus;
import trax.io.writeback.domain.StockLevelWriter;
import trax.io.writeback.domain.WritebackCommand;

/**
 * Trax IO apply facade: {@code POST /traxio/v1/inventory-levels}. Wire-contract-exact counterpart
 * to agent-spine's {@code RestWritebackClient} — field names and status-code semantics are pinned
 * (see the Task 8 brief), NOT the PRD batch facade's conventions (camelCase DTOs, 400 rejections).
 *
 * <p>HTTP mapping deliberately diverges from {@link trax.io.writeback.api.batch.BatchResource}:
 * {@code ACCEPTED}/{@code SHADOWED}/{@code SKIPPED_DUPLICATE} (replayed) all return 200;
 * {@code REJECTED_*}/{@code ERROR} return 422 (never 409 — open-order deferral is out of scope for
 * this facade). {@code ERROR} messages are sanitized before hitting the wire, mirroring {@link
 * trax.io.writeback.api.batch.BatchProcessor}.
 */
@Path("/traxio/v1/inventory-levels")
public class TraxIoResource {

    private static final Logger LOG = Logger.getLogger(TraxIoResource.class);

    static final String SOURCE = "agent-spine";

    static final String TRAXIO_FACADE = "traxio";

    private static final String METRIC_ITEMS = "writeback.items";

    @Inject StockLevelWriter writer;

    @Inject JsonWebToken jwt;

    @Inject MeterRegistry meterRegistry;

    @POST
    @RolesAllowed("writeback:write")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response apply(TraxIoRequest request) {
        Integer tier;
        try {
            tier = TierMapper.toDomain(request.tier());
        } catch (TierMapper.UnknownTierException e) {
            return Response.status(422)
                    .entity(failed(request, e.getMessage()))
                    .build();
        }

        String principal = jwt.getName();
        Provenance provenance =
                new Provenance(
                        request.tenantId(),
                        SOURCE,
                        null,
                        null,
                        request.provenanceId(),
                        request.idempotencyKey(),
                        tier,
                        null,
                        principal);

        LevelValues levels =
                new LevelValues(
                        toBigDecimal(request.rop()),
                        toBigDecimal(request.eoq()),
                        toBigDecimal(request.safetyStock()),
                        toBigDecimal(request.maxStock()),
                        null,
                        null,
                        null);

        WritebackCommand cmd =
                new WritebackCommand(request.pn(), request.location(), levels, provenance, request.shadow());

        ItemResult result = writer.writeItemDedup(cmd);
        meterRegistry.counter(METRIC_ITEMS, "status", result.status().name(), "facade", TRAXIO_FACADE).increment();
        return toResponse(request, result);
    }

    private Response toResponse(TraxIoRequest request, ItemResult result) {
        ResultStatus effectiveStatus =
                result.status() == ResultStatus.SKIPPED_DUPLICATE ? result.originalStatus() : result.status();

        if (effectiveStatus == ResultStatus.ACCEPTED || effectiveStatus == ResultStatus.SHADOWED) {
            TraxIoResult body =
                    new TraxIoResult(
                            request.tenantId(),
                            request.pn(),
                            request.location(),
                            TraxIoDtos.wireStatusFor(effectiveStatus),
                            result.oldValues(),
                            result.newValues(),
                            result.writtenAt(),
                            null);
            return Response.ok(body).build();
        }

        String errorMessage = result.message();
        if (result.status() == ResultStatus.ERROR) {
            LOG.errorf(
                    "traxio writeback item error (tenant=%s, pn=%s, location=%s): %s",
                    request.tenantId(), request.pn(), request.location(), result.message());
            errorMessage = "internal error";
        }
        return Response.status(422).entity(failed(request, errorMessage)).build();
    }

    private static TraxIoResult failed(TraxIoRequest request, String errorMessage) {
        return new TraxIoResult(
                request.tenantId(), request.pn(), request.location(), "failed", null, null, null, errorMessage);
    }

    private static BigDecimal toBigDecimal(Integer value) {
        return value == null ? null : BigDecimal.valueOf(value);
    }
}
