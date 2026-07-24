import { beforeEach, describe, expect, it, vi } from "vitest";
import { getWhoami } from "./whoami";

/**
 * Mirrors services/agent-spine/src/trax_io_spine/bff/whoami.py's
 * `WhoamiResponse`/`TenantRef` field names exactly (tenant_uuid/slug/name/role;
 * user_id/active/tenants) — see also
 * services/agent-spine/tests/pg/test_c5_whoami_reader.py for the backend's
 * own proof of this shape.
 */
describe("whoami api", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("GETs the whoami route and returns active + tenants", async () => {
    const body = {
      user_id: "u1",
      active: { tenant_uuid: "T1", slug: "acme", name: "Acme", role: "owner" },
      tenants: [{ tenant_uuid: "T1", slug: "acme", name: "Acme", role: "owner" }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 }),
    );

    const out = await getWhoami();

    expect(out.active?.slug).toBe("acme");
    expect(out.tenants).toHaveLength(1);
    const url = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls[0][0];
    expect(url).toContain("/v1/auth/whoami");
  });

  it("tolerates the no-membership state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user_id: "u1", active: null, tenants: [] }), { status: 200 }),
    );

    const out = await getWhoami();

    expect(out.active).toBeNull();
    expect(out.tenants).toEqual([]);
  });

  it("propagates a 401 (e.g. a signed-in user with zero tenant memberships) as a rejected promise, not a silent empty result", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "missing or invalid token" }), { status: 401 }),
    );

    await expect(getWhoami()).rejects.toThrow();
  });
});
