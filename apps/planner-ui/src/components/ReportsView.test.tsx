import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportsView } from "./ReportsView";
import { FakePlannerClient } from "../api/client";
import { SAMPLE_SEED } from "../api/sample";

function renderReports(client: FakePlannerClient) {
  return render(
    <MemoryRouter>
      <ReportsView client={client} tenant="acme" />
    </MemoryRouter>,
  );
}

describe("ReportsView", () => {
  it("renders the projected hero tiles and the applied/shadowed split", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    expect(await screen.findByText(/total projected/i)).toBeInTheDocument();
    expect(screen.getByText(/changes applied/i)).toBeInTheDocument();
    expect(screen.getByText(/changes shadowed/i)).toBeInTheDocument();
    expect(screen.getAllByText(/projected/i).length).toBeGreaterThan(1);
  });

  it("links to the printable HTML and the PDF", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    const open = await screen.findByRole("link", { name: /open printable report/i });
    expect(open).toHaveAttribute("href", expect.stringContaining("/reports/bvr.html"));
    const pdf = screen.getByRole("link", { name: /download pdf/i });
    expect(pdf).toHaveAttribute("href", expect.stringContaining("/reports/bvr.pdf"));
  });

  it("shows governance numbers from the report", async () => {
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    expect(await screen.findByText(/approval rate/i)).toBeInTheDocument();
  });

  it("handles a failed fetch without throwing", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED);
    client.getBvr = async () => {
      throw new Error("boom");
    };
    renderReports(client);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load/i);
  });

  it("surfaces the PDF-501 error inline when the PDF extra is unavailable", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 501,
      json: async () => ({ detail: "pdf extra not installed" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    renderReports(new FakePlannerClient(SAMPLE_SEED));
    const pdf = await screen.findByRole("link", { name: /download pdf/i });
    await userEvent.click(pdf);
    expect(await screen.findByRole("alert")).toHaveTextContent(/pdf unavailable/i);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
