import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useAuth } from "@/lib/auth/useAuth";
import { activeTenant, ApiError } from "@/lib/api/client";
import {
  getMembers,
  inviteMember,
  removeMember,
  updateMemberRole,
  type InviteRole,
  type Member,
} from "@/lib/api/members";
import { useFocusTrap } from "@/lib/useFocusTrap";

const INVITE_ROLE_OPTIONS: { value: InviteRole; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "planner", label: "Planner" },
  { value: "viewer", label: "Viewer" },
];

/** Options for the per-row "change role" select — a superset of the invite
 * roles, since an owner caller is allowed to grant/revoke `owner` itself
 * (see members_routes.py's `_require_owner` gate on owner rows/targets). */
const ROW_ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "owner", label: "Owner" },
  ...INVITE_ROLE_OPTIONS,
];

const ROLE_BADGE_VARIANT: Record<string, "brand" | "warn" | "default"> = {
  owner: "brand",
  admin: "warn",
};

function roleLabel(role: string): string {
  return role.length === 0 ? role : role.charAt(0).toUpperCase() + role.slice(1);
}

function formatCreatedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

/**
 * Maps a members-mutation error to display text. A 409 is the backend's
 * last-owner invariant (pg/members.py's `_guard_last_owner`) — its raw
 * `detail` is just the target user_id (`str(LastOwnerError(user_id))`), not
 * prose, so this renders a fixed friendly message instead of the raw detail.
 */
function memberActionErrorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError && error.status === 409) return "cannot remove the last owner";
  return error instanceof Error ? error.message : "Something went wrong";
}

/** Invite has its own 409 meaning ("user already a member") — distinct from
 * the remove/role-change 409 (last-owner), so it gets its own mapper. */
function inviteErrorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError && error.status === 409) return "This person is already a member.";
  return error instanceof Error ? error.message : "Something went wrong";
}

interface MemberRemoveConfirmDialogProps {
  member: Member;
  onCancel: () => void;
  onConfirm: () => void;
  isSubmitting?: boolean;
  resultError?: string | null;
}

/**
 * Inline remove-confirmation — structurally mirrors
 * `RollbackConfirmDialog` (Part Drill-Down): a `role="dialog"` affordance
 * with a dependency-free focus trap (`useFocusTrap`), Cancel/Confirm
 * buttons, and an inline result-error slot for a failed mutation (WCAG 2.1
 * AA: traps focus while open, Escape cancels and restores focus).
 */
function MemberRemoveConfirmDialog({
  member,
  onCancel,
  onConfirm,
  isSubmitting,
  resultError,
}: MemberRemoveConfirmDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, onCancel);
  const label = member.email ?? member.user_id;

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Remove ${label}`}
      className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3"
    >
      <p className="text-sm text-ink">
        Remove <span className="font-medium">{label}</span> from this tenant? They will immediately
        lose access.
      </p>
      {resultError && (
        <p role="alert" className="text-xs text-bad">
          {resultError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button variant="default" size="sm" onClick={onConfirm} disabled={isSubmitting}>
          Remove member
        </Button>
      </div>
    </div>
  );
}

/**
 * Members management (C2 Task 7) — GET/POST/PATCH/DELETE
 * /v1/tenants/{tenant}/members* (services/agent-spine/.../bff/members_routes.py).
 * Route-level gates on the BFF (backed by Postgres RLS) are the real
 * enforcement boundary; the role check below is a UX nicety that skips
 * firing a request that would just 403, mirroring the nav-gating in App.tsx.
 */
export function Members() {
  const { role, tenantSlug, session } = useAuth();
  const tenant = tenantSlug ?? activeTenant();
  const selfId = session?.user?.id ?? null;
  const queryClient = useQueryClient();
  const canManage = role === "admin" || role === "owner";
  const queryKey = ["members", tenant] as const;

  const { data, isPending, isError, error, refetch } = useQuery<Member[]>({
    queryKey,
    queryFn: () => getMembers(tenant),
    enabled: canManage,
  });

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<InviteRole>("planner");
  const [confirmingRemoveId, setConfirmingRemoveId] = useState<string | null>(null);

  const inviteMutation = useMutation({
    mutationFn: () => inviteMember(tenant, inviteEmail.trim(), inviteRole),
    onSuccess: () => {
      setInviteEmail("");
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const updateRoleMutation = useMutation({
    mutationFn: ({ userId, role: newRole }: { userId: string; role: string }) =>
      updateMemberRole(tenant, userId, newRole),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey }),
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => removeMember(tenant, userId),
    onSuccess: () => {
      setConfirmingRemoveId(null);
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  if (!canManage) {
    return (
      <div className="p-6">
        <p className="text-sm text-ink-2">Members management requires admin or owner access.</p>
      </div>
    );
  }

  if (isPending) {
    return <QueryLoading label="Loading members…" />;
  }

  if (isError) {
    return <QueryError label="Failed to load members" error={error} onRetry={() => refetch()} />;
  }

  const members = data ?? [];
  const removingMember = members.find((m) => m.user_id === confirmingRemoveId) ?? null;

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">Members</h1>
        <p className="text-sm text-ink-2">Manage who has access to this tenant.</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Invite a member</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              inviteMutation.mutate();
            }}
            className="flex flex-wrap items-end gap-2"
          >
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Email
              <input
                type="email"
                required
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                className="h-8 w-64 rounded-control border border-line bg-panel px-2 text-sm text-ink"
                placeholder="name@company.com"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Role
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as InviteRole)}
                className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
              >
                {INVITE_ROLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <Button
              type="submit"
              size="sm"
              disabled={inviteMutation.isPending || inviteEmail.trim() === ""}
            >
              Invite
            </Button>
            {inviteErrorText(inviteMutation.error) && (
              <p role="alert" className="text-xs text-bad">
                {inviteErrorText(inviteMutation.error)}
              </p>
            )}
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Members ({members.length})</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {members.length === 0 ? (
            <p className="text-sm text-ink-2">No members yet.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Tenant members</caption>
              <thead>
                <tr className="text-ink-2">
                  <th scope="col" className="pb-2 pr-4 font-medium">
                    Email
                  </th>
                  <th scope="col" className="pb-2 pr-4 font-medium">
                    Role
                  </th>
                  <th scope="col" className="pb-2 pr-4 font-medium">
                    Added
                  </th>
                  <th scope="col" className="pb-2 pr-4 font-medium">
                    Change role
                  </th>
                  <th scope="col" className="pb-2 font-medium">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {members.map((m) => {
                  const label = m.email ?? m.user_id;
                  const isSelf = selfId !== null && m.user_id === selfId;
                  const roleLocked = m.role === "owner" && role !== "owner";
                  const selectDisabled = isSelf || roleLocked || updateRoleMutation.isPending;
                  return (
                    <tr key={m.user_id} className="border-t border-line">
                      <td className="py-2 pr-4">{label}</td>
                      <td className="py-2 pr-4">
                        <Badge
                          data-testid="member-role-badge"
                          variant={ROLE_BADGE_VARIANT[m.role] ?? "default"}
                        >
                          {roleLabel(m.role)}
                        </Badge>
                      </td>
                      <td className="py-2 pr-4">{formatCreatedAt(m.created_at)}</td>
                      <td className="py-2 pr-4">
                        <select
                          aria-label={`Role for ${label}`}
                          value={m.role}
                          disabled={selectDisabled}
                          onChange={(e) =>
                            updateRoleMutation.mutate({ userId: m.user_id, role: e.target.value })
                          }
                          className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink disabled:opacity-50"
                        >
                          {ROW_ROLE_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={isSelf || roleLocked}
                          onClick={() => {
                            removeMutation.reset();
                            setConfirmingRemoveId(m.user_id);
                          }}
                        >
                          Remove
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {updateRoleMutation.isError && (
            <p role="alert" className="text-xs text-bad">
              {memberActionErrorText(updateRoleMutation.error)}
            </p>
          )}
        </CardContent>
      </Card>

      {removingMember && (
        <MemberRemoveConfirmDialog
          member={removingMember}
          isSubmitting={removeMutation.isPending}
          resultError={memberActionErrorText(removeMutation.error)}
          onCancel={() => {
            removeMutation.reset();
            setConfirmingRemoveId(null);
          }}
          onConfirm={() => removeMutation.mutate(removingMember.user_id)}
        />
      )}
    </div>
  );
}
