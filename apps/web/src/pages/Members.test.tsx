import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Members } from "@/pages/Members";
import { DEFAULT_BFF_URL } from "@/lib/api/client";
import type { Member } from "@/lib/api/members";

/**
 * Mocked at file scope via hoisted state, so individual tests can mutate
 * the identity. Default is an owner, but tests can override the role.
 */
const authState = vi.hoisted(() => ({
  role: "owner" as string,
  tenantSlug: "aeronta-demo",
  session: { user: { id: "u-owner" } },
}));

vi.mock("@/lib/auth/useAuth", () => ({
  useAuth: () => authState,
}));

const initialMembers: Member[] = [
  { user_id: "u-owner", role: "owner", created_at: "2026-01-01T00:00:00Z", email: "owner@aeronta.test" },
  { user_id: "u-planner", role: "planner", created_at: "2026-02-01T00:00:00Z", email: "planner@aeronta.test" },
];

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

interface RouterOptions {
  members?: Member[];
  onInvite?: (body: { email: string; role: string }) => void;
  onUpdateRole?: (userId: string, role: string) => void;
  onRemove?: (userId: string) => void;
  /** When set, every DELETE .../members/{id} resolves with this status instead of 200. */
  removeStatus?: number;
}

/** Fetch stub as a URL/method router (mirrors Scenarios.test.tsx's `mockFetchRouter`
 * convention) — mutates its own `members` array in place so a GET issued AFTER a
 * write (via TanStack Query's invalidate-triggered refetch) reflects the write,
 * proving the refetch actually happened rather than just the write request firing. */
function mockFetchRouter(options: RouterOptions) {
  let members = options.members ?? [...initialMembers];

  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";

    if (url.includes("/members/invite") && method === "POST") {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      options.onInvite?.(body);
      const user_id = "u-new";
      members = [
        ...members,
        { user_id, role: body.role, created_at: "2026-07-21T00:00:00Z", email: body.email },
      ];
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ user_id, role: body.role }) });
    }

    const rowMatch = url.match(/\/members\/([^/]+)$/);
    if (rowMatch && method === "PATCH") {
      const userId = decodeURIComponent(rowMatch[1]);
      const body = init?.body ? JSON.parse(init.body as string) : {};
      options.onUpdateRole?.(userId, body.role);
      members = members.map((m) => (m.user_id === userId ? { ...m, role: body.role } : m));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ user_id: userId, role: body.role }),
      });
    }

    if (rowMatch && method === "DELETE") {
      const userId = decodeURIComponent(rowMatch[1]);
      options.onRemove?.(userId);
      if (options.removeStatus && options.removeStatus >= 300) {
        return Promise.resolve({
          ok: false,
          status: options.removeStatus,
          statusText: "Conflict",
          json: () => Promise.resolve({ detail: userId }),
        });
      }
      members = members.filter((m) => m.user_id !== userId);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ removed: userId }) });
    }

    if (url.endsWith("/members")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(members) });
    }

    return Promise.reject(new Error(`Unhandled request: ${method} ${url}`));
  });
}

describe("Members", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    // Reset auth state to the default (owner).
    authState.role = "owner";
    authState.session = { user: { id: "u-owner" } };
  });

  it("renders member rows from the stubbed GET", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithClient(<Members />);

    await waitFor(() => expect(screen.getByText("owner@aeronta.test")).toBeInTheDocument());
    expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument();

    const rows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(rows).toHaveLength(2);
    // Scoped via the role Badge's testid — each row's "change role" <select>
    // also contains an <option> with the same text (e.g. "Admin"), so a bare
    // getByText would ambiguously match both.
    expect(within(rows[0]).getByTestId("member-role-badge")).toHaveTextContent("Owner");
    expect(within(rows[1]).getByTestId("member-role-badge")).toHaveTextContent("Planner");
    expect(`${DEFAULT_BFF_URL}`).toBeTruthy();
  });

  it("invite form POSTs to .../members/invite and the list refetches with the new member", async () => {
    const onInvite = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onInvite }));
    const user = userEvent.setup();

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("owner@aeronta.test")).toBeInTheDocument());

    await user.type(screen.getByLabelText(/email/i), "new@aeronta.test");
    await user.selectOptions(screen.getByRole("combobox", { name: "Role" }), "admin");
    await user.click(screen.getByRole("button", { name: "Invite" }));

    expect(onInvite).toHaveBeenCalledWith({ email: "new@aeronta.test", role: "admin" });
    await waitFor(() => expect(screen.getByText("new@aeronta.test")).toBeInTheDocument());
    // The email field clears after a successful invite.
    expect(screen.getByLabelText(/email/i)).toHaveValue("");
  });

  it("remove: clicking Remove opens a confirm dialog, and confirming issues the DELETE", async () => {
    const onRemove = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onRemove }));
    const user = userEvent.setup();

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument());

    const plannerRow = screen.getByText("planner@aeronta.test").closest("tr")!;
    await user.click(within(plannerRow).getByRole("button", { name: "Remove" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/planner@aeronta.test/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Remove member" }));

    await waitFor(() => expect(onRemove).toHaveBeenCalledWith("u-planner"));
    await waitFor(() => expect(screen.queryByText("planner@aeronta.test")).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the confirm dialog on Escape without deleting (useFocusTrap, WCAG 2.1 AA)", async () => {
    const onRemove = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onRemove }));
    const user = userEvent.setup();

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument());

    const plannerRow = screen.getByText("planner@aeronta.test").closest("tr")!;
    const removeButton = within(plannerRow).getByRole("button", { name: "Remove" });
    await user.click(removeButton);

    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument();
  });

  it("a 409 on delete renders the last-owner message and keeps the dialog open", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({ removeStatus: 409 }));
    const user = userEvent.setup();

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument());

    const plannerRow = screen.getByText("planner@aeronta.test").closest("tr")!;
    await user.click(within(plannerRow).getByRole("button", { name: "Remove" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove member" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/cannot remove the last owner/i);
    // The row is still there — the failed removal didn't optimistically vanish.
    // Scoped to the row itself (not `screen`) — the still-open dialog also
    // renders the member's email, so a bare `screen.getByText` would be ambiguous.
    expect(within(plannerRow).getByText("planner@aeronta.test")).toBeInTheDocument();
  });

  it("changing a row's role select PATCHes .../members/{user_id}", async () => {
    const onUpdateRole = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onUpdateRole }));
    const user = userEvent.setup();

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument());

    const roleSelect = screen.getByRole("combobox", { name: "Role for planner@aeronta.test" });
    await user.selectOptions(roleSelect, "admin");

    await waitFor(() => expect(onUpdateRole).toHaveBeenCalledWith("u-planner", "admin"));
    // Refetch reflects the change — the row's badge now reads Admin. Scoped via
    // the badge's testid — the row's own select also has an "Admin" <option>.
    const plannerRow = screen.getByText("planner@aeronta.test").closest("tr")!;
    await waitFor(() =>
      expect(within(plannerRow).getByTestId("member-role-badge")).toHaveTextContent("Admin"),
    );
  });

  it("disables the self row's Remove button and role select", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("owner@aeronta.test")).toBeInTheDocument());

    const ownRow = screen.getByText("owner@aeronta.test").closest("tr")!;
    expect(within(ownRow).getByRole("button", { name: "Remove" })).toBeDisabled();
    expect(within(ownRow).getByRole("combobox")).toBeDisabled();
  });

  it("disables Remove button and role select for non-owner callers on owner rows", async () => {
    // Simulate a non-owner admin caller.
    authState.role = "admin";
    authState.session = { user: { id: "u-admin" } };
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithClient(<Members />);
    await waitFor(() => expect(screen.getByText("owner@aeronta.test")).toBeInTheDocument());

    const ownerRow = screen.getByText("owner@aeronta.test").closest("tr")!;
    // Remove button is disabled (roleLocked=true because caller is admin, not owner).
    expect(within(ownerRow).getByRole("button", { name: "Remove" })).toBeDisabled();
    // Role select is also disabled.
    expect(within(ownerRow).getByRole("combobox")).toBeDisabled();
  });
});
