import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SubscriptionBanner } from "./SubscriptionBanner";
import * as billing from "@/lib/api/billing";

function renderWith(status: billing.BillingSummary["subscription_status"], extra = {}) {
  vi.spyOn(billing, "getBilling").mockResolvedValue({
    plan_tier: "growth",
    subscription_status: status,
    key_quota: 25000,
    keys_used: 1,
    current_period_end: null,
    trial_ends_at: "2099-01-01T00:00:00Z",
    ...extra,
  } as billing.BillingSummary);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SubscriptionBanner tenant="acme" />
    </QueryClientProvider>,
  );
}

describe("SubscriptionBanner", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("active renders nothing", async () => {
    const { container } = renderWith("active");
    await new Promise((r) => setTimeout(r, 0));
    expect(container.textContent).toBe("");
  });

  it("trialing shows a countdown with the trial_ends_at date and a billing link", async () => {
    renderWith("trialing", { trial_ends_at: "2099-03-15T00:00:00Z" });
    const expected = new Date("2099-03-15T00:00:00Z").toLocaleDateString();
    expect(await screen.findByText(new RegExp(`trial.*${expected}`, "i"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage billing/i })).toHaveAttribute("href", "#/billing");
  });

  it("past_due prompts to update card", async () => {
    renderWith("past_due");
    expect(await screen.findByText(/update.*card|payment/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage billing/i })).toHaveAttribute("href", "#/billing");
  });

  it("canceled prompts to reactivate", async () => {
    renderWith("canceled");
    expect(await screen.findByText(/reactivate|read-only/i)).toBeInTheDocument();
  });

  it("unpaid (read-only) prompts to reactivate", async () => {
    renderWith("unpaid");
    expect(await screen.findByText(/reactivate|read-only/i)).toBeInTheDocument();
  });

  it("paused (read-only) prompts to reactivate", async () => {
    renderWith("paused");
    expect(await screen.findByText(/reactivate|read-only/i)).toBeInTheDocument();
  });

  it("null status (never subscribed) prompts to finish subscribing", async () => {
    renderWith(null);
    expect(await screen.findByText(/finish subscribing/i)).toBeInTheDocument();
  });

  it("incomplete status prompts to finish subscribing", async () => {
    renderWith("incomplete");
    expect(await screen.findByText(/finish subscribing/i)).toBeInTheDocument();
  });

  it("renders nothing while loading (query never resolves)", async () => {
    vi.spyOn(billing, "getBilling").mockReturnValue(new Promise(() => {}));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <SubscriptionBanner tenant="acme" />
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing on query error", async () => {
    vi.spyOn(billing, "getBilling").mockRejectedValue(new Error("network down"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <SubscriptionBanner tenant="acme" />
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(container).toBeEmptyDOMElement();
  });
});
