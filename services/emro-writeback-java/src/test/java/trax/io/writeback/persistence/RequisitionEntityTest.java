package trax.io.writeback.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import java.util.Date;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import jakarta.transaction.Transactional;
import org.junit.jupiter.api.Test;

/**
 * Persistence round-trip proof for the lifted requisition entities: Hibernate's {@code
 * drop-and-create} (test profile) creates {@code REQUISITION_HEADER}/{@code REQUISITION_DETAIL}
 * from the entity metadata alone (no Flyway migration owns these eMRO-native tables), so a
 * committed insert-then-read-back is enough to prove the four mechanical changes (package move,
 * relationship removal, writable PK columns, verbatim column names) didn't break mapping.
 */
@QuarkusTest
class RequisitionEntityTest {

    @Inject EntityManager em;

    @Test
    @Transactional
    void requisition_header_and_detail_round_trip() {
        long requisitionNo = 900001L;

        RequisitionHeader header = new RequisitionHeader();
        header.setRequisition(requisitionNo);
        header.setRequisitionDescription("As per RIOSYS Recommendation");
        header.setRequistionType("REOR");
        header.setRequesterLocation("JFK");
        header.setStatus("OPEN");
        header.setPriority("REOR");
        header.setCreatedBy("TRAX_IFACE");
        header.setCreatedDate(new Date());
        em.persist(header);

        RequisitionDetailPK detailId = new RequisitionDetailPK();
        detailId.setRequisition(requisitionNo);
        detailId.setRequisitionLine(1L);

        RequisitionDetail detail = new RequisitionDetail();
        detail.setId(detailId);
        detail.setPn("PN-900001");
        detail.setLocation("JFK");
        detail.setQtyRequire(BigDecimal.TEN);
        detail.setStatus("OPEN");
        detail.setCreatedBy("TRAX_IFACE");
        detail.setCreatedDate(new Date());
        em.persist(detail);

        em.flush();
        em.clear();

        RequisitionHeader foundHeader = em.find(RequisitionHeader.class, requisitionNo);
        assertEquals(requisitionNo, foundHeader.getRequisition());
        assertEquals("As per RIOSYS Recommendation", foundHeader.getRequisitionDescription());
        assertEquals("REOR", foundHeader.getRequistionType());
        assertEquals("OPEN", foundHeader.getStatus());

        RequisitionDetail foundDetail = em.find(RequisitionDetail.class, detailId);
        assertEquals(requisitionNo, foundDetail.getId().getRequisition());
        assertEquals(1L, foundDetail.getId().getRequisitionLine());
        assertEquals("PN-900001", foundDetail.getPn());
        assertEquals("JFK", foundDetail.getLocation());
        assertEquals(0, BigDecimal.TEN.compareTo(foundDetail.getQtyRequire()));
    }
}
