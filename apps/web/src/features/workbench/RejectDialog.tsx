import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { RejectReason } from "@/lib/api/types";

const REASON_OPTIONS: { value: RejectReason; label: string }[] = [
  { value: "wrong_for_fleet", label: "Wrong for fleet" },
  { value: "wrong_essentiality", label: "Wrong essentiality" },
  { value: "bad_lead_time", label: "Bad lead time" },
  { value: "planner_override", label: "Planner override" },
  { value: "other", label: "Other" },
];

export interface RejectDialogProps {
  recommendationId: string;
  onCancel: () => void;
  onConfirm: (reason: RejectReason, detail: string) => void;
  isSubmitting?: boolean;
}

/**
 * Inline dismiss-with-reason affordance (Dismiss = reject on the BFF, which
 * requires a `RejectReason`). Rendered inline in the worklist row per
 * DESIGN-SYSTEM.md §5 ("accept/adjust/override inline — never navigate away").
 */
export function RejectDialog({ recommendationId, onCancel, onConfirm, isSubmitting }: RejectDialogProps) {
  const [reason, setReason] = useState<RejectReason>("other");
  const [detail, setDetail] = useState("");

  return (
    <div
      role="dialog"
      aria-label={`Dismiss recommendation ${recommendationId}`}
      className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3"
    >
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Reason
        <select
          value={reason}
          onChange={(e) => setReason(e.target.value as RejectReason)}
          className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
        >
          {REASON_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Detail (optional)
        <input
          type="text"
          value={detail}
          onChange={(e) => setDetail(e.target.value)}
          className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
          placeholder="Add context…"
        />
      </label>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button
          variant="default"
          size="sm"
          onClick={() => onConfirm(reason, detail)}
          disabled={isSubmitting}
        >
          Confirm dismiss
        </Button>
      </div>
    </div>
  );
}
