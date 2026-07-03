import type {
  ActionResult,
  BulkApproveFilter,
  BulkApproveResult,
  BvrReport,
  DashboardSummary,
  HistoryEntry,
  KillSwitchState,
  PagedQueue,
  PartContext,
  PolicyView,
  QueueRow,
  RecommendationDetail,
  RejectReason,
  RollbackRequest,
  RollbackResult,
  TaskStatus,
} from "./types";
import { SAMPLE_BVR, SAMPLE_DASHBOARD, SAMPLE_PART_CONTEXT } from "./sample";

export class PlannerError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "PlannerError";
  }
}

export interface PlannerClient {
  // limit defaults to 50 (server default), max 200; offset defaults to 0.
  getQueue(
    tenant: string,
    status?: TaskStatus,
    limit?: number,
    offset?: number,
  ): Promise<PagedQueue>;
  getDetail(tenant: string, id: string): Promise<RecommendationDetail>;
  approve(tenant: string, id: string): Promise<ActionResult>;
  reject(tenant: string, id: string, reason: RejectReason, detail?: string): Promise<ActionResult>;
  defer(tenant: string, id: string): Promise<ActionResult>;
  bulkApprove(tenant: string, filter: BulkApproveFilter): Promise<BulkApproveResult>;
  getHistory(tenant: string, pn: string, location: string): Promise<HistoryEntry[]>;
  rollback(tenant: string, req: RollbackRequest): Promise<RollbackResult>;
  getKillSwitch(tenant: string): Promise<KillSwitchState>;
  setKillSwitch(tenant: string, engaged: boolean): Promise<KillSwitchState>;
  getPartContext(tenant: string, pn: string, location: string): Promise<PartContext>;
  getDashboard(tenant: string): Promise<DashboardSummary>;
  getBvr(tenant: string): Promise<BvrReport>;
  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string;
}

// --------------------------------------------------------------------------- //
// HTTP client against a running BFF (uvicorn create_planner_app)
// --------------------------------------------------------------------------- //

export class HttpPlannerClient implements PlannerClient {
  constructor(private readonly baseUrl: string) {}

  private base(tenant: string): string {
    return `${this.baseUrl.replace(/\/$/, "")}/v1/tenants/${encodeURIComponent(tenant)}`;
  }

  private async json<T>(res: Response): Promise<T> {
    if (!res.ok) {
      let message = `HTTP ${res.status}`;
      try {
        const body = (await res.json()) as { detail?: string };
        if (body?.detail) message = body.detail;
      } catch {
        // non-JSON error body; keep the status message
      }
      throw new PlannerError(res.status, message);
    }
    return (await res.json()) as T;
  }

  async getQueue(
    tenant: string,
    status: TaskStatus = "pending",
    limit = 50,
    offset = 0,
  ): Promise<PagedQueue> {
    const q = new URLSearchParams({ status, limit: String(limit), offset: String(offset) });
    return this.json(await fetch(`${this.base(tenant)}/recommendations?${q}`));
  }

  async getDetail(tenant: string, id: string): Promise<RecommendationDetail> {
    return this.json(await fetch(`${this.base(tenant)}/recommendations/${encodeURIComponent(id)}`));
  }

  async approve(tenant: string, id: string): Promise<ActionResult> {
    return this.json(
      await fetch(`${this.base(tenant)}/recommendations/${encodeURIComponent(id)}/approve`, {
        method: "POST",
      }),
    );
  }

  async reject(
    tenant: string,
    id: string,
    reason: RejectReason,
    detail = "",
  ): Promise<ActionResult> {
    return this.json(
      await fetch(`${this.base(tenant)}/recommendations/${encodeURIComponent(id)}/reject`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ reason, detail }),
      }),
    );
  }

  async defer(tenant: string, id: string): Promise<ActionResult> {
    return this.json(
      await fetch(`${this.base(tenant)}/recommendations/${encodeURIComponent(id)}/defer`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      }),
    );
  }

  async bulkApprove(tenant: string, filter: BulkApproveFilter): Promise<BulkApproveResult> {
    return this.json(
      await fetch(`${this.base(tenant)}/recommendations/bulk-approve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(filter),
      }),
    );
  }

  async getHistory(tenant: string, pn: string, location: string): Promise<HistoryEntry[]> {
    const q = new URLSearchParams({ pn, location });
    return this.json(await fetch(`${this.base(tenant)}/history?${q}`));
  }

  async rollback(tenant: string, req: RollbackRequest): Promise<RollbackResult> {
    return this.json(
      await fetch(`${this.base(tenant)}/rollback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(req),
      }),
    );
  }

  async getKillSwitch(tenant: string): Promise<KillSwitchState> {
    return this.json(await fetch(`${this.base(tenant)}/killswitch`));
  }

  async setKillSwitch(tenant: string, engaged: boolean): Promise<KillSwitchState> {
    return this.json(
      await fetch(`${this.base(tenant)}/killswitch`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ engaged }),
      }),
    );
  }

  async getPartContext(tenant: string, pn: string, location: string): Promise<PartContext> {
    return this.json(
      await fetch(
        `${this.base(tenant)}/parts/${encodeURIComponent(pn)}/${encodeURIComponent(location)}`,
      ),
    );
  }

  async getDashboard(tenant: string): Promise<DashboardSummary> {
    return this.json(await fetch(`${this.base(tenant)}/dashboard`));
  }

  async getBvr(tenant: string): Promise<BvrReport> {
    return this.json(await fetch(`${this.base(tenant)}/reports/bvr`));
  }

  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string {
    return `${this.base(tenant)}/reports/bvr.${kind}`;
  }
}

// --------------------------------------------------------------------------- //
// In-memory fake — drives tests and offline `npm run dev` (VITE_FAKE=1).
// Mirrors the BFF lifecycle: approve/reject/defer remove the row from the
// pending queue; approve while the kill switch is engaged throws 423.
// --------------------------------------------------------------------------- //

interface FakeEntry {
  row: QueueRow;
  detail: RecommendationDetail;
}

const FAKE_AGENT_VERSION = "fake-1";
const keyOf = (pn: string, location: string) => JSON.stringify([pn, location]);
const policyValues = (p: PolicyView): Record<string, number> => ({
  rop: p.rop,
  eoq: p.eoq,
  safety_stock: p.safety_stock,
  max_stock: p.max_stock,
});

export class FakePlannerClient implements PlannerClient {
  private entries: Map<string, FakeEntry>;
  private engaged = false;
  // Writeback ledger + current applied values, keyed by an injective (pn, location) string.
  private historyByKey = new Map<string, HistoryEntry[]>();
  private levels = new Map<string, Record<string, number>>();

  constructor(seed: FakeEntry[], seedHistory: HistoryEntry[] = []) {
    // Copy each entry — approve/reject/defer reassign .row/.detail, so without this
    // the fake would mutate the caller's seed (and leak state across tests).
    this.entries = new Map(seed.map((e) => [e.row.recommendation_id, { ...e }]));
    for (const h of seedHistory) {
      const k = keyOf(h.pn, h.location);
      (this.historyByKey.get(k) ?? this.historyByKey.set(k, []).get(k)!).push(h);
      if (h.status === "written") this.levels.set(k, h.new_values);
    }
  }

  // Append a WRITTEN ledger entry mirroring the server's _record (no 90-day window —
  // that policy is enforced server-side and covered by the Python suites).
  private record(
    tenant: string,
    pn: string,
    location: string,
    newValues: Record<string, number>,
    provenanceId: string,
    tier: HistoryEntry["tier"],
    principal: string,
    changedAt: string,
  ): HistoryEntry {
    const k = keyOf(pn, location);
    const entries = this.historyByKey.get(k) ?? this.historyByKey.set(k, []).get(k)!;
    const oldValues = this.levels.get(k) ?? null;
    const parent = [...entries].reverse().find((e) => e.status === "written")?.version ?? null;
    const entry: HistoryEntry = {
      tenant_id: tenant,
      pn,
      location,
      version: entries.length + 1,
      status: "written",
      old_values: oldValues,
      new_values: newValues,
      provenance_id: provenanceId,
      tier,
      agent_version: FAKE_AGENT_VERSION,
      changed_by_principal: principal,
      idempotency_key: null,
      parent_version: parent,
      changed_at: changedAt,
    };
    entries.push(entry);
    this.levels.set(k, newValues);
    return entry;
  }

  private require(id: string): FakeEntry {
    const e = this.entries.get(id);
    if (!e) throw new PlannerError(404, `unknown recommendation ${id}`);
    return e;
  }

  async getQueue(
    _tenant?: string,
    status: TaskStatus = "pending",
    limit = 50,
    offset = 0,
  ): Promise<PagedQueue> {
    const all = [...this.entries.values()]
      .filter((e) => e.row.status === status)
      .map((e) => e.row)
      .sort((a, b) => b.priority_score - a.priority_score);
    return { items: all.slice(offset, offset + limit), total: all.length, limit, offset };
  }

  async getDetail(_tenant: string, id: string): Promise<RecommendationDetail> {
    return this.require(id).detail;
  }

  async approve(tenant: string, id: string): Promise<ActionResult> {
    if (this.engaged) throw new PlannerError(423, "kill switch engaged");
    const e = this.require(id);
    if (e.detail.proposed_policy === null) {
      throw new PlannerError(409, `recommendation ${id} has no writable policy`);
    }
    const entry = this.record(
      tenant,
      e.detail.pn,
      e.detail.location,
      policyValues(e.detail.proposed_policy),
      e.detail.provenance_id ?? "unknown",
      e.detail.tier,
      "agent-spine",
      new Date().toISOString(),
    );
    e.row = { ...e.row, status: "approved" };
    e.detail = { ...e.detail, status: "approved" };
    return {
      recommendation_id: id,
      status: "approved",
      writeback: {
        tenant_id: entry.tenant_id,
        pn: entry.pn,
        location: entry.location,
        status: entry.status,
        old_values: entry.old_values,
        new_values: entry.new_values,
        written_at: entry.changed_at,
        error_message: null,
      },
      message: `written (${entry.status})`,
    };
  }

  async reject(_tenant: string, id: string, reason: RejectReason): Promise<ActionResult> {
    const e = this.require(id);
    e.row = { ...e.row, status: "rejected" };
    e.detail = { ...e.detail, status: "rejected" };
    return { recommendation_id: id, status: "rejected", writeback: null, message: reason };
  }

  async defer(_tenant: string, id: string): Promise<ActionResult> {
    const e = this.require(id);
    e.row = { ...e.row, status: "deferred" };
    e.detail = { ...e.detail, status: "deferred" };
    return { recommendation_id: id, status: "deferred", writeback: null, message: "deferred" };
  }

  // Largest per-field |Δ| as a percentage of the current policy — mirrors the BFF's
  // delta_pct so the offline filter behaves like the server's.
  private deltaPct(detail: RecommendationDetail): number {
    const cur = detail.current_policy;
    const next = detail.proposed_policy;
    if (cur === null || next === null) return 0;
    const fields = ["rop", "eoq", "safety_stock", "max_stock"] as const;
    return Math.max(
      ...fields.map((f) => {
        const base = cur[f];
        if (base === 0) return next[f] === 0 ? 0 : Infinity;
        return (Math.abs(next[f] - base) / base) * 100;
      }),
    );
  }

  private matches(e: FakeEntry, f: BulkApproveFilter): boolean {
    if (f.tiers && !f.tiers.includes(e.row.tier)) return false;
    if (f.criticality_min != null && e.row.criticality_tier < f.criticality_min) return false;
    if (f.types && !f.types.includes(e.row.type)) return false;
    if (f.max_delta_pct != null && this.deltaPct(e.detail) > f.max_delta_pct) return false;
    return true;
  }

  async bulkApprove(tenant: string, filter: BulkApproveFilter): Promise<BulkApproveResult> {
    if (this.engaged) throw new PlannerError(423, "kill switch engaged");
    const targets = [...this.entries.values()].filter(
      (e) => e.row.status === "pending" && e.row.approvable && this.matches(e, filter),
    );
    const results: ActionResult[] = [];
    for (const e of targets) results.push(await this.approve(tenant, e.row.recommendation_id));
    return { approved_count: results.length, results };
  }

  async getHistory(_tenant: string, pn: string, location: string): Promise<HistoryEntry[]> {
    return [...(this.historyByKey.get(keyOf(pn, location)) ?? [])];
  }

  async rollback(tenant: string, req: RollbackRequest): Promise<RollbackResult> {
    const k = keyOf(req.pn, req.location);
    const entries = this.historyByKey.get(k) ?? [];
    const latest = [...entries].reverse().find((e) => e.status === "written");
    const base = { tenant_id: req.tenant_id, pn: req.pn, location: req.location };
    if (!latest || latest.old_values === null) {
      return { ...base, status: "nothing_to_revert" };
    }
    const current = this.levels.get(k) ?? null;
    const toValues = { ...latest.old_values };
    const entry = this.record(
      tenant,
      req.pn,
      req.location,
      toValues,
      `rollback:${latest.provenance_id}`,
      latest.tier,
      req.principal ?? "planner",
      req.requested_at,
    );
    return {
      ...base,
      status: "rolled_back",
      from_values: current,
      to_values: toValues,
      reverted_from_version: latest.version,
      new_version: entry.version,
      rolled_back_at: req.requested_at,
    };
  }

  async getKillSwitch(_tenant?: string): Promise<KillSwitchState> {
    return { engaged: this.engaged };
  }

  async setKillSwitch(_tenant: string, engaged: boolean): Promise<KillSwitchState> {
    this.engaged = engaged;
    return { engaged };
  }

  async getPartContext(_tenant: string, pn: string, location: string): Promise<PartContext> {
    return SAMPLE_PART_CONTEXT(pn, location);
  }

  async getDashboard(_tenant: string): Promise<DashboardSummary> {
    return SAMPLE_DASHBOARD;
  }

  async getBvr(_tenant: string): Promise<BvrReport> {
    return SAMPLE_BVR;
  }

  bvrDocumentUrl(tenant: string, kind: "html" | "pdf"): string {
    return `/v1/tenants/${tenant}/reports/bvr.${kind}`;
  }
}
