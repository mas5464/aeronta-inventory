package trax.io.writeback.api.batch;

import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import org.eclipse.microprofile.jwt.JsonWebToken;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchRequest;
import trax.io.writeback.api.batch.TransferDtos.TransferBatchResponse;

/**
 * Transfers batch REST facade: {@code POST /api/v1/transfers}. Thin by design — auth enforcement
 * and JWT claim extraction only; all mapping/creator-invocation logic lives in {@link
 * TransferProcessor}, which Task 9's Kafka consumer reuses verbatim. Mirrors {@link
 * RequisitionResource} exactly.
 */
@Path("/api/v1/transfers")
public class TransferResource {

    private static final String DEFAULT_TENANT = "default";

    @Inject TransferProcessor processor;

    @Inject JsonWebToken jwt;

    @POST
    @RolesAllowed("writeback:write")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public TransferBatchResponse submitBatch(TransferBatchRequest request) {
        String tenantId = jwt.getClaim("tenant_id");
        if (tenantId == null || tenantId.isBlank()) {
            tenantId = DEFAULT_TENANT;
        }
        String principal = jwt.getName();
        return processor.process(request, tenantId, principal);
    }
}
