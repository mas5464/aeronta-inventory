import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { DataConnections } from "@/features/feeds/DataConnections";
import type { FeedHealthRow, FeedsSummary } from "@/lib/api/types";

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

describe("DataConnections", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state, then the health strip with provenance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleFeeds) }),
    );

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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleFeeds) }),
    );

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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleFeeds) }),
    );

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
    expect(screen.getByText("INVENTORY")).toBeInTheDocument();
    expect(screen.queryByText("REPAIR_ORDERS")).not.toBeInTheDocument();
  });

  it("renders the recommended-feeds-to-add panel from the not_connected feeds only", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleFeeds) }),
    );

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
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleFeeds) }),
    );

    renderWithClient(<DataConnections />);

    await waitFor(() =>
      expect(screen.getByText("Part statistics reference browser")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/part number/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/location/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open part stat sheet/i })).toBeDisabled();
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
