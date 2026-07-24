import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { SignupWizard } from "./SignupWizard";

/**
 * Hoisted mock fns (same pattern as TenantSwitcher.test.tsx / billing.test.ts)
 * so the `vi.mock` factories below — themselves hoisted above these `const`s
 * by Vitest — can close over them, and each test can assert on / override
 * call behavior directly without re-importing the mocked module.
 */
const mockSignUp = vi.hoisted(() => vi.fn());
const mockGetSession = vi.hoisted(() => vi.fn());
const mockRefreshSession = vi.hoisted(() => vi.fn());
const mockRpc = vi.hoisted(() => vi.fn());
const mockCreateCheckoutSession = vi.hoisted(() => vi.fn());
const mockGetPublicPrices = vi.hoisted(() => vi.fn());

vi.mock("@/lib/auth/supabase", () => ({
  supabase: {
    auth: {
      signUp: mockSignUp,
      getSession: mockGetSession,
      refreshSession: mockRefreshSession,
    },
    rpc: mockRpc,
  },
  authEnabled: true,
}));

vi.mock("@/lib/api/billing", () => ({
  createCheckoutSession: mockCreateCheckoutSession,
  getPublicPrices: mockGetPublicPrices,
}));

const DEFAULT_PRICES = [
  { id: "price_g_m", tier: "growth", interval: "month", unit_amount: 29900, currency: "usd" },
  { id: "price_g_y", tier: "growth", interval: "year", unit_amount: 299000, currency: "usd" },
];

function resetMocks() {
  // Matches the brief's fixture exactly: a brand-new signUp returns no
  // session (email confirmation required), while getSession — used by the
  // wizard's already-logged-in mount check and the confirm step's retry —
  // resolves truthy by default.
  mockSignUp.mockReset().mockResolvedValue({ data: { session: null }, error: null });
  mockGetSession.mockReset().mockResolvedValue({ data: { session: { user: { id: "u1" } } } });
  mockRefreshSession.mockReset().mockResolvedValue({ data: { session: {} }, error: null });
  mockRpc.mockReset().mockResolvedValue({ data: "tenant-uuid", error: null });
  mockCreateCheckoutSession.mockReset().mockResolvedValue("https://stripe/checkout");
  mockGetPublicPrices.mockReset().mockResolvedValue(DEFAULT_PRICES);
}

describe("SignupWizard", () => {
  beforeEach(resetMocks);

  it("new signup requires email confirmation before org step", async () => {
    render(<SignupWizard initialPlan="growth" />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "pw12345678" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    // signUp returned no session (confirmation required) → interstitial shown.
    expect(await screen.findByText(/confirm your email/i)).toBeInTheDocument();
  });

  it("an already-authenticated visitor skips the account step and starts on org", async () => {
    render(<SignupWizard initialPlan="growth" />);
    expect(await screen.findByLabelText(/organization/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
  });

  it("org step calls the create-tenant RPC, refreshes the session, then shows the plan step", async () => {
    render(<SignupWizard initialPlan="growth" />);
    const orgInput = await screen.findByLabelText(/organization/i);

    fireEvent.change(orgInput, { target: { value: "Acme Air" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByRole("button", { name: /monthly/i })).toBeInTheDocument();
    expect(mockRpc).toHaveBeenCalledWith("create_tenant_for_current_user", { p_name: "Acme Air" });
    expect(mockRefreshSession).toHaveBeenCalled();
  });

  it("org step surfaces a refreshSession error and does not advance to the plan step", async () => {
    mockRefreshSession.mockReset().mockResolvedValue({
      data: { session: null },
      error: { message: "boom" },
    });
    render(<SignupWizard initialPlan="growth" />);
    const orgInput = await screen.findByLabelText(/organization/i);

    fireEvent.change(orgInput, { target: { value: "Acme Air" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.queryByRole("button", { name: /monthly/i })).not.toBeInTheDocument();
  });

  it("org step surfaces an error and resets busy when the RPC call rejects", async () => {
    mockRpc.mockReset().mockRejectedValue(new Error("network down"));
    render(<SignupWizard initialPlan="growth" />);
    const orgInput = await screen.findByLabelText(/organization/i);

    fireEvent.change(orgInput, { target: { value: "Acme Air" } });
    const continueButton = screen.getByRole("button", { name: /continue/i });
    fireEvent.click(continueButton);

    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
    expect(continueButton).not.toBeDisabled();
  });

  it("plan step calls createCheckoutSession with the matching monthly price id", async () => {
    render(<SignupWizard initialPlan="growth" />);
    const orgInput = await screen.findByLabelText(/organization/i);
    fireEvent.change(orgInput, { target: { value: "Acme Air" } });
    fireEvent.click(screen.getByRole("button", { name: /continue/i }));
    const monthlyButton = await screen.findByRole("button", { name: /monthly/i });

    fireEvent.click(monthlyButton);

    await waitFor(() =>
      expect(mockCreateCheckoutSession).toHaveBeenCalledWith(expect.any(String), "price_g_m"),
    );
  });
});
