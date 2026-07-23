package trax.io.writeback.persistence;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;

/**
 * Real {@link RequisitionNumberSource}: mirrors ARMAC's {@code RequisitionData.ReqSeq()}
 * verbatim — {@code SELECT PKG_APPLICATION_FUNCTION.config_number('REQSEQ') FROM DUAL} against
 * the eMRO schema.
 *
 * <p>{@code @DefaultBean} makes this the CDI default everywhere <em>except</em> where an {@code
 * @Alternative} bean of higher priority is active — which is always true in tests, since the
 * {@code REQSEQ} package does not exist in the Dev Services schema and calling this bean there
 * would fail. See {@code src/test/java/.../TestRequisitionNumberSource.java}.
 */
@io.quarkus.arc.DefaultBean
@ApplicationScoped
public class EmroRequisitionNumberSource implements RequisitionNumberSource {

    @Inject EntityManager em;

    @Override
    public String nextRequisitionNumber() {
        Number seq =
                (Number)
                        em.createNativeQuery(
                                        "SELECT PKG_APPLICATION_FUNCTION.config_number('REQSEQ') FROM DUAL")
                                .getSingleResult();
        return String.valueOf(seq.longValue());
    }
}
