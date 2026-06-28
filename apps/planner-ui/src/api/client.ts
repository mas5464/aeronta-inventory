import type {
  ActionResult,
  KillSwitchState,
  QueueRow,
  RecommendationDetail,
  RejectReason,
} from "./types";

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
  getQueue(tenant: string): Promise<QueueRow[]>;
  getDetail(tenant: string, id: string): Promise<RecommendationDetail>;
  approve(tenant: string, id: string): Promise<ActionResult>;
  reject(tenant: string, id: string, reason: RejectReason, detail?: string): Promise<ActionResult>;
  defer(tenant: string, id: string): Promise<ActionResult>;
  getKillSwitch(tenant: string): Promise<KillSwitchState>;
  setKillSwitch(tenant: string, engaged: boolean): Promise<KillSwitchState>;
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

  async getQueue(tenant: string): Promise<QueueRow[]> {
    return this.json(await fetch(`${this.base(tenant)}/recommendations`));
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

export class FakePlannerClient implements PlannerClient {
  private entries: Map<string, FakeEntry>;
  private engaged = false;

  constructor(seed: FakeEntry[]) {
    this.entries = new Map(seed.map((e) => [e.row.recommendation_id, e]));
  }

  private require(id: string): FakeEntry {
    const e = this.entries.get(id);
    if (!e) throw new PlannerError(404, `unknown recommendation ${id}`);
    return e;
  }

  async getQueue(_tenant?: string): Promise<QueueRow[]> {
    return [...this.entries.values()]
      .filter((e) => e.row.status === "pending")
      .map((e) => e.row)
      .sort((a, b) => b.priority_score - a.priority_score);
  }

  async getDetail(_tenant: string, id: string): Promise<RecommendationDetail> {
    return this.require(id).detail;
  }

  async approve(_tenant: string, id: string): Promise<ActionResult> {
    if (this.engaged) throw new PlannerError(423, "kill switch engaged");
    const e = this.require(id);
    if (e.detail.proposed_policy === null) {
      throw new PlannerError(409, `recommendation ${id} has no writable policy`);
    }
    e.row = { ...e.row, status: "approved" };
    e.detail = { ...e.detail, status: "approved" };
    return { recommendation_id: id, status: "approved", writeback: null, message: "written" };
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

  async getKillSwitch(_tenant?: string): Promise<KillSwitchState> {
    return { engaged: this.engaged };
  }

  async setKillSwitch(_tenant: string, engaged: boolean): Promise<KillSwitchState> {
    this.engaged = engaged;
    return { engaged };
  }
}
