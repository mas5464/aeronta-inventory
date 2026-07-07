package trax.io.writeback.api.traxio;

import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import trax.io.writeback.api.traxio.TraxIoDtos.RollbackRequestDto;
import trax.io.writeback.api.traxio.TraxIoDtos.RollbackResultDto;
import trax.io.writeback.domain.RollbackCommand;
import trax.io.writeback.domain.RollbackOutcome;
import trax.io.writeback.domain.RollbackService;

/**
 * Trax IO rollback facade: {@code POST /traxio/v1/rollback}. Wire-contract-exact counterpart to
 * agent-spine's {@code RestWritebackClient.rollback} / {@code fake_emro}'s {@code POST /rollback}
 * — field names and status-code semantics are pinned (see the Task 2 brief), NOT the PRD batch
 * facade's conventions.
 *
 * <p>Unlike {@link TraxIoResource} (which maps {@code REJECTED_*}/{@code ERROR} to HTTP 422), this
 * endpoint ALWAYS returns HTTP 200 for {@code rolled_back}/{@code outside_window}/
 * {@code nothing_to_revert} — {@code fake_emro}'s {@code POST /rollback} does the same (see
 * {@code trax_io_spine.writeback.fake_emro.rollback}), and the Python {@code RestWritebackClient}
 * validates the response BODY with no status-code check, so a non-200 here would simply be an
 * unnecessary wire divergence. A reverting-write failure (a Java-only case with no Python
 * precedent — see {@link RollbackService}'s Javadoc) is folded into {@code nothing_to_revert} with
 * a non-null {@code error_message} rather than a distinct HTTP status.
 *
 * <p>{@code principal} defaults to {@code "planner"} when absent/blank, matching the Python
 * {@code RollbackRequest.principal: str = "planner"} default.
 */
@Path("/traxio/v1/rollback")
public class TraxIoRollbackResource {

    static final String DEFAULT_PRINCIPAL = "planner";

    @Inject RollbackService rollbackService;

    @POST
    @RolesAllowed("writeback:write")
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response rollback(RollbackRequestDto request) {
        String principal =
                request.principal() == null || request.principal().isBlank()
                        ? DEFAULT_PRINCIPAL
                        : request.principal();

        RollbackCommand cmd =
                new RollbackCommand(
                        request.tenantId(),
                        request.pn(),
                        request.location(),
                        request.reason(),
                        principal,
                        request.requestedAt());

        RollbackOutcome outcome = rollbackService.rollback(cmd);

        RollbackResultDto body =
                new RollbackResultDto(
                        request.tenantId(),
                        request.pn(),
                        request.location(),
                        TraxIoDtos.wireStatusFor(outcome.status()),
                        outcome.fromValues(),
                        outcome.toValues(),
                        outcome.revertedFromVersion(),
                        outcome.newVersion(),
                        outcome.rolledBackAt(),
                        outcome.errorMessage());

        return Response.ok(body).build();
    }
}
