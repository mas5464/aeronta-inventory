import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TenantSwitcher } from "@/components/TenantSwitcher";
import type { TenantRef } from "@/lib/api/whoami";

/**
 * `state`/mocks are `vi.hoisted` so the `vi.mock` factories below (which are
 * themselves hoisted above these `const`s by Vitest) can close over them.
 * Each test mutates `state` in place before rendering — the mocked modules
 * return these SAME object/array references, so in-place mutation (not
 * reassignment) is what the component sees on its next render.
 *
 * `tenants` (C5 Task 8) replaces the old build-time tenant-slug-map
 * fixture — `useAuth()` now sources the tenant list from
 * `GET /v1/auth/whoami`, so the mock lives on the `useAuth` mock rather
 * than `@/lib/auth/supabase`.
 */
const state = vi.hoisted(() => ({
  session: null as { user: { id: string } } | null,
  tenantSlug: null as string | null,
  tenants: [] as TenantRef[],
}));

const mockActivateTenant = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
const mockRefreshSession = vi.hoisted(() => vi.fn().mockResolvedValue({ data: {}, error: null }));

vi.mock("@/lib/auth/useAuth", () => ({
  useAuth: () => ({ session: state.session, tenantSlug: state.tenantSlug, tenants: state.tenants }),
}));

vi.mock("@/lib/auth/supabase", () => ({
  supabase: { auth: { refreshSession: mockRefreshSession } },
}));

vi.mock("@/lib/api/members", () => ({
  activateTenant: mockActivateTenant,
}));

function setTenants(list: TenantRef[]) {
  state.tenants = list;
}

const acme: TenantRef = { tenant_uuid: "uuid-1", slug: "acme", name: "Acme", role: "owner" };
const aerontaDemo: TenantRef = {
  tenant_uuid: "uuid-2",
  slug: "aeronta-demo",
  name: "Aeronta Demo",
  role: "planner",
};

describe("TenantSwitcher", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    state.session = null;
    state.tenantSlug = null;
    setTenants([]);
  });

  it("renders nothing when there's only one (or zero) known tenant, even if authenticated", () => {
    state.session = { user: { id: "u1" } };
    setTenants([acme]);

    const { container } = render(<TenantSwitcher />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when unauthenticated, even with multiple known tenants", () => {
    state.session = null;
    setTenants([acme, aerontaDemo]);

    const { container } = render(<TenantSwitcher />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders a select of every known tenant, defaulted to the current one, when authenticated with >1 tenant", () => {
    state.session = { user: { id: "u1" } };
    state.tenantSlug = "acme";
    setTenants([acme, aerontaDemo]);

    render(<TenantSwitcher />);

    const select = screen.getByRole("combobox", { name: "Switch tenant" });
    expect(select).toHaveValue("uuid-1");
    expect(screen.getByRole("option", { name: "acme" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "aeronta-demo" })).toBeInTheDocument();
  });

  it("selecting a different tenant activates it, refreshes the session, then reloads", async () => {
    state.session = { user: { id: "u1" } };
    state.tenantSlug = "acme";
    setTenants([acme, aerontaDemo]);
    const reloadMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload: reloadMock });
    const user = userEvent.setup();

    render(<TenantSwitcher />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Switch tenant" }), "uuid-2");

    expect(mockActivateTenant).toHaveBeenCalledWith("uuid-2");
    await vi.waitFor(() => expect(mockRefreshSession).toHaveBeenCalled());
    await vi.waitFor(() => expect(reloadMock).toHaveBeenCalled());
  });

  it("activateTenant rejection surfaces an error, disables reload, and re-enables the select", async () => {
    state.session = { user: { id: "u1" } };
    state.tenantSlug = "acme";
    setTenants([acme, aerontaDemo]);
    mockActivateTenant.mockRejectedValueOnce(new Error("Unauthorized"));
    const reloadMock = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload: reloadMock });
    const user = userEvent.setup();

    render(<TenantSwitcher />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Switch tenant" }), "uuid-2");

    // Error text appears.
    await vi.waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Could not switch tenant/i),
    );
    // Reload was NOT called.
    expect(reloadMock).not.toHaveBeenCalled();
    // Select is now re-enabled so user can retry.
    expect(screen.getByRole("combobox", { name: "Switch tenant" })).not.toBeDisabled();
  });
});
