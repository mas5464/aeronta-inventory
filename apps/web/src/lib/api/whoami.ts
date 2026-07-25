import { request } from "./client";

/**
 * Mirrors services/agent-spine/src/trax_io_spine/bff/whoami.py's `TenantRef`
 * pydantic model field-for-field (tenant_uuid/slug/name/role).
 */
export type TenantRef = {
  tenant_uuid: string;
  slug: string;
  name: string;
  role: string;
};

/** Mirrors bff/whoami.py's `WhoamiResponse`. */
export type Whoami = {
  user_id: string;
  active: TenantRef | null;
  tenants: TenantRef[];
};

/**
 * The caller's identity + tenant memberships, straight from the verified
 * token (GET /v1/auth/whoami — C5 Task 5). Replaces C2's build-time
 * tenant-slug env map, which could not know about tenants created after
 * the last frontend deploy.
 *
 * Deliberately outside `/v1/tenants/{tenant}/...` on the BFF side (see that
 * route's docstring) — the caller may have no active tenant at all
 * (mid-signup), so there's no slug to gate on.
 *
 * A signed-in user with ZERO tenant memberships gets a 401 here, not an
 * empty list — the BFF's AuthMiddleware rejects any authed request whose
 * JWT lacks a `tenant_id` claim, and the claims hook omits that claim
 * entirely for such a user. This rejects like any other failed `request<T>`
 * call (an `ApiError`); callers (useAuth.tsx) are responsible for degrading
 * gracefully rather than crashing.
 *
 * Passes `signOutOn401: false` (Group A review fix): unlike every other 401
 * in the app, a 401 here is an EXPECTED outcome for a brand-new signed-up
 * user with no tenant yet, not an expired/invalid session. Without this
 * carve-out, `request<T>()`'s global `aeronta:unauthorized` dispatch would
 * sign that user out mid-signup — before `SignupWizard` ever gets to call
 * `createOrg()` with the still-valid session, and before the "no tenant
 * access" card (useAuth.tsx/App.tsx) could ever be seen, since the sign-out
 * bounces to the sign-in screen a beat later. See client.ts's
 * `RequestOptions` doc comment for the full rationale.
 */
export function getWhoami(): Promise<Whoami> {
  return request<Whoami>("/v1/auth/whoami", undefined, { signOutOn401: false });
}
