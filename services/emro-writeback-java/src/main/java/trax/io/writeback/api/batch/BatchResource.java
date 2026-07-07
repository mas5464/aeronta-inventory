package trax.io.writeback.api.batch;

import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import org.eclipse.microprofile.jwt.JsonWebToken;
import trax.io.writeback.api.batch.BatchDtos.BatchRequest;
import trax.io.writeback.api.batch.BatchDtos.BatchResponse;

/**
 * PRD batch REST facade: {@code POST /api/v1/stock-levels}. Thin by design — auth enforcement and
 * JWT claim extraction only; all mapping/writer-invocation logic lives in {@link BatchProcessor},
 * which Task 10's Kafka consumer reuses verbatim.
 */
@Path("/api/v1/stock-levels")
public class BatchResource {

    private static final String DEFAULT_TENANT = "default";

    @Inject BatchProcessor processor;

    @Inject JsonWebToken jwt;

    @POST
    @RolesAllowed("writeback:write")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public BatchResponse submitBatch(BatchRequest request) {
        String tenantId = jwt.getClaim("tenant_id");
        if (tenantId == null || tenantId.isBlank()) {
            tenantId = DEFAULT_TENANT;
        }
        String principal = jwt.getName();
        return processor.process(request, tenantId, principal);
    }
}
