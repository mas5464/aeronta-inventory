import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  RepairPipelineStatus,
  RepairReturnProfile,
} from "@/lib/api/types";

const integerFormatter = new Intl.NumberFormat("en-US");
const unitsFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 1,
});
const probabilityFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const STATUS_PRESENTATION: Record<
  RepairPipelineStatus,
  { label: string; variant: "good" | "warn" | "bad" }
> = {
  available: { label: "Available", variant: "good" },
  partial: { label: "Partial", variant: "warn" },
  unavailable: { label: "Unavailable", variant: "bad" },
};

const METHOD_LABELS: Record<RepairReturnProfile["evidence"]["method"], string> = {
  kaplan_meier: "Kaplan–Meier survival",
  lognormal_quantile: "Lognormal fit from REP quantiles",
  deterministic_promise: "Configured REP promise",
  unavailable: "Unavailable",
};

const WARNING_EXPLANATIONS: Record<string, string> = {
  repair_return_evidence_unavailable:
    "No eligible REP duration evidence was available, so the model grants zero projected repair receipts.",
  repair_return_configured_promise:
    "Returns use a configured REP promise rather than an observed duration distribution.",
  repair_pipeline_unavailable:
    "The underlying open-repair source was unavailable.",
  repair_work_excluded:
    "At least one repair-work row was conservatively excluded.",
  repair_identity_excluded:
    "Stable repair order or line identity was missing.",
  repair_age_missing:
    "At least one open repair lacked the date needed to establish its age.",
  repair_source_duplicates:
    "Duplicate order-line or serial identities were excluded.",
  repair_wip_mismatch:
    "Identified repair work did not match aggregate in-repair WIP.",
  repair_residual_unidentified:
    "Aggregate repair WIP remains unidentified and receives no return credit.",
  repair_return_right_censoring_not_fitted:
    "Open-work ages condition the fallback projection, but no raw completed-cycle sample was available to fit those units as right-censored observations.",
};

function SummaryValue({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-card border border-line bg-panel-2 p-3">
      <dt className="text-xs text-ink-2">{label}</dt>
      <dd className="mt-1 text-xl font-semibold text-ink">
        {integerFormatter.format(value)}
      </dd>
    </div>
  );
}

export function RepairReturnProfilePanel({
  profile,
}: {
  profile: RepairReturnProfile | null | undefined;
}) {
  const status = profile?.status ?? "unavailable";
  const statusPresentation = STATUS_PRESENTATION[status];

  return (
    <Card role="region" aria-labelledby="repair-return-profile-heading">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle id="repair-return-profile-heading">
            Projected repair returns
          </CardTitle>
          <Badge
            variant={statusPresentation.variant}
            aria-label={`Repair return evidence status: ${statusPresentation.label}`}
          >
            {statusPresentation.label}
          </Badge>
        </div>
        <p className="text-sm text-ink-2">
          Horizon-specific serviceable receipts conditioned on each open
          repair line’s current age.
        </p>
      </CardHeader>

      <CardContent className="flex flex-col gap-5">
        {!profile ? (
          <p className="text-sm text-ink-2" role="note">
            This legacy response has no repair-return profile. Expected units
            and probabilities are unavailable, not observed zeros.
          </p>
        ) : (
          <>
            <dl
              className="grid grid-cols-1 gap-3 sm:grid-cols-3"
              aria-label="Repair return eligibility"
            >
              <SummaryValue
                label="Eligible open WIP"
                value={profile.eligible_quantity}
              />
              <SummaryValue
                label="Excluded identifiable"
                value={profile.excluded_quantity}
              />
              <SummaryValue
                label="Aggregate residual"
                value={profile.aggregate_residual_quantity}
              />
            </dl>

            <div
              role="note"
              aria-label="Age-conditioned repair return methodology"
              className="rounded-card border border-brand/30 bg-brand/10 p-3 text-sm text-ink-2"
            >
              An open repair keeps its observed age; its repair clock is not
              restarted at day zero. Expected units are probability-weighted,
              not guaranteed receipts, and excluded or residual WIP receives
              no credit.
            </div>

            {profile.warning_codes.length > 0 && (
              <div
                role="note"
                aria-label="Repair return warnings"
                className="rounded-card border border-warn/40 bg-warn/10 p-3"
              >
                <h4 className="text-sm font-semibold text-ink">
                  Projection warnings
                </h4>
                <ul className="mt-2 flex list-disc flex-col gap-2 pl-5 text-sm text-ink-2">
                  {profile.warning_codes.map((warning) => (
                    <li key={warning}>
                      <code className="font-mono text-xs text-ink">
                        {warning}
                      </code>
                      <span>
                        :{" "}
                        {WARNING_EXPLANATIONS[warning] ??
                          "The source contract reported this data-quality condition."}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="overflow-x-auto rounded-card border border-line">
              <table className="w-full min-w-[48rem] text-left text-sm">
                <caption className="sr-only">
                  Repair return horizon summary
                </caption>
                <thead className="bg-panel-2 text-ink-2">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Horizon
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Expected units
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      P10–P90 units
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Mean serviceable probability
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Variance
                    </th>
                    <th scope="col" className="px-3 py-2 font-medium">
                      Eligible
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {profile.horizons.map((horizon) => (
                    <tr
                      key={horizon.horizon_days}
                      className="border-t border-line"
                    >
                      <th scope="row" className="px-3 py-2 font-medium text-ink">
                        {integerFormatter.format(horizon.horizon_days)} days
                      </th>
                      <td className="px-3 py-2">
                        {unitsFormatter.format(horizon.expected_units)}
                      </td>
                      <td className="px-3 py-2">
                        {unitsFormatter.format(horizon.p10_units)}–
                        {unitsFormatter.format(horizon.p90_units)}
                      </td>
                      <td className="px-3 py-2">
                        {probabilityFormatter.format(
                          horizon.mean_serviceable_probability,
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {unitsFormatter.format(horizon.variance_units)}
                      </td>
                      <td className="px-3 py-2">
                        {integerFormatter.format(horizon.eligible_quantity)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {profile.horizons.map(
              (horizon) =>
                horizon.item_probabilities.length > 0 && (
                  <div
                    key={horizon.horizon_days}
                    className="overflow-x-auto rounded-card border border-line"
                  >
                    <table className="w-full min-w-[58rem] text-left text-sm">
                      <caption className="sr-only">
                        {horizon.horizon_days} day repair item probabilities
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
                            Current age
                          </th>
                          <th scope="col" className="px-3 py-2 font-medium">
                            Qty
                          </th>
                          <th scope="col" className="px-3 py-2 font-medium">
                            Return probability
                          </th>
                          <th scope="col" className="px-3 py-2 font-medium">
                            Serviceable probability
                          </th>
                          <th scope="col" className="px-3 py-2 font-medium">
                            Expected serviceable
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {horizon.item_probabilities.map((item) => (
                          <tr
                            key={`${item.repair_order_id}:${item.repair_line_id}`}
                            className="border-t border-line"
                          >
                            <td className="px-3 py-2">
                              <span className="block">
                                {item.repair_order_id}
                              </span>
                              <span className="text-xs text-ink-3">
                                Line {item.repair_line_id}
                              </span>
                            </td>
                            <td className="px-3 py-2">
                              {item.serial_number ?? "—"}
                            </td>
                            <td className="px-3 py-2">
                              {integerFormatter.format(item.age_days)} days
                            </td>
                            <td className="px-3 py-2">
                              {integerFormatter.format(item.quantity)}
                            </td>
                            <td className="px-3 py-2">
                              {probabilityFormatter.format(
                                item.return_probability,
                              )}
                            </td>
                            <td className="px-3 py-2">
                              {probabilityFormatter.format(
                                item.serviceable_probability,
                              )}
                            </td>
                            <td className="px-3 py-2">
                              {unitsFormatter.format(
                                item.expected_serviceable_units,
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ),
            )}

            {profile.exclusions.length > 0 && (
              <div className="overflow-x-auto rounded-card border border-line">
                <table className="w-full min-w-[44rem] text-left text-sm">
                  <caption className="sr-only">
                    Repair return exclusions
                  </caption>
                  <thead className="bg-panel-2 text-ink-2">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-medium">
                        Order / line
                      </th>
                      <th scope="col" className="px-3 py-2 font-medium">
                        Qty
                      </th>
                      <th scope="col" className="px-3 py-2 font-medium">
                        Reason
                      </th>
                      <th scope="col" className="px-3 py-2 font-medium">
                        Detail
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {profile.exclusions.map((exclusion, index) => (
                      <tr
                        key={`${exclusion.repair_order_id ?? "unknown"}:${exclusion.repair_line_id ?? "unknown"}:${index}`}
                        className="border-t border-line"
                      >
                        <td className="px-3 py-2">
                          {exclusion.repair_order_id ?? "—"} · Line{" "}
                          {exclusion.repair_line_id ?? "—"}
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

            <dl
              className="grid gap-3 border-t border-line pt-3 text-xs sm:grid-cols-2 lg:grid-cols-3"
              aria-label="Repair return evidence"
            >
              <div>
                <dt className="text-ink-3">Method</dt>
                <dd className="mt-0.5 text-ink-2">
                  {METHOD_LABELS[profile.evidence.method]} ·{" "}
                  <code>{profile.evidence.method}</code>
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Completed observations</dt>
                <dd className="mt-0.5 text-ink-2">
                  {integerFormatter.format(
                    profile.evidence.completed_observations,
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">
                  Right-censored observations used in fit
                </dt>
                <dd className="mt-0.5 text-ink-2">
                  {integerFormatter.format(
                    profile.evidence.right_censored_observations,
                  )}
                </dd>
              </div>
              {profile.evidence.method !== "kaplan_meier" && (
                <div>
                  <dt className="text-ink-3">Fallback censoring treatment</dt>
                  <dd className="mt-0.5 text-ink-2">
                    Open WIP ages condition each projection, but are not used
                    to fit the fallback duration curve.
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-ink-3">
                  Assumed serviceable yield
                </dt>
                <dd className="mt-0.5 text-ink-2">
                  {probabilityFormatter.format(
                    profile.evidence.serviceable_yield,
                  )}{" "}
                  (model input; not an observed yield)
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Repair-TAT multiplier</dt>
                <dd className="mt-0.5 text-ink-2">
                  {unitsFormatter.format(profile.evidence.tat_multiplier)}×
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Confidence</dt>
                <dd className="mt-0.5 capitalize text-ink-2">
                  {profile.evidence.confidence}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Source</dt>
                <dd className="mt-0.5 break-words text-ink-2">
                  {profile.evidence.source}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Data cutoff</dt>
                <dd className="mt-0.5 text-ink-2">
                  {profile.evidence.data_cutoff ?? "Unavailable"}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Model version</dt>
                <dd className="mt-0.5 break-words text-ink-2">
                  {profile.evidence.model_version}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Proxy definition</dt>
                <dd className="mt-0.5 break-words text-ink-2">
                  {profile.evidence.proxy_definition ?? "Not applicable"}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">Planning as-of</dt>
                <dd className="mt-0.5 text-ink-2">{profile.as_of}</dd>
              </div>
              <div>
                <dt className="text-ink-3">Contract / scoped key</dt>
                <dd className="mt-0.5 break-words text-ink-2">
                  {profile.contract_version} · {profile.tenant_id} ·{" "}
                  {profile.part_number} · {profile.location_code}
                </dd>
              </div>
            </dl>
          </>
        )}
      </CardContent>
    </Card>
  );
}
