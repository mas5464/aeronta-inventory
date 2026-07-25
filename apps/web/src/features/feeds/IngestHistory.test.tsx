import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IngestHistory } from "@/features/feeds/IngestHistory";
import type { IngestHistoryItem } from "@/lib/api/ingest";

const sampleHistory: IngestHistoryItem[] = [
  {
    id: 7,
    kind: "ingest",
    status: "done",
    result: { files: ["parts", "stock"], keys: 321, recommendations: 12, seeded_at: "2026-07-21T09:00:00Z" },
    uploaded_by: "u-planner",
    created_at: "2026-07-21T09:05:00Z",
  },
  {
    id: 6,
    kind: "ingest",
    status: "failed",
    result: null,
    uploaded_by: "u-admin",
    created_at: "2026-07-20T14:30:00Z",
  },
];

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("IngestHistory", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders when/uploaded_by/status badge/key count for each job", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleHistory) }),
    );

    renderWithClient(<IngestHistory />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(rows).toHaveLength(2);

    expect(within(rows[0]).getByText("u-planner")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Done")).toBeInTheDocument();
    expect(within(rows[0]).getByText("321")).toBeInTheDocument();

    expect(within(rows[1]).getByText("u-admin")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Failed")).toBeInTheDocument();
    // No result on a failed job — key count falls back to the em dash.
    expect(within(rows[1]).getByText("—")).toBeInTheDocument();
  });

  it("shows an empty-state message when there are no uploads yet", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) }));

    renderWithClient(<IngestHistory />);

    await waitFor(() => expect(screen.getByText("No uploads yet.")).toBeInTheDocument());
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders an error state when the BFF call fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.resolve({}),
      }),
    );

    renderWithClient(<IngestHistory />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load ingest history/i);
  });

  it("labels scheduled recomputes distinctly from uploads", async () => {
    const mixedHistory: IngestHistoryItem[] = [
      {
        id: 9,
        kind: "recompute",
        status: "done",
        result: {
          files: ["parts", "stock"],
          keys: 58899,
          recommendations: 4200,
          seeded_at: "2026-07-24T03:00:00Z",
        },
        uploaded_by: null,
        created_at: "2026-07-24T03:00:00Z",
      },
      {
        id: 8,
        kind: "ingest",
        status: "done",
        result: { files: ["parts", "stock"], keys: 321, recommendations: 12, seeded_at: "2026-07-21T09:00:00Z" },
        uploaded_by: "u-planner",
        created_at: "2026-07-21T09:05:00Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mixedHistory) }),
    );

    renderWithClient(<IngestHistory />);

    expect(await screen.findByText(/scheduled recompute/i)).toBeInTheDocument();
    expect(screen.getByText(/^upload$/i)).toBeInTheDocument();
    // The recompute row still shows its own key count like any other done run.
    expect(screen.getByText("58899")).toBeInTheDocument();
  });

  it("shows a superseded recompute as an uneventful outcome, not an error", async () => {
    const supersededHistory: IngestHistoryItem[] = [
      {
        id: 10,
        kind: "recompute",
        status: "done",
        result: {
          outcome: "superseded",
          reason: "tenant t1: a newer completed ingest (job 4) landed after this recompute resolved job 3; skipped to avoid overwriting it",
        },
        uploaded_by: null,
        created_at: "2026-07-24T03:00:00Z",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(supersededHistory) }),
    );

    renderWithClient(<IngestHistory />);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByText(/scheduled recompute/i)).toBeInTheDocument();
    expect(screen.getByText("Superseded")).toBeInTheDocument();
    // Never rendered as a failure, and never leaks the missing `.keys` field as text.
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });
});
