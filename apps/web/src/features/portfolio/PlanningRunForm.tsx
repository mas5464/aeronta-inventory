import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  CreatePlanningRunBody,
  PlanningMandatoryFloor,
  PlanningObjectiveWeights,
  PlanningRerunConfig,
  PlanningScopeKey,
  PlanningRunView,
} from "@/lib/api/planningRuns";
import { usePlanningRunRerunConfig } from "@/lib/api/usePlanningRuns";
import { parsePlanningScope } from "@/features/portfolio/portfolioView";

interface DraftFloor {
  id: number;
  decisionKey: string;
  floorId: string;
  source: string;
  minServiceLevel: string;
  maxExpectedShortage: string;
  maxAogRisk: string;
  detail: string;
}

interface PlanningRunFormProps {
  tenant: string;
  terminalRuns: PlanningRunView[];
  isPending: boolean;
  error: Error | null;
  onSubmit: (body: CreatePlanningRunBody) => void;
}

const inputClass =
  "h-9 w-full rounded-control border border-line bg-panel px-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const labelClass = "flex flex-col gap-1 text-xs font-medium text-ink-2";
const DEFAULT_CRITICALITY_WEIGHTS = {
  "1": "5",
  "2": "3",
  "3": "2",
  "4": "1",
  "5": "1",
};

function rerunDraftFloors(config: PlanningRerunConfig): DraftFloor[] {
  let id = 1;
  return Object.entries(config.mandatory_floors).flatMap(
    ([decisionKey, floors]) =>
      floors.map((floor) => ({
        id: id++,
        decisionKey,
        floorId: floor.floor_id,
        source: floor.source,
        minServiceLevel: floor.min_service_level ?? "",
        maxExpectedShortage: floor.max_expected_shortage ?? "",
        maxAogRisk: floor.max_aog_risk ?? "",
        detail: floor.detail ?? "",
      })),
  );
}

function normalizeDecimal(value: string): string {
  const trimmed = value.trim();
  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(trimmed);
  if (!match) return trimmed;
  const sign = match[1] === "-" ? "-" : "";
  const integer = match[2].replace(/^0+(?=\d)/, "");
  const fraction = (match[3] ?? "").replace(/0+$/, "");
  return `${sign}${integer}${fraction ? `.${fraction}` : ""}`;
}

function floorSignature(floors: DraftFloor[]) {
  return floors
    .map((floor) => ({
      decision_key: floor.decisionKey.trim(),
      floor_id: floor.floorId.trim(),
      source: floor.source.trim(),
      min_service_level: normalizeDecimal(floor.minServiceLevel),
      max_expected_shortage: normalizeDecimal(floor.maxExpectedShortage),
      max_aog_risk: normalizeDecimal(floor.maxAogRisk),
      detail: floor.detail.trim(),
    }))
    .sort((left, right) =>
      JSON.stringify(left).localeCompare(JSON.stringify(right)),
    );
}

interface EditableAssumptions {
  scopeKind: "all_eligible" | "explicit";
  keys: PlanningScopeKey[];
  budget: string;
  horizon: string;
  currency: string;
  timeLimit: string;
  objectiveWeights: PlanningObjectiveWeights;
  floors: DraftFloor[];
}

function assumptionSignature(assumptions: EditableAssumptions): string {
  return JSON.stringify({
    scope_kind: assumptions.scopeKind,
    keys:
      assumptions.scopeKind === "explicit"
        ? assumptions.keys.map((key) => `${key.pn}@${key.location}`).sort()
        : [],
    budget: normalizeDecimal(assumptions.budget),
    horizon_days: Number(assumptions.horizon),
    currency: assumptions.currency.trim().toUpperCase(),
    time_limit_seconds: Number(assumptions.timeLimit),
    objective_weights: {
      shortage_reduction_weight: normalizeDecimal(
        assumptions.objectiveWeights.shortage_reduction_weight,
      ),
      aog_risk_reduction_weight: normalizeDecimal(
        assumptions.objectiveWeights.aog_risk_reduction_weight,
      ),
      holding_cost_penalty_weight: normalizeDecimal(
        assumptions.objectiveWeights.holding_cost_penalty_weight,
      ),
      ordering_cost_penalty_weight: normalizeDecimal(
        assumptions.objectiveWeights.ordering_cost_penalty_weight,
      ),
      criticality_weights: Object.fromEntries(
        Object.entries(assumptions.objectiveWeights.criticality_weights)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([tier, value]) => [tier, normalizeDecimal(value)]),
      ),
    },
    mandatory_floors: floorSignature(assumptions.floors),
  });
}

function rerunConfigSignature(config: PlanningRerunConfig): string {
  return assumptionSignature({
    scopeKind: config.scope_kind,
    keys: config.keys,
    budget: config.budget,
    horizon: String(config.horizon_days),
    currency: config.currency,
    timeLimit: String(config.time_limit_seconds),
    objectiveWeights: config.objective_weights,
    floors: rerunDraftFloors(config),
  });
}

export function PlanningRunForm({
  tenant,
  terminalRuns,
  isPending,
  error,
  onSubmit,
}: PlanningRunFormProps) {
  const [scope, setScope] = useState("");
  const [scopeKind, setScopeKind] = useState<"all_eligible" | "explicit">(
    "all_eligible",
  );
  const [budget, setBudget] = useState("100000");
  const [horizon, setHorizon] = useState("60");
  const [currency, setCurrency] = useState("USD");
  const [timeLimit, setTimeLimit] = useState("30");
  const [parentRunId, setParentRunId] = useState("");
  const [shortageWeight, setShortageWeight] = useState("1");
  const [aogWeight, setAogWeight] = useState("1");
  const [holdingWeight, setHoldingWeight] = useState("0.01");
  const [orderingWeight, setOrderingWeight] = useState("0.01");
  const [criticalityWeights, setCriticalityWeights] = useState<
    Record<string, string>
  >(DEFAULT_CRITICALITY_WEIGHTS);
  const [floors, setFloors] = useState<DraftFloor[]>([]);
  const [nextFloorId, setNextFloorId] = useState(1);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [appliedRerunId, setAppliedRerunId] = useState<string | null>(null);
  const [
    useCurrentTrustedRepair,
    setUseCurrentTrustedRepair,
  ] = useState(false);
  const rerunConfigQuery = usePlanningRunRerunConfig(
    parentRunId || null,
    tenant,
  );

  const parsedScope = useMemo(() => parsePlanningScope(scope), [scope]);
  const scopeIds =
    parsedScope.error === null
      ? parsedScope.keys.map((key) => `${key.pn}@${key.location}`)
      : [];
  const currentSignature = useMemo(
    () =>
      assumptionSignature({
        scopeKind,
        keys: parsedScope.error === null ? parsedScope.keys : [],
        budget,
        horizon,
        currency,
        timeLimit,
        objectiveWeights: {
          shortage_reduction_weight: shortageWeight,
          aog_risk_reduction_weight: aogWeight,
          holding_cost_penalty_weight: holdingWeight,
          ordering_cost_penalty_weight: orderingWeight,
          criticality_weights: criticalityWeights,
        },
        floors,
      }),
    [
      scopeKind,
      parsedScope,
      budget,
      horizon,
      currency,
      timeLimit,
      shortageWeight,
      aogWeight,
      holdingWeight,
      orderingWeight,
      criticalityWeights,
      floors,
    ],
  );
  const savedSignature = rerunConfigQuery.data
    ? rerunConfigSignature(rerunConfigQuery.data)
    : null;
  const editableRerunChange =
    savedSignature !== null && currentSignature !== savedSignature;
  const repairChangeAvailable =
    rerunConfigQuery.data?.repair_assumption_change_available === true;
  const rerunReady =
    parentRunId === "" ||
    (rerunConfigQuery.data?.parent_run_id === parentRunId &&
      appliedRerunId === parentRunId &&
      (!repairChangeAvailable || useCurrentTrustedRepair) &&
      (editableRerunChange || useCurrentTrustedRepair));

  useEffect(() => {
    const config = rerunConfigQuery.data;
    if (
      !config ||
      config.parent_run_id !== parentRunId ||
      appliedRerunId === config.parent_run_id
    ) {
      return;
    }
    const savedFloors = rerunDraftFloors(config);
    setScopeKind(config.scope_kind);
    setScope(config.keys.map((key) => `${key.pn}@${key.location}`).join("\n"));
    setBudget(config.budget);
    setHorizon(String(config.horizon_days));
    setCurrency(config.currency);
    setTimeLimit(String(config.time_limit_seconds));
    setShortageWeight(
      config.objective_weights.shortage_reduction_weight,
    );
    setAogWeight(config.objective_weights.aog_risk_reduction_weight);
    setHoldingWeight(
      config.objective_weights.holding_cost_penalty_weight,
    );
    setOrderingWeight(
      config.objective_weights.ordering_cost_penalty_weight,
    );
    setCriticalityWeights(config.objective_weights.criticality_weights);
    setFloors(savedFloors);
    setNextFloorId(savedFloors.length + 1);
    setUseCurrentTrustedRepair(false);
    setValidationError(null);
    setAppliedRerunId(config.parent_run_id);
  }, [appliedRerunId, parentRunId, rerunConfigQuery.data]);

  function addFloor() {
    setFloors((current) => [
      ...current,
      {
        id: nextFloorId,
        decisionKey: scopeIds[0] ?? "",
        floorId: `required-floor-${nextFloorId}`,
        source: "planner-input",
        minServiceLevel: "0.95",
        maxExpectedShortage: "",
        maxAogRisk: "",
        detail: "",
      },
    ]);
    setNextFloorId((value) => value + 1);
  }

  function updateFloor(id: number, update: Partial<DraftFloor>) {
    setFloors((current) =>
      current.map((floor) =>
        floor.id === id ? { ...floor, ...update } : floor,
      ),
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (parentRunId && !rerunReady) {
      setValidationError(
        repairChangeAvailable && !useCurrentTrustedRepair
          ? "Confirm use of the current trusted repair assumptions before rerunning."
          : "Change a permitted planning input before rerunning this parent.",
      );
      return;
    }
    if (scopeKind === "explicit" && parsedScope.error) {
      setValidationError(parsedScope.error);
      return;
    }
    const numericBudget = Number(budget);
    const numericHorizon = Number(horizon);
    const numericTimeLimit = Number(timeLimit);
    const weightValues = [
      shortageWeight,
      aogWeight,
      holdingWeight,
      orderingWeight,
    ].map(Number);
    if (!Number.isFinite(numericBudget) || numericBudget < 0) {
      setValidationError("Budget must be a non-negative amount.");
      return;
    }
    if (!Number.isInteger(numericHorizon) || numericHorizon <= 0) {
      setValidationError("Horizon must be a positive whole number of days.");
      return;
    }
    if (
      !Number.isFinite(numericTimeLimit) ||
      numericTimeLimit <= 0 ||
      numericTimeLimit > 600
    ) {
      setValidationError("Solver time limit must be between 1 and 600 seconds.");
      return;
    }
    if (
      weightValues.some((value) => !Number.isFinite(value) || value < 0) ||
      weightValues.every((value) => value === 0)
    ) {
      setValidationError(
        "Objective weights must be non-negative and at least one must be positive.",
      );
      return;
    }

    const mandatoryFloors: Record<string, PlanningMandatoryFloor[]> = {};
    for (const floor of floors) {
      if (
        !floor.decisionKey ||
        (scopeKind === "explicit" && !scopeIds.includes(floor.decisionKey))
      ) {
        setValidationError(
          `Floor ${floor.floorId || floor.id} must reference a key in this scope.`,
        );
        return;
      }
      if (!floor.floorId.trim() || !floor.source.trim()) {
        setValidationError("Every mandatory floor needs an ID and source.");
        return;
      }
      const thresholds = [
        ["min_service_level", floor.minServiceLevel, true],
        ["max_expected_shortage", floor.maxExpectedShortage, false],
        ["max_aog_risk", floor.maxAogRisk, true],
      ] as const;
      const populated = thresholds.filter(
        ([, value]) => value.trim() !== "",
      );
      if (populated.length === 0) {
        setValidationError(
          "Every mandatory floor needs at least one threshold.",
        );
        return;
      }
      if (
        populated.some(([, value, unitInterval]) => {
          const threshold = Number(value);
          return (
            !Number.isFinite(threshold) ||
            threshold < 0 ||
            (unitInterval && threshold > 1)
          );
        })
      ) {
        setValidationError(
          "Service and AOG floors use 0–1; shortage floors must be non-negative.",
        );
        return;
      }
      const wire: PlanningMandatoryFloor = {
        floor_id: floor.floorId.trim(),
        source: floor.source.trim(),
        detail: floor.detail.trim() || null,
      };
      for (const [field, value] of populated) {
        wire[field] = value;
      }
      mandatoryFloors[floor.decisionKey] = [
        ...(mandatoryFloors[floor.decisionKey] ?? []),
        wire,
      ];
    }

    setValidationError(null);
    onSubmit({
      scope_kind: scopeKind,
      keys: scopeKind === "explicit" ? parsedScope.keys : [],
      budget,
      horizon_days: numericHorizon,
      currency: currency.trim().toUpperCase(),
      time_limit_seconds: numericTimeLimit,
      parent_run_id: parentRunId || null,
      objective_weights: {
        shortage_reduction_weight: shortageWeight,
        aog_risk_reduction_weight: aogWeight,
        holding_cost_penalty_weight: holdingWeight,
        ordering_cost_penalty_weight: orderingWeight,
        criticality_weights: criticalityWeights,
      },
      mandatory_floors: mandatoryFloors,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New advisory plan</CardTitle>
        <p className="text-xs text-ink-2">
          Scope, budget, horizon, currency, and objective assumptions are
          frozen with the run.
        </p>
      </CardHeader>
      <CardContent>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-medium text-ink-2">
              Planning scope
            </legend>
            <label className="flex items-start gap-2 text-sm text-ink">
              <input
                type="radio"
                name="scope-kind"
                value="all_eligible"
                checked={scopeKind === "all_eligible"}
                onChange={() => setScopeKind("all_eligible")}
              />
              <span>
                Full eligible portfolio
                <span className="block text-xs text-ink-3">
                  The server resolves this tenant’s authoritative key universe;
                  no key list is sent by the browser.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm text-ink">
              <input
                type="radio"
                name="scope-kind"
                value="explicit"
                checked={scopeKind === "explicit"}
                onChange={() => setScopeKind("explicit")}
              />
              <span>
                Explicit preview (up to 200 keys)
                <span className="block text-xs text-ink-3">
                  Use for a bounded review before a full-network run.
                </span>
              </span>
            </label>
          </fieldset>

          {scopeKind === "explicit" && (
            <label className={labelClass}>
              Explicit keys
              <textarea
                value={scope}
                onChange={(event) => setScope(event.target.value)}
                rows={4}
                placeholder={"HYD-PUMP-001@YYZ\nVALVE-MOD-117@YYZ"}
                aria-describedby="planning-scope-help"
                className="w-full resize-y rounded-control border border-line bg-panel px-2 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              />
              <span id="planning-scope-help" className="font-normal text-ink-3">
                One PN@LOCATION per line. Selected keys must share the
                requested horizon and currency.
              </span>
            </label>
          )}

          <div className="grid grid-cols-2 gap-3">
            <label className={labelClass}>
              Acquisition budget
              <input
                aria-label="Acquisition budget"
                inputMode="decimal"
                value={budget}
                onChange={(event) => setBudget(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className={labelClass}>
              Currency
              <input
                aria-label="Currency"
                maxLength={3}
                value={currency}
                onChange={(event) => setCurrency(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className={labelClass}>
              Horizon (days)
              <input
                aria-label="Horizon days"
                inputMode="numeric"
                value={horizon}
                onChange={(event) => setHorizon(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className={labelClass}>
              Solver limit (seconds)
              <input
                aria-label="Solver time limit"
                inputMode="decimal"
                value={timeLimit}
                onChange={(event) => setTimeLimit(event.target.value)}
                className={inputClass}
              />
            </label>
          </div>

          <fieldset className="rounded-control border border-line p-3">
            <legend className="px-1 text-xs font-semibold text-ink-2">
              Objective weights
            </legend>
            <div className="grid grid-cols-2 gap-3">
              <label className={labelClass}>
                Shortage reduction
                <input
                  aria-label="Shortage reduction weight"
                  value={shortageWeight}
                  onChange={(event) => setShortageWeight(event.target.value)}
                  className={inputClass}
                />
              </label>
              <label className={labelClass}>
                AOG risk reduction
                <input
                  aria-label="AOG risk reduction weight"
                  value={aogWeight}
                  onChange={(event) => setAogWeight(event.target.value)}
                  className={inputClass}
                />
              </label>
              <label className={labelClass}>
                Holding cost penalty
                <input
                  aria-label="Holding cost penalty weight"
                  value={holdingWeight}
                  onChange={(event) => setHoldingWeight(event.target.value)}
                  className={inputClass}
                />
              </label>
              <label className={labelClass}>
                Ordering cost penalty
                <input
                  aria-label="Ordering cost penalty weight"
                  value={orderingWeight}
                  onChange={(event) => setOrderingWeight(event.target.value)}
                  className={inputClass}
                />
              </label>
            </div>
            <p className="mt-2 text-xs font-normal text-ink-3">
              Criticality multipliers for tiers 1–5:{" "}
              {["1", "2", "3", "4", "5"]
                .map((tier) => `${criticalityWeights[tier]}×`)
                .join(", ")}
              . Saved reruns preserve the parent values.
            </p>
          </fieldset>

          <fieldset className="flex flex-col gap-3 rounded-control border border-line p-3">
            <legend className="px-1 text-xs font-semibold text-ink-2">
              Mandatory floors
            </legend>
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-ink-3">
                Optional planner-entered hard constraints
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={addFloor}
              >
                Add floor
              </Button>
            </div>
            {floors.length === 0 ? (
              <p className="text-xs text-ink-3">
                No additional planner-entered floors. Candidate hard
                constraints still apply.
              </p>
            ) : (
              floors.map((floor, index) => (
                <div
                  key={floor.id}
                  className="flex flex-col gap-2 border-t border-line pt-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-ink">
                      Floor {index + 1}
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setFloors((current) =>
                          current.filter((item) => item.id !== floor.id),
                        )
                      }
                      className="text-xs text-bad hover:underline"
                      aria-label={`Remove floor ${index + 1}`}
                    >
                      Remove
                    </button>
                  </div>
                  <label className={labelClass}>
                    Scope key
                    {scopeKind === "explicit" ? (
                      <select
                        aria-label={`Floor ${index + 1} scope key`}
                        value={floor.decisionKey}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            decisionKey: event.target.value,
                          })
                        }
                        className={inputClass}
                      >
                        <option value="">Select a scope key</option>
                        {scopeIds.map((key) => (
                          <option key={key} value={key}>
                            {key}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        aria-label={`Floor ${index + 1} scope key`}
                        value={floor.decisionKey}
                        placeholder="PN@LOCATION"
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            decisionKey: event.target.value,
                          })
                        }
                        className={inputClass}
                      />
                    )}
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label className={labelClass}>
                      Floor ID
                      <input
                        aria-label={`Floor ${index + 1} ID`}
                        value={floor.floorId}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            floorId: event.target.value,
                          })
                        }
                        className={inputClass}
                      />
                    </label>
                    <label className={labelClass}>
                      Source
                      <input
                        aria-label={`Floor ${index + 1} source`}
                        value={floor.source}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            source: event.target.value,
                          })
                        }
                        className={inputClass}
                      />
                    </label>
                    <label className={labelClass}>
                      Minimum service level
                      <input
                        aria-label={`Floor ${index + 1} minimum service level`}
                        value={floor.minServiceLevel}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            minServiceLevel: event.target.value,
                          })
                        }
                        placeholder="Optional · 0–1"
                        className={inputClass}
                      />
                    </label>
                    <label className={labelClass}>
                      Maximum expected shortage
                      <input
                        aria-label={`Floor ${index + 1} maximum expected shortage`}
                        value={floor.maxExpectedShortage}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            maxExpectedShortage: event.target.value,
                          })
                        }
                        placeholder="Optional · non-negative"
                        className={inputClass}
                      />
                    </label>
                    <label className={labelClass}>
                      Maximum AOG risk
                      <input
                        aria-label={`Floor ${index + 1} maximum AOG risk`}
                        value={floor.maxAogRisk}
                        onChange={(event) =>
                          updateFloor(floor.id, {
                            maxAogRisk: event.target.value,
                          })
                        }
                        placeholder="Optional · 0–1"
                        className={inputClass}
                      />
                    </label>
                  </div>
                </div>
              ))
            )}
          </fieldset>

          <label className={labelClass}>
            Compare assumptions with
            <select
              aria-label="Parent planning run"
              value={parentRunId}
              onChange={(event) => {
                setParentRunId(event.target.value);
                setAppliedRerunId(null);
                setUseCurrentTrustedRepair(false);
                setValidationError(null);
              }}
              className={inputClass}
            >
              <option value="">No parent run</option>
              {terminalRuns.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.run_id.slice(0, 8)} · {run.status}
                </option>
              ))}
            </select>
          </label>

          {parentRunId && rerunConfigQuery.isPending && (
            <p role="status" className="text-xs text-ink-3">
              Loading the parent’s bounded saved configuration…
            </p>
          )}

          {parentRunId && rerunConfigQuery.data && (
            <div className="rounded-control border border-line p-3 text-xs">
              <p className="font-semibold text-ink">
                Trusted repair assumptions
              </p>
              <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-ink-2">
                <dt>Parent run</dt>
                <dd className="font-mono">
                  {
                    rerunConfigQuery.data.parent_model_profile
                      .repair_model_version
                  }
                </dd>
                <dt>Current trusted</dt>
                <dd className="font-mono">
                  {rerunConfigQuery.data.current_trusted_model_profile
                    ?.repair_model_version ?? "Unavailable"}
                </dd>
              </dl>
              {repairChangeAvailable ? (
                <label className="mt-3 flex items-start gap-2 text-ink">
                  <input
                    type="checkbox"
                    checked={useCurrentTrustedRepair}
                    onChange={(event) =>
                      setUseCurrentTrustedRepair(event.target.checked)
                    }
                  />
                  <span>
                    Use current trusted repair assumptions
                    <span className="block font-normal text-ink-3">
                      The repair model is trusted server state and cannot be
                      replaced with a browser-supplied version.
                    </span>
                  </span>
                </label>
              ) : (
                <p className="mt-3 text-ink-3">
                  The current trusted repair model matches the parent run.
                </p>
              )}
              {!repairChangeAvailable && !editableRerunChange && (
                <p role="status" className="mt-2 text-warn">
                  Change a permitted input to create a rerun. Unchanged saved
                  inputs resolve to the existing parent run.
                </p>
              )}
            </div>
          )}

          {(validationError || error || rerunConfigQuery.error) && (
            <p role="alert" className="text-xs text-bad">
              {validationError ??
                error?.message ??
                rerunConfigQuery.error?.message}
            </p>
          )}
          <Button
            type="submit"
            disabled={
              isPending ||
              Boolean(parentRunId && rerunConfigQuery.isPending) ||
              Boolean(parentRunId && !rerunReady)
            }
          >
            {isPending ? "Submitting plan…" : "Submit advisory plan"}
          </Button>
          <p className="text-xs text-ink-3">
            Submission is idempotent: identical immutable inputs return the
            existing run instead of starting duplicate work.
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
