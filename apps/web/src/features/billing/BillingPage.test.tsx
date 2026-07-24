import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BillingPage } from "./BillingPage";
import * as billing from "@/lib/api/billing";

function renderWith(summary: Partial<billing.BillingSummary>) {
  vi.spyOn(billing, "getBilling").mockResolvedValue({
    plan_tier: "growth",
    subscription_status: "active",
    key_quota: 25000,
    keys_used: 5000,
    current_period_end: null,
    trial_ends_at: null,
    ...summary,
  } as billing.BillingSummary);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BillingPage tenant="acme" role="owner" />
    </QueryClientProvider>,
  );
}

describe("BillingPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("active plan shows the tier, a usage meter, and Manage billing", async () => {
    renderWith({ subscription_status: "active", plan_tier: "growth" });
    expect(await screen.findByText(/growth/i)).toBeInTheDocument();
    expect(await screen.findByText(/5,?000 \/ 25,?000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /manage billing/i })).toBeInTheDocument();
  });

  it("read-only (canceled) shows reactivate", async () => {
    renderWith({ subscription_status: "canceled" });
    expect(await screen.findByText(/read-only|reactivate/i)).toBeInTheDocument();
  });

  it("provisioning (null) shows Start subscription", async () => {
    renderWith({ subscription_status: null });
    expect(await screen.findByRole("button", { name: /start subscription/i })).toBeInTheDocument();
  });

  it("provisioning with no available plans shows an alert and does not redirect (Task 10 review)", async () => {
    vi.spyOn(billing, "getPublicPrices").mockResolvedValue([]);
    renderWith({ subscription_status: null });
    const user = userEvent.setup();
    const initialHref = window.location.href;

    await user.click(await screen.findByRole("button", { name: /start subscription/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no plans are available/i);
    expect(window.location.href).toBe(initialHref);
  });

  it("active plan action failure (createPortalLink rejects) shows an alert (Task 10 review)", async () => {
    vi.spyOn(billing, "createPortalLink").mockRejectedValue(new Error("portal link failed"));
    renderWith({ subscription_status: "active" });
    const user = userEvent.setup();
    const initialHref = window.location.href;

    await user.click(await screen.findByRole("button", { name: /manage billing/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(window.location.href).toBe(initialHref);
  });
});
