import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Portfolio } from "@/features/portfolio/Portfolio";

const state = vi.hoisted(() => ({
  enabled: false,
  canSubmit: false,
  role: "viewer",
}));

vi.mock("@/lib/auth/useAuth", () => ({
  useAuth: () => ({
    authEnabled: true,
    role: state.role,
    tenantSlug: "acme",
  }),
}));

vi.mock("@/lib/api/usePlanningRuns", () => ({
  usePlanningCapability: () => ({
    data: {
      enabled: state.enabled,
      can_read: state.enabled,
      can_submit: state.canSubmit,
      advisory_only: true,
      reason_code: state.enabled ? "insufficient_role" : "feature_disabled",
    },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  usePlanningRuns: () => ({
    data: [],
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useCreatePlanningRun: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
    reset: vi.fn(),
  }),
  usePlanningRun: () => ({
    data: undefined,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/features/replay/ShadowValidationPanel", () => ({
  ShadowValidationPanel: () => <div>Shadow validation boundary</div>,
}));

describe("Portfolio feature gate", () => {
  beforeEach(() => {
    state.enabled = false;
    state.canSubmit = false;
    state.role = "viewer";
  });

  it("fails closed for a tenant outside the default-off allowlist", () => {
    render(<Portfolio />);

    expect(
      screen.getByRole("heading", {
        name: /portfolio optimization is not enabled/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/default-off/i);
    expect(
      screen.queryByRole("button", { name: /submit advisory plan/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/shadow validation boundary/i),
    ).not.toBeInTheDocument();
  });

  it("keeps enabled viewers read-only", () => {
    state.enabled = true;
    render(<Portfolio />);

    expect(
      screen.getByRole("heading", { name: /read-only portfolio access/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/viewers may inspect run evidence/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /submit advisory plan/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/shadow validation boundary/i)).toBeInTheDocument();
  });
});
