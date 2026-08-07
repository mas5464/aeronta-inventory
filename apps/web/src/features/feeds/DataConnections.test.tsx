import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { DataConnections } from "@/features/feeds/DataConnections";
import type { IngestHistoryItem } from "@/lib/api/ingest";
import type { FeedHealthRow, FeedsSummary } from "@/lib/api/types";

/**
 * `UploadPanel` (C3 Task 6, mounted inside `DataConnections`) calls
 * `useAuth()` — mocked at file scope via hoisted state (mirrors
 * Members.test.tsx) so the component tree renders without a real
 * AuthProvider. Role defaults to "planner" so the upload controls render;
 * none of these pre-existing tests assert on them.
 */
const authState = vi.hoisted(() => ({
  role: "planner" as string,
  tenantSlug: "aeronta-demo",
  session: { user: { id: "u-planner" } },
}));

vi.mock("@/lib/auth/useAuth", () => ({
  useAuth: () => authState,
}));

const ALL_13_FEED_IDS: FeedHealthRow["feed_id"][] = [
  "REQUISITIONS",
  "PURCHASE_ORDERS",
  "QUOTATIONS",
  "REPAIR_ORDERS",
  "INVENTORY",
  "SERIAL_TRACKING",
  "RELIABILITY",
  "FLEET_UTILIZATION",
  "MAINTENANCE_SCHEDULE",
  "VENDOR_MASTER",
  "INTERCHANGEABILITY",
  "CONTRACTS",
  "SHELF_LIFE",
];

const CONNECTED = new Set<FeedHealthRow["feed_id"]>([
  "INVENTORY",
  "PURCHASE_ORDERS",
  "VENDOR_MASTER",
  "INTERCHANGEABILITY",
]);
const PARTIAL = new Set<FeedHealthRow["feed_id"]>([
  "REQUISITIONS",
  "SHELF_LIFE",
  "FLEET_UTILIZATION",
]);

function statusFor(feedId: FeedHealthRow["feed_id"]): FeedHealthRow["status"] {
  if (CONNECTED.has(feedId)) return "connected";
  if (PARTIAL.has(feedId)) return "partial";
  return "not_connected";
}

// Realistic display names distinct from the raw FeedId (mirrors the real BFF —
// name is a human label, feed_id is the enum value rendered separately).
const FEED_DISPLAY_NAME: Record<FeedHealthRow["feed_id"], string> = {
  REQUISITIONS: "Requisitions / open demand",
  PURCHASE_ORDERS: "Purchase orders (on-order)",
  QUOTATIONS: "Quotations (RFQ / on hand)",
  REPAIR_ORDERS: "Repair orders (units in shop)",
  INVENTORY: "Current inventory / on-hand",
  SERIAL_TRACKING: "Serial / rotable tracking",
  RELIABILITY: "Reliability (MTBUR/MTBF/removals)",
  FLEET_UTILIZATION: "Fleet & utilization (FH/FC)",
  MAINTENANCE_SCHEDULE: "Maintenance schedule (checks)",
  VENDOR_MASTER: "Vendor master & lead times",
  INTERCHANGEABILITY: "Interchangeability / alternates / PMA",
  CONTRACTS: "Contracts (PBH / pooling / consignment)",
  SHELF_LIFE: "Shelf life / expiry",
};

const sampleFeeds: FeedsSummary = {
  health: { connected: 4, partial: 3, not_connected: 6, extract_date: "2026-04-01" },
  feeds: ALL_13_FEED_IDS.map((feedId) => ({
    feed_id: feedId,
    name: FEED_DISPLAY_NAME[feedId],
    status: statusFor(feedId),
    domains: statusFor(feedId) === "not_connected" ? [] : [`${feedId.toLowerCase()}_domain`],
    rows: null,
    last_sync: statusFor(feedId) === "not_connected" ? null : "2026-04-01",
    notes: `Notes for ${feedId}.`,
  })),
};

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * URL/method router (mirrors Members.test.tsx's `mockFetchRouter`): serves
 * `sampleFeeds` for the `useFeeds()` call the pre-existing tests below
 * exercise, plus an empty ingest history for `IngestHistory` (C3 Task 6,
 * now mounted inside `DataConnections`) so its query resolves cleanly
 * instead of falling through to the unhandled-request rejection.
 */
function mockFetchRouter(history: IngestHistoryItem[] = []) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";

    if (url.endsWith("/feeds") && method === "GET") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(sampleFeeds) });
    }

    if (url.endsWith("/ingest") && method === "GET") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(history) });
    }

    return Promise.reject(new Error(`Unhandled request: ${method} ${url}`));
  });
}

describe("DataConnections", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    authState.role = "planner";
  });

  it("shows a loading state, then the health strip with provenance", async () => {
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    expect(screen.getByRole("status", { name: "" })).toBeInTheDocument();

    // Card titles are headings — disambiguates from the same-text status badges
    // that also appear in the feed table below.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Connected" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: "Partial" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Not connected" })).toBeInTheDocument();

    // Health strip counts: 4 connected, 3 partial, 6 not_connected
    expect(screen.getAllByText("4").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("3").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("6").length).toBeGreaterThanOrEqual(1);

    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThanOrEqual(1);
  });

  it("renders all 13 feeds in the table with truthful statuses", async () => {
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    await waitFor(() => expect(screen.getByText("Source feeds (13)")).toBeInTheDocument());

    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    // 13 data rows + 1 header row
    expect(rows).toHaveLength(14);

    // Ground-truth spot checks: INVENTORY is connected, REPAIR_ORDERS is not_connected.
    const inventoryRow = within(table).getByText("INVENTORY").closest("tr")!;
    expect(within(inventoryRow).getByText("Connected")).toBeInTheDocument();

    const repairRow = within(table).getByText("REPAIR_ORDERS").closest("tr")!;
    expect(within(repairRow).getByText("Not connected")).toBeInTheDocument();
    expect(within(repairRow).getByText("none")).toBeInTheDocument();

    const fleetRow = within(table).getByText("FLEET_UTILIZATION").closest("tr")!;
    expect(within(fleetRow).getByText("Partial")).toBeInTheDocument();
  });

  it("filters the feed table by status", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    await waitFor(() => expect(screen.getByText("Source feeds (13)")).toBeInTheDocument());

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(14);

    await user.click(screen.getByRole("button", { name: "Connected" }));

    await waitFor(() => {
      const filteredTable = screen.getByRole("table");
      // 4 connected rows + 1 header row
      expect(within(filteredTable).getAllByRole("row")).toHaveLength(5);
    });
    const filteredTable = screen.getByRole("table");
    expect(within(filteredTable).getByText("INVENTORY")).toBeInTheDocument();
    expect(within(filteredTable).queryByText("REPAIR_ORDERS")).not.toBeInTheDocument();
  });

  it("renders the recommended-feeds-to-add panel from the not_connected feeds only", async () => {
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Recommended feeds to add" })).toBeInTheDocument(),
    );
    // The panel is an ordered list (<ol>) — its first <li> is the #1-ranked feed.
    // RELIABILITY is ranked #1 per PRD §10 ("the #1 recommended feed").
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(within(items[0]).getByText(FEED_DISPLAY_NAME.RELIABILITY)).toBeInTheDocument();

    // A connected feed must never appear in the recommendations panel.
    expect(within(list).queryByText(FEED_DISPLAY_NAME.INVENTORY)).not.toBeInTheDocument();
    // Only the 6 not_connected feeds render as recommendations.
    expect(items).toHaveLength(6);
  });

  it("renders the part stat-sheet lookup as a link-out search box", async () => {
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    await waitFor(() =>
      expect(screen.getByText("Part statistics reference browser")).toBeInTheDocument(),
    );
    // Scoped to the lookup form itself — UploadPanel's "Locations file" dropzone
    // label (C3 Task 6) also matches a bare /location/i, so a page-wide query
    // would be ambiguous.
    const lookupForm = screen.getByRole("form", { name: "Part statistics lookup" });
    expect(within(lookupForm).getByLabelText(/part number/i)).toBeInTheDocument();
    expect(within(lookupForm).getByLabelText(/location/i)).toBeInTheDocument();
    expect(within(lookupForm).getByRole("button", { name: /open part stat sheet/i })).toBeDisabled();
  });

  it("shows native repair-feed status and unavailable legacy coverage without fabricating zeros", async () => {
    vi.stubGlobal("fetch", mockFetchRouter());

    renderWithClient(<DataConnections />);

    const panel = await screen.findByTestId("repair-history-coverage");
    const nativeStatus = within(panel).getByLabelText("Native repair feed status");
    const selfServeCoverage = within(panel).getByLabelText(
      "Self-serve repair history coverage",
    );

    expect(within(nativeStatus).getByText("Not connected")).toBeInTheDocument();
    expect(within(nativeStatus).getByText("none")).toBeInTheDocument();
    expect(within(selfServeCoverage).getByText("Not reported")).toBeInTheDocument();
    expect(
      within(selfServeCoverage).getByText(/legacy results remain unavailable/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("repair-coverage-accepted")).toHaveTextContent("—");
    expect(screen.getByTestId("repair-coverage-proxy")).toHaveTextContent("—");
    expect(screen.getByTestId("repair-coverage-unavailable")).toHaveTextContent("—");
  });

  it("keeps repair history and exact coverage visible to a viewer while upload stays hidden", async () => {
    authState.role = "viewer";
    // The history endpoint is capped at 20 mixed jobs. Replay-equivalent
    // recompute results must therefore remain usable after the source upload
    // has fallen outside the returned window.
    const history: IngestHistoryItem[] = Array.from({ length: 20 }, (_, index) => ({
      id: 30 - index,
      kind: "recompute",
      status: "done",
      result: {
        files: ["parts", "repair_history", "stock"],
        keys: 10,
        recommendations: 3,
        seeded_at: "2026-07-22T00:00:00Z",
        repair_history: {
          accepted: 18,
          excluded: 2,
          quarantined: 1,
          parts_covered: 7,
          shops_covered: 3,
          observed: 5,
          pooled: 2,
          proxy: 4,
          unavailable: 6,
          proxy_definition: "order_creation_to_last_receipt",
        },
      },
      uploaded_by: null,
      created_at: `2026-07-${String(28 - index).padStart(2, "0")}T12:00:00Z`,
    }));
    vi.stubGlobal("fetch", mockFetchRouter(history));

    renderWithClient(<DataConnections />);

    const panel = await screen.findByTestId("repair-history-coverage");
    const selfServeCoverage = within(panel).getByLabelText(
      "Self-serve repair history coverage",
    );
    expect(within(selfServeCoverage).getByText("Reported")).toBeInTheDocument();
    expect(
      within(selfServeCoverage).getByText(/scheduled recompute #30/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("repair-coverage-accepted")).toHaveTextContent("18");
    expect(screen.getByTestId("repair-coverage-excluded")).toHaveTextContent("2");
    expect(screen.getByTestId("repair-coverage-quarantined")).toHaveTextContent("1");
    expect(screen.getByTestId("repair-coverage-parts")).toHaveTextContent("7");
    expect(screen.getByTestId("repair-coverage-shops")).toHaveTextContent("3");
    expect(screen.getByTestId("repair-coverage-observed")).toHaveTextContent("5");
    expect(screen.getByTestId("repair-coverage-pooled")).toHaveTextContent("2");
    expect(screen.getByTestId("repair-coverage-proxy")).toHaveTextContent("4");
    expect(screen.getByTestId("repair-coverage-unavailable")).toHaveTextContent("6");
    expect(
      within(selfServeCoverage).getByText("order_creation_to_last_receipt"),
    ).toBeInTheDocument();

    expect(
      screen.queryByRole("heading", { name: "Upload data" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Upload history" }),
    ).toBeInTheDocument();
  });

  it("shows the latest failed validation evidence without presenting it as completed coverage", async () => {
    const history: IngestHistoryItem[] = [
      {
        id: 91,
        kind: "ingest",
        status: "failed",
        result: {
          validation_summary: {
            validation_error_count: 4,
            repair_history: {
              accepted: 18,
              excluded: 2,
              quarantined: 4,
              parts_covered: 7,
              shops_covered: 3,
              observed: 5,
              pooled: 2,
              proxy: 4,
              unavailable: 6,
            },
          },
        },
        uploaded_by: "planner@example.test",
        created_at: "2026-07-28T12:00:00Z",
      },
    ];
    vi.stubGlobal("fetch", mockFetchRouter(history));

    renderWithClient(<DataConnections />);

    const panel = await screen.findByTestId("repair-history-coverage");
    const coverage = within(panel).getByLabelText(
      "Self-serve repair history coverage",
    );

    expect(within(coverage).getByText("Validation failed")).toBeInTheDocument();
    expect(within(coverage).getByText(/4 rejected row\/error findings/i)).toBeInTheDocument();
    expect(within(coverage).getByText(/no failed batch was seeded/i)).toBeInTheDocument();
    expect(screen.getByTestId("repair-coverage-accepted")).toHaveTextContent("18");
    expect(screen.getByTestId("repair-coverage-excluded")).toHaveTextContent("2");
    expect(screen.getByTestId("repair-coverage-quarantined")).toHaveTextContent("4");
    expect(within(coverage).getByText("—")).toBeInTheDocument();
    expect(within(coverage).queryByText("Reported")).not.toBeInTheDocument();
  });

  it("keeps omitted failed-batch repair evidence explicitly unavailable", async () => {
    const history: IngestHistoryItem[] = [
      {
        id: 92,
        kind: "ingest",
        status: "failed",
        result: {
          validation_summary: {
            validation_error_count: 1,
          },
        },
        uploaded_by: null,
        created_at: "2026-07-28T13:00:00Z",
      },
    ];
    vi.stubGlobal("fetch", mockFetchRouter(history));

    renderWithClient(<DataConnections />);

    const panel = await screen.findByTestId("repair-history-coverage");
    const coverage = within(panel).getByLabelText(
      "Self-serve repair history coverage",
    );

    expect(within(coverage).getByText("Validation failed")).toBeInTheDocument();
    expect(within(coverage).getByText(/1 rejected row\/error finding/i)).toBeInTheDocument();
    expect(screen.getByTestId("repair-coverage-accepted")).toHaveTextContent("—");
    expect(screen.getByTestId("repair-coverage-quarantined")).toHaveTextContent("—");
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

    renderWithClient(<DataConnections />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load feed health/i);
  });
});
