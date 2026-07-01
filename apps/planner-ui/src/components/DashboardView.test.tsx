import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DashboardView } from "./DashboardView";
import { FakePlannerClient } from "../api/client";
import { SAMPLE_SEED } from "../api/sample";

function renderDashboard(client: FakePlannerClient) {
  return render(
    <MemoryRouter>
      <DashboardView client={client} tenant="acme" />
    </MemoryRouter>,
  );
}

describe("DashboardView", () => {
  it("renders portfolio KPIs and top shortages", async () => {
    renderDashboard(new FakePlannerClient(SAMPLE_SEED));
    expect(await screen.findByText(/parts/i)).toBeInTheDocument();
    expect(screen.getAllByText(/on hand/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/HYD-PUMP-001/).length).toBeGreaterThan(0);
  });

  it("renders an empty-friendly state before the fetch resolves", async () => {
    renderDashboard(new FakePlannerClient(SAMPLE_SEED));
    expect(screen.getByRole("status")).toBeInTheDocument();
    await screen.findByText(/parts/i); // let the fetch settle so no state update leaks into the next test
  });

  it("handles a failed fetch without throwing", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED);
    client.getDashboard = async () => {
      throw new Error("boom");
    };
    renderDashboard(client);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load/i);
  });
});
