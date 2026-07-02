import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";

/**
 * PRD §6.7 — "Part-statistics reference browser: every derived metric per part with
 * its source and confidence." v1 treatment: a search box that navigates straight to
 * the existing Part Drill-Down view (Slice S2), which already renders every derived
 * metric with its source/confidence via the provenance invariant — this does NOT
 * build a second, parallel part browser. See the module docstring in
 * DataConnections.tsx for the full disclosure.
 */
export function PartStatSheetLookup() {
  const navigate = useNavigate();
  const [pn, setPn] = useState("");
  const [location, setLocation] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedPn = pn.trim();
    const trimmedLocation = location.trim();
    if (!trimmedPn || !trimmedLocation) return;
    navigate(`/parts/${encodeURIComponent(trimmedPn)}/${encodeURIComponent(trimmedLocation)}`);
  }

  return (
    <form className="flex flex-wrap items-end gap-3" onSubmit={handleSubmit} aria-label="Part statistics lookup">
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Part number
        <input
          type="text"
          value={pn}
          onChange={(event) => setPn(event.target.value)}
          placeholder="e.g. 3214-116-401"
          className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-ink"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Location
        <input
          type="text"
          value={location}
          onChange={(event) => setLocation(event.target.value)}
          placeholder="e.g. YYZ"
          className="rounded-md border border-line bg-panel px-3 py-1.5 text-sm text-ink"
        />
      </label>
      <Button type="submit" size="sm" disabled={!pn.trim() || !location.trim()}>
        Open part stat sheet
      </Button>
      <p className="w-full text-xs text-ink-3">
        Opens the existing Part Drill-Down view — v1 does not build a second, parallel
        part-statistics browser.
      </p>
    </form>
  );
}
