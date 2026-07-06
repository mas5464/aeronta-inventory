import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { formatPolicyValues } from "@/features/part/writebackView";
import type { HistoryEntry } from "@/lib/api/types";

export interface RollbackConfirmDialogProps {
  entry: HistoryEntry;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
  isSubmitting?: boolean;
  resultError?: string | null;
}

export function RollbackConfirmDialog({ entry, onCancel, onConfirm, isSubmitting, resultError }: RollbackConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, onCancel);

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Roll back ${entry.pn} / ${entry.location}`}
      className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3"
    >
      <p className="text-sm text-ink">
        Reverting <span className="font-medium">v{entry.version}</span> — this restores the prior value.
      </p>
      <div className="text-xs text-ink-2">
        <div>From: {entry.new_values ? formatPolicyValues(entry.new_values) : "—"}</div>
        <div>To: {entry.old_values ? formatPolicyValues(entry.old_values) : "—"}</div>
      </div>
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Reason
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
          placeholder="Why are you rolling this back?"
        />
      </label>
      {resultError && <p role="alert" className="text-xs text-bad">{resultError}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={isSubmitting}>Cancel</Button>
        <Button
          variant="default"
          size="sm"
          onClick={() => onConfirm(reason)}
          disabled={isSubmitting || reason.trim() === ""}
        >
          Confirm rollback
        </Button>
      </div>
    </div>
  );
}
