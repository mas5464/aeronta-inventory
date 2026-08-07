import { describe, expect, it } from "vitest";

import { CANONICAL_FILE_NAMES, canonicalTemplateCsv } from "@/lib/api/ingest";

describe("canonical ingest contract", () => {
  it("mirrors all backend upload domains in canonical order", () => {
    expect(CANONICAL_FILE_NAMES).toEqual([
      "parts",
      "stock",
      "demand_history",
      "demand_window",
      "locations",
      "open_orders",
      "requisitions",
      "vendors",
      "repair_history",
    ]);
  });

  it("includes the closed demand window and scheduled-demand templates", () => {
    expect(canonicalTemplateCsv("demand_history")).toBe(
      "part_number,location_code,period,quantity,transaction_type,observation_start,observation_end\n",
    );
    expect(canonicalTemplateCsv("demand_window")).toBe(
      "observation_start,observation_end\n",
    );
    expect(canonicalTemplateCsv("requisitions")).toBe(
      "requisition_id,part_number,location_code,quantity,need_by,alt_source_location\n",
    );
  });

  it("keeps procurement and repair-order identity fields in canonical open-orders order", () => {
    expect(canonicalTemplateCsv("open_orders")).toBe(
      "part_number,location_code,quantity,expected_date,order_type,order_id,order_line_id,vendor_code,shop_code,opened_at,status,serial_number\n",
    );
  });

  it("keeps required then optional repair-history columns in the public template order", () => {
    expect(canonicalTemplateCsv("repair_history")).toBe(
      "repair_order_id,repair_line_id,part_number,quantity,started_at,completed_at,status,shop_code,vendor_code,location_code,outcome,serial_number\n",
    );
  });
});
