package trax.io.writeback.domain;

import io.quarkus.narayana.jta.QuarkusTransaction;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.persistence.EntityManager;
import jakarta.transaction.Transactional;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Date;
import java.util.Optional;
import trax.io.writeback.persistence.LocationMaster;
import trax.io.writeback.persistence.OrderDetail;
import trax.io.writeback.persistence.OrderDetailAudit;
import trax.io.writeback.persistence.OrderDetailAuditPK;
import trax.io.writeback.persistence.OrderDetailPK;
import trax.io.writeback.persistence.OrderHeader;
import trax.io.writeback.persistence.OrderHeaderAudit;
import trax.io.writeback.persistence.OrderHeaderAuditPK;
import trax.io.writeback.persistence.OrderHeaderPK;
import trax.io.writeback.persistence.OrderNumberSource;
import trax.io.writeback.persistence.PnMaster;
import trax.io.writeback.persistence.TraxRepository;
import trax.io.writeback.persistence.WritebackLedger;

/**
 * Domain service that creates an eMRO stock-transfer order (header + a single line-1 detail row)
 * moving a {@code (PN, qty)} from one location to another, ported from ARMAC's {@code
 * StockTransferOrderData.createOrderHeader} — see {@code
 * /Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/data/StockTransferOrderData.java}.
 *
 * <p>Structurally this mirrors {@link RequisitionCreator}'s {@code createDedup}/{@code create}
 * split verbatim — same self-invocation/transactional-interception design, same constraint
 * classification via {@link StockLevelWriter#classifyConstraintViolation}. See that class's
 * Javadoc for the full rationale; this class reuses it rather than re-deriving it.
 *
 * <p><b>Version chaining (D10):</b> the ledger row this class writes shares the SAME per-{@code
 * (tenantId, pn, location)} version space as {@link StockLevelWriter} and {@link
 * RequisitionCreator}. For a transfer, the chained {@code location} key is the {@code
 * fromLocation} — stock physically leaves that location, so a transfer's ledger entry
 * chronologically interleaves with any other write (stock-level or requisition) against the
 * source location's inventory. The {@code toLocation} is not separately chained; a transfer's
 * effect on the destination is out of scope for this slice (see below).
 *
 * <p><b>Header status (documented deviation from ARMAC):</b> ARMAC's {@code createOrderHeader}
 * inserts the header {@code OPEN}, then — after building the detail row, PN-inventory-history
 * rows, and the receiving-side inventory-detail mutation (none of which exist in this slice, see
 * below) — flips the header to {@code CLOSED} and re-persists it. Since none of that
 * receiving-side work happens here, this class leaves the header (and detail) {@code OPEN} rather
 * than faking a {@code CLOSED} status the transfer never actually completed.
 *
 * <p><b>Out of scope (documented, matches {@link RequisitionCreator}'s precedent):</b>
 *
 * <ul>
 *   <li><b>PnInterchangeable resolution</b> — same rationale as {@code RequisitionCreator}: the
 *       Trax IO optimizer sends already-resolved PNs.
 *   <li><b>Batch/inventory-detail resolution</b> — ARMAC resolves {@code parameter.getBatchNumber()}
 *       against {@code PnInventoryDetail} to source {@code sn}/{@code roBin}/{@code qtyAvailable}
 *       and validates the batch's PN/location/available-qty against the request (see {@code
 *       checkMinData}). This project has no {@code PnInventoryDetail} entity; {@link
 *       TransferCommand#batch()} is a caller-supplied opaque string persisted verbatim onto the
 *       detail row's {@code BATCH} column, with no cross-check against on-hand inventory.
 *   <li><b>PnInventoryHistory rows / receiving-side inventory mutation</b> — ARMAC's {@code
 *       setPnInevtoryHistory}/{@code setPnInevtoryDetail(SN)} write {@code TS/CREATE} and {@code
 *       TS/RECEIVING} history rows and mutate (or split/delete) the {@code PnInventoryDetail} row
 *       to actually move stock between locations. No {@code PnInventoryHistory}/{@code
 *       PnInventoryDetail} entities exist in this project's lifted set, so the physical stock
 *       move itself is out of scope for this slice — this class creates the transfer order
 *       (header + detail + audits) that a downstream process would consume, matching the
 *       ARMAC-parity boundary drawn for #6 writeback generally (creates eMRO records; does not
 *       reimplement eMRO's own inventory-movement side effects).
 *   <li><b>Print-server call</b> — ARMAC's receiving branch optionally posts a print job via
 *       {@code PrintPoster}/JMS when {@code Trax_Print_URL} is set. No analogue exists here.
 * </ul>
 */
@ApplicationScoped
public class TransferCreator {

    static final String AGENT_VERSION = "emro-writeback-java/1.0";

    /** {@link WritebackLedger#getDomain()} value for every row this creator ledgers. */
    public static final String DOMAIN_TRANSFER = "TRANSFER";

    /** {@link OrderHeaderPK#getOrderType()} value for every stock-transfer order this creator creates. */
    public static final String ORDER_TYPE_TRANSFER = "TS";

    /** Transfer detail line number this slice always creates (single-line transfers). */
    private static final int DETAIL_LINE = 1;

    private static final int MAX_VERSION_CONFLICT_ATTEMPTS = 3;
    private static final long VERSION_CONFLICT_BACKOFF_MILLIS = 25L;

    @Inject TraxRepository repo;

    @Inject OrderNumberSource numberSource;

    @Inject EntityManager em;

    /**
     * Non-transactional wrapper around {@link #create(TransferCommand)} — callers use this, never
     * {@code create} directly. See {@link StockLevelWriter#writeItemDedup} for the full rationale.
     */
    public TransferResult createDedup(TransferCommand cmd) {
        Exception lastVersionConflict = null;
        for (int attempt = 1; attempt <= MAX_VERSION_CONFLICT_ATTEMPTS; attempt++) {
            try {
                return create(cmd);
            } catch (Exception e) {
                StockLevelWriter.ConstraintViolation violation = StockLevelWriter.classifyConstraintViolation(e);
                if (violation == StockLevelWriter.ConstraintViolation.VERSION_CONFLICT
                        || violation == StockLevelWriter.ConstraintViolation.LEVEL_ROW_RACE) {
                    lastVersionConflict = e;
                    if (attempt < MAX_VERSION_CONFLICT_ATTEMPTS) {
                        backoff();
                        continue;
                    }
                    return errorResult(e.getMessage(), cmd.provenance().rowId());
                }
                if (violation == StockLevelWriter.ConstraintViolation.IDEMPOTENCY_DUPLICATE
                        || isUnexpectedPersistenceFailure(e)) {
                    Optional<WritebackLedger> winner = QuarkusTransaction.requiringNew()
                            .call(() -> findByIdempotencyKey(
                                    cmd.provenance().tenantId(), cmd.provenance().idempotencyKey()));
                    if (winner.isPresent()) {
                        return skippedDuplicateFrom(cmd, winner.get());
                    }
                }
                return errorResult(e.getMessage(), cmd.provenance().rowId());
            }
        }
        return errorResult(
                lastVersionConflict == null ? "exhausted retries" : lastVersionConflict.getMessage(),
                cmd.provenance().rowId());
    }

    private static boolean isUnexpectedPersistenceFailure(Throwable e) {
        Throwable cause = e;
        while (cause != null) {
            if (cause instanceof java.sql.SQLIntegrityConstraintViolationException) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }

    private static void backoff() {
        try {
            Thread.sleep(VERSION_CONFLICT_BACKOFF_MILLIS);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        }
    }

    @Transactional(Transactional.TxType.REQUIRES_NEW)
    public TransferResult create(TransferCommand cmd) {
        // 1. Validate (rejections are NOT ledgered).
        TransferResult rejection = validate(cmd);
        if (rejection != null) {
            return rejection;
        }

        // 2. Duplicate check (tenant-scoped).
        String idempotencyKey = cmd.provenance().idempotencyKey();
        String tenantId = cmd.provenance().tenantId();
        Optional<WritebackLedger> existing = findByIdempotencyKey(tenantId, idempotencyKey);
        if (existing.isPresent()) {
            return skippedDuplicateFrom(cmd, existing.get());
        }

        PnMaster pnMaster = repo.findActivePn(cmd.pn()).orElseThrow();
        boolean consumable = repo.isConsumable(pnMaster.getCategory());

        // 3. Category-aware qty (NumericPolicy) — consumable keeps decimals, else truncated.
        BigDecimal qty = NumericPolicy.apply(cmd.qty(), consumable);

        Instant now = Instant.now();
        Date nowDate = Date.from(now);
        String principal = cmd.provenance().principal();
        String orderNumber = numberSource.nextOrderNumber();

        // 4. Header — field set ported from ARMAC's StockTransferOrderData.createOrderHeader:
        // requester/bill-to location = TO (receiving side), shipped-from = FROM, priority NORM,
        // currency QAR->omitted (no currency column carried, see OrderHeader trim), inventory
        // type MAINTENANCE, status OPEN (left OPEN, not CLOSED — see class Javadoc),
        // override-address N, currency exchange 1, no-of-print 0, created/modified stamps.
        OrderHeaderPK headerPk = new OrderHeaderPK();
        headerPk.setOrderType(ORDER_TYPE_TRANSFER);
        headerPk.setOrderNumber(orderNumber);

        OrderHeader header = new OrderHeader();
        header.setId(headerPk);
        header.setRequesterLocation(cmd.toLocation());
        header.setBillToLocation(cmd.toLocation());
        header.setShippedFromLocation(cmd.fromLocation());
        header.setPriority("NORM");
        header.setInventoryType("MAINTENANCE");
        header.setStatus("OPEN");
        header.setOverrideAddress("N");
        header.setCurrencyExchange(BigDecimal.valueOf(1));
        header.setNoOfPrint(BigDecimal.valueOf(0));
        header.setCreatedBy(principal);
        header.setCreatedDate(nowDate);
        header.setModifiedBy(principal);
        header.setModifiedDate(nowDate);
        header.setInterfaceCreatedDate(nowDate);
        header.setInterfaceModifiedDate(nowDate);
        // ARMAC sets these three UNCONDITIONALLY (StockTransferOrderData.java:145-147) — not
        // gated on any switch, mirroring RequisitionCreator's authorization triple.
        header.setAuthorization("Y");
        header.setAuthorizationBy("TRAX_IFACE");
        header.setAuthorizationDate(nowDate);
        repo.persistOrMerge(header);

        // 5. Detail line 1 — pn/location(=TO)/ro_location(=FROM)/qty/batch/delivery date, status
        // OPEN.
        OrderDetailPK detailPk = new OrderDetailPK();
        detailPk.setOrderType(ORDER_TYPE_TRANSFER);
        detailPk.setOrderNumber(orderNumber);
        detailPk.setOrderLine(DETAIL_LINE);

        OrderDetail detail = new OrderDetail();
        detail.setId(detailPk);
        detail.setPn(cmd.pn());
        detail.setLocation(cmd.toLocation());
        detail.setRoLocation(cmd.fromLocation());
        detail.setQtyRequire(qty);
        detail.setStatus("OPEN");
        detail.setBatch(cmd.batch());
        if (cmd.deliveryDate() != null) {
            detail.setDeliveryDate(
                    Date.from(cmd.deliveryDate().atStartOfDay(java.time.ZoneOffset.UTC).toInstant()));
        }
        // ARMAC sets these general defaults unconditionally too (createOrderDeatail).
        detail.setNonInventoryFlag("N");
        detail.setQtyReceived(BigDecimal.ZERO);
        detail.setDeliveryHour(BigDecimal.ZERO);
        detail.setDeliveryMinute(BigDecimal.ZERO);
        detail.setInUse("N");
        String stockUom = pnMaster.getStockUom();
        detail.setUom(stockUom == null || stockUom.isBlank() ? "EA" : stockUom);
        detail.setCreatedBy(principal);
        detail.setCreatedDate(nowDate);
        detail.setModifiedBy(principal);
        detail.setModifiedDate(nowDate);
        repo.persistOrMerge(detail);

        // 6. Audit rows mirroring both header and detail (same created-by/date/company discipline
        // as RequisitionCreator).
        OrderHeaderAuditPK headerAuditPk = new OrderHeaderAuditPK();
        headerAuditPk.setOrderType(ORDER_TYPE_TRANSFER);
        headerAuditPk.setOrderNumber(orderNumber);
        headerAuditPk.setCreatedBy(principal);
        headerAuditPk.setCreatedDate(nowDate);

        OrderHeaderAudit headerAudit = new OrderHeaderAudit();
        headerAudit.setId(headerAuditPk);
        headerAudit.setRequesterLocation(header.getRequesterLocation());
        headerAudit.setBillToLocation(header.getBillToLocation());
        headerAudit.setShippedFromLocation(header.getShippedFromLocation());
        headerAudit.setPriority(header.getPriority());
        headerAudit.setInventoryType(header.getInventoryType());
        headerAudit.setStatus(header.getStatus());
        headerAudit.setOverrideAddress(header.getOverrideAddress());
        headerAudit.setCurrencyExchange(header.getCurrencyExchange());
        headerAudit.setNoOfPrint(header.getNoOfPrint());
        headerAudit.setModifiedBy(header.getModifiedBy());
        headerAudit.setModifiedDate(header.getModifiedDate());
        headerAudit.setInterfaceCreatedDate(header.getInterfaceCreatedDate());
        headerAudit.setAuthorization(header.getAuthorization());
        headerAudit.setAuthorizationBy(header.getAuthorizationBy());
        headerAudit.setAuthorizationDate(header.getAuthorizationDate());
        repo.persistOrMerge(headerAudit);

        OrderDetailAuditPK detailAuditPk = new OrderDetailAuditPK();
        detailAuditPk.setOrderType(ORDER_TYPE_TRANSFER);
        detailAuditPk.setOrderNumber(orderNumber);
        detailAuditPk.setOrderLine(DETAIL_LINE);
        detailAuditPk.setCreatedBy(principal);
        detailAuditPk.setCreatedDate(nowDate);

        OrderDetailAudit detailAudit = new OrderDetailAudit();
        detailAudit.setId(detailAuditPk);
        detailAudit.setPn(detail.getPn());
        detailAudit.setLocation(detail.getLocation());
        detailAudit.setRoLocation(detail.getRoLocation());
        detailAudit.setQtyRequire(detail.getQtyRequire());
        detailAudit.setStatus(detail.getStatus());
        detailAudit.setBatch(detail.getBatch());
        detailAudit.setDeliveryDate(detail.getDeliveryDate());
        detailAudit.setNonInventoryFlag(detail.getNonInventoryFlag());
        detailAudit.setQtyReceived(detail.getQtyReceived());
        detailAudit.setDeliveryHour(detail.getDeliveryHour());
        detailAudit.setDeliveryMinute(detail.getDeliveryMinute());
        detailAudit.setUom(detail.getUom());
        detailAudit.setModifiedBy(detail.getModifiedBy());
        detailAudit.setModifiedDate(detail.getModifiedDate());
        repo.persistOrMerge(detailAudit);

        // 7. Ledger insert (same tx): version chaining shared with STOCK_LEVEL/REQUISITION (D10).
        // Ledger key location is the FROM location — stock leaves there (see class Javadoc).
        Long previousMaxVersion = maxVersion(tenantId, cmd.pn(), cmd.fromLocation());
        long version = previousMaxVersion == null ? 1L : previousMaxVersion + 1L;
        String message = "transfer " + orderNumber + " created";

        WritebackLedger ledger = new WritebackLedger();
        ledger.setIdempotencyKey(idempotencyKey);
        ledger.setTenantId(tenantId);
        ledger.setRunId(cmd.provenance().runId());
        ledger.setRowId(cmd.provenance().rowId());
        ledger.setProvenanceId(cmd.provenance().provenanceId());
        ledger.setPn(cmd.pn());
        ledger.setLocation(cmd.fromLocation());
        ledger.setSource(cmd.provenance().source());
        ledger.setTier(cmd.provenance().tier());
        ledger.setApprover(cmd.provenance().approver());
        ledger.setPrincipal(principal);
        ledger.setAgentVersion(AGENT_VERSION);
        ledger.setOutcome("WRITTEN");
        ledger.setDomain(DOMAIN_TRANSFER);
        ledger.setCreatedRef(orderNumber);
        ledger.setVersion(version);
        ledger.setParentVersion(previousMaxVersion);
        ledger.setMessage(message);
        ledger.setCreatedAt(now);

        em.persist(ledger);
        em.flush();

        return new TransferResult(
                ResultStatus.ACCEPTED, codeFor(ResultStatus.ACCEPTED), message, cmd.provenance().rowId(),
                orderNumber, cmd.batch());
    }

    private TransferResult validate(TransferCommand cmd) {
        Long rowId = cmd.provenance().rowId();

        Optional<PnMaster> pnMaster = repo.findActivePn(cmd.pn());
        if (pnMaster.isEmpty()) {
            Optional<String> actualStatus = pnStatusRegardlessOfActivity(cmd.pn());
            if (actualStatus.isPresent()) {
                return rejection(
                        ResultStatus.REJECTED_VALIDATION,
                        "PN " + cmd.pn() + " is not active, status=" + actualStatus.get(),
                        rowId);
            }
            return rejection(ResultStatus.REJECTED_UNKNOWN_KEY, "unknown PN: " + cmd.pn(), rowId);
        }

        Optional<LocationMaster> fromLocation = repo.findActiveInventoryLocation(cmd.fromLocation());
        if (fromLocation.isEmpty()) {
            if (!locationExistsRegardlessOfEligibility(cmd.fromLocation())) {
                return rejection(
                        ResultStatus.REJECTED_UNKNOWN_KEY, "unknown from-location: " + cmd.fromLocation(), rowId);
            }
            return rejection(
                    ResultStatus.REJECTED_VALIDATION,
                    "from-location is not an active, non-quarantine inventory location: " + cmd.fromLocation(),
                    rowId);
        }

        Optional<LocationMaster> toLocation = repo.findActiveInventoryLocation(cmd.toLocation());
        if (toLocation.isEmpty()) {
            if (!locationExistsRegardlessOfEligibility(cmd.toLocation())) {
                return rejection(
                        ResultStatus.REJECTED_UNKNOWN_KEY, "unknown to-location: " + cmd.toLocation(), rowId);
            }
            return rejection(
                    ResultStatus.REJECTED_VALIDATION,
                    "to-location is not an active, non-quarantine inventory location: " + cmd.toLocation(),
                    rowId);
        }

        if (cmd.fromLocation().equals(cmd.toLocation())) {
            return rejection(
                    ResultStatus.REJECTED_VALIDATION, "from-location and to-location must differ", rowId);
        }

        if (cmd.qty() == null || cmd.qty().signum() <= 0) {
            return rejection(ResultStatus.REJECTED_VALIDATION, "qty must be > 0", rowId);
        }

        return null;
    }

    private Optional<String> pnStatusRegardlessOfActivity(String pn) {
        try {
            return Optional.of(em.createQuery("select p.status from PnMaster p where p.pn = :pn", String.class)
                    .setParameter("pn", pn)
                    .getSingleResult());
        } catch (jakarta.persistence.NoResultException e) {
            return Optional.empty();
        }
    }

    private boolean locationExistsRegardlessOfEligibility(String location) {
        try {
            em.createQuery("select l.location from LocationMaster l where l.location = :location", String.class)
                    .setParameter("location", location)
                    .getSingleResult();
            return true;
        } catch (jakarta.persistence.NoResultException e) {
            return false;
        }
    }

    /**
     * Looks up a ledger row by (tenant, idempotency key), scoped exactly like {@link
     * StockLevelWriter#findByIdempotencyKey} — shared uniqueness constraint, so the same lookup
     * shape applies regardless of domain.
     */
    public Optional<WritebackLedger> findByIdempotencyKey(String tenantId, String key) {
        return em.createQuery(
                        "select l from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.idempotencyKey = :key",
                        WritebackLedger.class)
                .setParameter("tenantId", tenantId)
                .setParameter("key", key)
                .getResultStream()
                .findFirst();
    }

    private Long maxVersion(String tenantId, String pn, String location) {
        return em.createQuery(
                        "select max(l.version) from WritebackLedger l"
                                + " where l.tenantId = :tenantId and l.pn = :pn and l.location = :location",
                        Long.class)
                .setParameter("tenantId", tenantId)
                .setParameter("pn", pn)
                .setParameter("location", location)
                .getSingleResult();
    }

    private TransferResult skippedDuplicateFrom(TransferCommand cmd, WritebackLedger ledger) {
        return new TransferResult(
                ResultStatus.SKIPPED_DUPLICATE,
                codeFor(ResultStatus.SKIPPED_DUPLICATE),
                "duplicate idempotency key: " + ledger.getIdempotencyKey(),
                cmd.provenance().rowId(),
                ledger.getCreatedRef(),
                cmd.batch());
    }

    private TransferResult rejection(ResultStatus status, String message, Long rowId) {
        return new TransferResult(status, codeFor(status), message, rowId, null, null);
    }

    private TransferResult errorResult(String message, Long rowId) {
        return new TransferResult(ResultStatus.ERROR, codeFor(ResultStatus.ERROR), message, rowId, null, null);
    }

    private static int codeFor(ResultStatus status) {
        return switch (status) {
            case ACCEPTED, SHADOWED, SKIPPED_DUPLICATE -> 200;
            case REJECTED_VALIDATION, REJECTED_UNKNOWN_KEY -> 400;
            case ERROR -> 500;
        };
    }
}
