package trax.io.writeback.persistence;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;

/**
 * Real {@link OrderNumberSource}: mirrors ARMAC's {@code
 * StockTransferOrderData.getTransactionNo("POSEQ")} verbatim — {@code SELECT
 * pkg_application_function.config_number(?) FROM DUAL} against the eMRO schema, bound to the
 * {@code POSEQ} config code.
 *
 * <p>{@code @DefaultBean} makes this the CDI default everywhere <em>except</em> where an {@code
 * @Alternative} bean of higher priority is active — which is always true in tests, since the
 * {@code POSEQ} package does not exist in the Dev Services schema and calling this bean there
 * would fail. See {@code src/test/java/.../TestOrderNumberSource.java}.
 */
@io.quarkus.arc.DefaultBean
@ApplicationScoped
public class EmroOrderNumberSource implements OrderNumberSource {

    @Inject EntityManager em;

    @Override
    public String nextOrderNumber() {
        Number seq =
                (Number)
                        em.createNativeQuery(
                                        "SELECT pkg_application_function.config_number(?1) FROM DUAL")
                                .setParameter(1, "POSEQ")
                                .getSingleResult();
        return String.valueOf(seq.longValue());
    }
}
