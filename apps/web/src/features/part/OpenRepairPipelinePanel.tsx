import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  RepairPipeline,
  RepairPipelineStatus,
  RepairPipelineWarningCode,
} from "@/lib/api/types";

const integerFormatter = new Intl.NumberFormat("en-US");

const STATUS_PRESENTATION: Record<
  RepairPipelineStatus,
  { label: string; variant: "good" | "warn" | "bad" }
> = {
  available: { label: "Available", variant: "good" },
  partial: { label: "Partial", variant: "warn" },
  unavailable: { label: "Unavailable", variant: "bad" },
};

const WARNING_EXPLANATIONS: Record<RepairPipelineWarningCode, string> = {
  repair_pipeline_unavailable:
    "The open-repair source was unavailable, so repair-work quantities cannot be treated as observed.",
  repair_work_excluded:
    "At least one repair-work row failed an eligibility or reconciliation rule and was conservatively excluded.",
  repair_identity_excluded:
    "At least one repair-work row lacked the stable order or line identity needed for safe reconciliation.",
  repair_age_missing:
    "At least one repair-work row lacked a usable opened date, so its age could not be established.",
  repair_source_duplicates:
    "Duplicate order-line or serial identities were found and excluded from eligible repair work.",
  repair_wip_mismatch:
    "Identified repair-order quantity did not match the aggregate stock-position WIP quantity.",
  repair_residual_unidentified:
    "Some aggregate repair WIP could not be linked to an identifiable repair-order line.",
};

function Quantity({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  return (
    <div className="rounded-card border border-line bg-panel-2 p-3">
      <dt className="text-xs leading-5 text-ink-2">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-ink">
        {value === null ? "—" : integerFormatter.format(value)}
      </dd>
    </div>
  );
}

function Identity({
  orderId,
  lineId,
}: {
  orderId: string | null;
  lineId: string | null;
}) {
  return (
    <div className="flex min-w-32 flex-col gap-0.5">
      <span>{orderId ?? "—"}</span>
      <span className="text-xs text-ink-3">Line {lineId ?? "—"}</span>
    </div>
  );
}

export function OpenRepairPipelinePanel({
  pipeline,
}: {
  pipeline: RepairPipeline | null | undefined;
}) {
  const status = pipeline?.status ?? "unavailable";
  const statusPresentation = STATUS_PRESENTATION[status];

  return (
    <Card
      role="region"
      aria-labelledby="open-repair-pipeline-heading"
      data-testid="open-repair-pipeline"
    >
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle id="open-repair-pipeline-heading">
            Open repair pipeline
          </CardTitle>
          <Badge
            variant={statusPresentation.variant}
            aria-label={`Repair pipeline evidence status: ${statusPresentation.label}`}
          >
            {statusPresentation.label}
          </Badge>
        </div>
        <p className="text-sm text-ink-2">
          Identifiable repair orders reconciled to aggregate in-repair WIP.
        </p>
      </CardHeader>

      <CardContent className="flex flex-col gap-5">
        {!pipeline && (
          <p className="text-sm text-ink-2" role="note">
            This legacy response did not return a repair-pipeline contract.
            Quantities are unavailable, not observed zeros.
          </p>
        )}

        <dl
          className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
          aria-label="Open repair pipeline quantities"
        >
          <Quantity
            label="Aggregate WIP"
            value={pipeline?.aggregate_wip_quantity ?? null}
          />
          <Quantity
            label="Identified open"
            value={pipeline?.identified_open_quantity ?? null}
          />
          <Quantity
            label="Missing-identity source"
            value={pipeline?.unidentified_source_quantity ?? null}
          />
          <Quantity
            label="Eligible for future modeling"
            value={pipeline?.eligible_quantity ?? null}
          />
          <Quantity
            label="Excluded identifiable"
            value={pipeline?.excluded_identifiable_quantity ?? null}
          />
          <Quantity
            label="Aggregate residual"
            value={pipeline?.aggregate_residual_quantity ?? null}
          />
          <Quantity
            label="Source overflow"
            value={pipeline?.source_overflow_quantity ?? null}
          />
          <Quantity
            label="Time-phased credit"
            value={pipeline?.time_phased_credit_quantity ?? null}
          />
        </dl>

        <div
          role="note"
          aria-label="Conservative repair-credit methodology"
          className="rounded-card border border-brand/30 bg-brand/10 p-3 text-sm text-ink-2"
        >
          <p className="font-medium text-ink">
            Phase 5 grants zero time-phased repair credit.
          </p>
          <p className="mt-1">
            Eligible quantity is retained only for future age-conditioned
            modeling. Identified exclusions, missing-identity source rows, and
            aggregate residual work contribute no projected receipt.
          </p>
          <p className="mt-1">
            Missing-identity source quantity consumes aggregate WIP before the
            residual is calculated, so the same physical unit is never counted
            in both categories.
          </p>
          <p className="mt-1">
            Only RO lines are reconciled here. PO lines remain procurement
            receipts, and repair work is never counted as a generic open
            receipt.
          </p>
        </div>

        {pipeline && pipeline.warning_codes.length > 0 && (
          <div
            role="note"
            aria-label="Repair pipeline data-quality warnings"
            className="rounded-card border border-warn/40 bg-warn/10 p-3"
          >
            <h4 className="text-sm font-semibold text-ink">
              Data-quality warnings
            </h4>
            <ul className="mt-2 flex list-disc flex-col gap-2 pl-5 text-sm text-ink-2">
              {pipeline.warning_codes.map((warning) => (
                <li key={warning}>
                  <code className="font-mono text-xs text-ink">{warning}</code>
                  <span>: {WARNING_EXPLANATIONS[warning]}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {pipeline && pipeline.included.length > 0 && (
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full min-w-[62rem] text-left text-sm">
              <caption className="sr-only">
                Included open repair positions
              </caption>
              <thead className="bg-panel-2 text-ink-2">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Order / line
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Line qty
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Eligible
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Age
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Opened
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Status
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Shop / vendor
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Serial
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Location
                  </th>
                </tr>
              </thead>
              <tbody>
                {pipeline.included.map(({ work_item: item, eligible_quantity, age_days }) => (
                  <tr
                    key={`${item.repair_order_id}:${item.repair_line_id}`}
                    className="border-t border-line align-top"
                  >
                    <td className="px-3 py-2">
                      <Identity
                        orderId={item.repair_order_id}
                        lineId={item.repair_line_id}
                      />
                    </td>
                    <td className="px-3 py-2">
                      {integerFormatter.format(item.quantity)}
                    </td>
                    <td className="px-3 py-2">
                      {integerFormatter.format(eligible_quantity)}
                    </td>
                    <td className="px-3 py-2">
                      {integerFormatter.format(age_days)} days
                    </td>
                    <td className="px-3 py-2">
                      <time dateTime={item.opened_at}>{item.opened_at}</time>
                    </td>
                    <td className="px-3 py-2">{item.status}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-col gap-0.5">
                        <span>Shop {item.shop_code ?? "—"}</span>
                        <span className="text-xs text-ink-3">
                          Vendor {item.vendor_code ?? "—"}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {item.serial_number ?? "—"}
                    </td>
                    <td className="px-3 py-2">{item.location_code}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pipeline && pipeline.exclusions.length > 0 && (
          <div className="overflow-x-auto rounded-card border border-line">
            <table className="w-full min-w-[50rem] text-left text-sm">
              <caption className="sr-only">
                Excluded open repair positions
              </caption>
              <thead className="bg-panel-2 text-ink-2">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Order / line
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Serial
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Qty
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Exclusion
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Detail
                  </th>
                </tr>
              </thead>
              <tbody>
                {pipeline.exclusions.map((exclusion, index) => (
                  <tr
                    key={`${exclusion.repair_order_id ?? "unknown"}:${exclusion.repair_line_id ?? "unknown"}:${exclusion.reason}:${index}`}
                    className="border-t border-line align-top"
                  >
                    <td className="px-3 py-2">
                      <Identity
                        orderId={exclusion.repair_order_id}
                        lineId={exclusion.repair_line_id}
                      />
                    </td>
                    <td className="px-3 py-2">
                      {exclusion.serial_number ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      {integerFormatter.format(exclusion.quantity)}
                    </td>
                    <td className="px-3 py-2">
                      <code className="font-mono text-xs">
                        {exclusion.reason}
                      </code>
                    </td>
                    <td className="px-3 py-2">{exclusion.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pipeline && (
          <dl
            className="grid gap-2 border-t border-line pt-3 text-xs sm:grid-cols-2"
            aria-label="Repair pipeline evidence"
          >
            <div>
              <dt className="text-ink-3">Evidence source</dt>
              <dd className="mt-0.5 break-words text-ink-2">
                {pipeline.evidence_source}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Planning as-of</dt>
              <dd className="mt-0.5 text-ink-2">
                <time dateTime={pipeline.as_of}>{pipeline.as_of}</time>
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Contract</dt>
              <dd className="mt-0.5 text-ink-2">
                {pipeline.contract_version}
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Scoped key</dt>
              <dd className="mt-0.5 break-words text-ink-2">
                {pipeline.tenant_id} · {pipeline.part_number} ·{" "}
                {pipeline.location_code}
              </dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
