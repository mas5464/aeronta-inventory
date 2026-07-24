import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UploadPanel } from "@/features/feeds/UploadPanel";

/**
 * Mocked at file scope via hoisted state (mirrors Members.test.tsx) — default
 * is a planner, but tests override the role to exercise the viewer gate.
 */
const authState = vi.hoisted(() => ({
  role: "planner" as string,
  tenantSlug: "aeronta-demo",
  session: { user: { id: "u-planner" } },
}));

vi.mock("@/lib/auth/useAuth", () => ({
  useAuth: () => authState,
}));

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function makeFile(name: string, contents = "a,b,c\n1,2,3\n"): File {
  return new File([contents], name, { type: "text/csv" });
}

interface RouterOptions {
  onMint?: (files: string[]) => void;
  onPut?: (url: string) => void;
  onCreateIngest?: (body: { batch_id: string; files: Record<string, string> }) => void;
  /** Poll responses returned in order — the last one repeats once exhausted. */
  pollSequence?: Array<{ status: string; result?: unknown; errors?: unknown }>;
}

/** Fetch stub as a URL/method router (mirrors Members.test.tsx's `mockFetchRouter`). */
function mockFetchRouter(options: RouterOptions) {
  const pollSequence = options.pollSequence ?? [{ status: "queued", result: null, errors: null }];
  let pollCall = 0;

  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";

    if (url.endsWith("/uploads") && method === "POST") {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      options.onMint?.(body.files);
      const targets: Record<string, { url: string; path: string }> = {};
      for (const name of body.files as string[]) {
        targets[name] = { url: `https://signed.storage.test/${name}`, path: `tenant-uuid/batch-1/${name}` };
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ batch_id: "batch-1", targets }),
      });
    }

    if (url.startsWith("https://signed.storage.test/") && method === "PUT") {
      options.onPut?.(url);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }

    if (url.endsWith("/ingest") && method === "POST") {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      options.onCreateIngest?.(body);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ job_id: 42 }) });
    }

    if (/\/ingest\/42$/.test(url) && method === "GET") {
      const index = Math.min(pollCall, pollSequence.length - 1);
      pollCall += 1;
      const next = pollSequence[index];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(next) });
    }

    return Promise.reject(new Error(`Unhandled request: ${method} ${url}`));
  });
}

describe("UploadPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    authState.role = "planner";
  });

  it("renders the six canonical dropzones for a planner", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    expect(screen.getByLabelText(/parts file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/stock file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/demand history file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/locations file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/open orders file/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/vendors file/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run ingest/i })).toBeInTheDocument();
  });

  it("is hidden for a viewer (history only)", () => {
    authState.role = "viewer";
    vi.stubGlobal("fetch", mockFetchRouter({}));

    const { container } = renderWithClient(<UploadPanel pollIntervalMs={10} />);

    expect(screen.queryByLabelText(/parts file/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run ingest/i })).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("selecting required files + Run ingest mints→PUTs→creates then polls to a done summary", async () => {
    const onMint = vi.fn();
    const onPut = vi.fn();
    const onCreateIngest = vi.fn();
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({
        onMint,
        onPut,
        onCreateIngest,
        pollSequence: [
          { status: "queued", result: null, errors: null },
          {
            status: "done",
            result: { files: ["parts", "stock"], keys: 123, recommendations: 45, seeded_at: "2026-07-21T00:00:00Z" },
            errors: null,
          },
        ],
      }),
    );
    const user = userEvent.setup();

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    await user.upload(screen.getByLabelText(/parts file/i), makeFile("parts.csv"));
    await user.upload(screen.getByLabelText(/stock file/i), makeFile("stock.csv"));

    const runButton = screen.getByRole("button", { name: /run ingest/i });
    expect(runButton).not.toBeDisabled();
    await user.click(runButton);

    await waitFor(() => expect(onMint).toHaveBeenCalledWith(["parts", "stock"]));
    await waitFor(() => expect(onPut).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(onCreateIngest).toHaveBeenCalledWith({
        batch_id: "batch-1",
        files: { parts: "tenant-uuid/batch-1/parts", stock: "tenant-uuid/batch-1/stock" },
      }),
    );

    await waitFor(() => expect(screen.getByText(/123/)).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.getByText(/45/)).toBeInTheDocument();
    const workbenchLink = screen.getByRole("link", { name: /workbench/i });
    expect(workbenchLink).toHaveAttribute("href", expect.stringContaining("/workbench"));
  });

  it("a failed poll renders the grouped error table with row/column/message", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({
        pollSequence: [
          { status: "queued", result: null, errors: null },
          {
            status: "failed",
            result: null,
            errors: [
              { file: "stock", row: 3, column: "on_hand", message: "'abc' in 'on_hand' is not numeric" },
              { file: "parts", row: null, column: null, message: "missing required column: part_number" },
            ],
          },
        ],
      }),
    );
    const user = userEvent.setup();

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    await user.upload(screen.getByLabelText(/parts file/i), makeFile("parts.csv"));
    await user.upload(screen.getByLabelText(/stock file/i), makeFile("stock.csv"));
    await user.click(screen.getByRole("button", { name: /run ingest/i }));

    await waitFor(
      () => expect(screen.getByText(/'abc' in 'on_hand' is not numeric/)).toBeInTheDocument(),
      { timeout: 3000 },
    );

    const stockGroup = screen.getByTestId("ingest-error-group-stock");
    expect(within(stockGroup).getByText("3")).toBeInTheDocument();
    expect(within(stockGroup).getByText("on_hand")).toBeInTheDocument();

    const partsGroup = screen.getByTestId("ingest-error-group-parts");
    expect(within(partsGroup).getByText(/missing required column: part_number/)).toBeInTheDocument();
  });

  it("a failed poll with string error (whole-job exception) renders message text visibly", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({
        pollSequence: [
          { status: "queued", result: null, errors: null },
          {
            status: "failed",
            result: null,
            errors: ["StorageError: could not download parts.csv"],
          },
        ],
      }),
    );
    const user = userEvent.setup();

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    await user.upload(screen.getByLabelText(/parts file/i), makeFile("parts.csv"));
    await user.upload(screen.getByLabelText(/stock file/i), makeFile("stock.csv"));
    await user.click(screen.getByRole("button", { name: /run ingest/i }));

    await waitFor(
      () => expect(screen.getByText(/StorageError: could not download parts.csv/)).toBeInTheDocument(),
      { timeout: 3000 },
    );

    const ingestGroup = screen.getByTestId("ingest-error-group-ingest");
    expect(within(ingestGroup).getByText(/StorageError: could not download parts.csv/)).toBeInTheDocument();
  });

  it("an over-quota validation error shows an Upgrade your plan link to /billing", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({
        pollSequence: [
          { status: "queued", result: null, errors: null },
          {
            status: "failed",
            result: null,
            errors: [
              {
                file: "stock",
                row: null,
                column: null,
                message: "30000 keys exceeds your plan limit of 25000; contact support to raise your quota or reduce the upload",
              },
            ],
          },
        ],
      }),
    );
    const user = userEvent.setup();

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    await user.upload(screen.getByLabelText(/parts file/i), makeFile("parts.csv"));
    await user.upload(screen.getByLabelText(/stock file/i), makeFile("stock.csv"));
    await user.click(screen.getByRole("button", { name: /run ingest/i }));

    await waitFor(
      () => expect(screen.getByText(/exceeds your plan limit/)).toBeInTheDocument(),
      { timeout: 3000 },
    );

    const upgradeLink = screen.getByRole("link", { name: /upgrade your plan/i });
    expect(upgradeLink).toHaveAttribute("href", expect.stringContaining("/billing"));
  });

  it("a non-quota failure does not show the Upgrade your plan link", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({
        pollSequence: [
          { status: "queued", result: null, errors: null },
          {
            status: "failed",
            result: null,
            errors: [
              { file: "stock", row: 3, column: "on_hand", message: "'abc' in 'on_hand' is not numeric" },
            ],
          },
        ],
      }),
    );
    const user = userEvent.setup();

    renderWithClient(<UploadPanel pollIntervalMs={10} />);

    await user.upload(screen.getByLabelText(/parts file/i), makeFile("parts.csv"));
    await user.upload(screen.getByLabelText(/stock file/i), makeFile("stock.csv"));
    await user.click(screen.getByRole("button", { name: /run ingest/i }));

    await waitFor(
      () => expect(screen.getByText(/'abc' in 'on_hand' is not numeric/)).toBeInTheDocument(),
      { timeout: 3000 },
    );

    expect(screen.queryByRole("link", { name: /upgrade your plan/i })).not.toBeInTheDocument();
  });
});
