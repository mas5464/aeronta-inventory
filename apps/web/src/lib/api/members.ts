import { activeTenant, request } from "@/lib/api/client";

/**
 * Members management + tenant-activation client (C2 Task 7), mirroring
 * services/agent-spine/src/trax_io_spine/bff/members_routes.py's
 * `/v1/tenants/{tenant}/members*` + `/v1/auth/activate-tenant` routes.
 *
 * Kept as its own module (rather than folded into `bffClient` in client.ts)
 * per the C2 task-7 brief — still built entirely on the shared `request<T>`
 * helper, so auth-header attach + 401 handling + ApiError mapping come free.
 */

/** A tenant's membership row — `email` is best-effort (only present when the
 * BFF's Admin API seam is configured; falls back to `user_id` in the UI). */
export interface Member {
  user_id: string;
  role: string;
  created_at: string;
  email?: string;
}

/** Roles invitable via the invite form — `owner` is never an invite target
 * (mirrors the BFF's `InviteRole` literal, which excludes it). */
export type InviteRole = "admin" | "planner" | "viewer";

export function getMembers(tenant: string = activeTenant()): Promise<Member[]> {
  return request<Member[]>(`/v1/tenants/${encodeURIComponent(tenant)}/members`);
}

export function inviteMember(
  tenant: string,
  email: string,
  role: InviteRole,
): Promise<{ user_id: string; role: string }> {
  return request<{ user_id: string; role: string }>(
    `/v1/tenants/${encodeURIComponent(tenant)}/members/invite`,
    { method: "POST", body: JSON.stringify({ email, role }) },
  );
}

export function updateMemberRole(tenant: string, userId: string, role: string): Promise<void> {
  return request<void>(
    `/v1/tenants/${encodeURIComponent(tenant)}/members/${encodeURIComponent(userId)}`,
    { method: "PATCH", body: JSON.stringify({ role }) },
  );
}

export function removeMember(tenant: string, userId: string): Promise<void> {
  return request<void>(
    `/v1/tenants/${encodeURIComponent(tenant)}/members/${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
}

/**
 * Switches the caller's active tenant (writes a `tenant_preferences` row the
 * next-mint claims hook reads — see pg/members.py's `set_preference`).
 * Deliberately outside `/v1/tenants/{tenant}/...` — see the BFF route's
 * docstring for why. Resolves to `undefined` on the BFF's 204.
 */
export function activateTenant(tenantUuid: string): Promise<void> {
  return request<void>("/v1/auth/activate-tenant", {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantUuid }),
  });
}
