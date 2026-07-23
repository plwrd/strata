/**
 * The knowledge-health dialog: findings with remedies, honest about locks.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthDialog } from "../features/health/HealthDialog";
import type { HealthReport } from "../bridge/types";
import { installFakeBridge } from "./fakeBridge";

const REPORT: HealthReport = {
  items: [
    {
      key: "unprocessed",
      label: "Unprocessed captures",
      count: 3,
      note_ids: ["n1"],
      note_titles: ["Encryption Architecture"],
      recommendation:
        "Select them and run “Process into knowledge” in the Changes tab.",
    },
    {
      key: "stale",
      label: "Notes untouched for 90+ days",
      count: 0,
      note_ids: [],
      note_titles: [],
      recommendation: "",
    },
  ],
  duplicates: [],
  total_notes: 42,
  locked_layers: 2,
};

describe("HealthDialog", () => {
  it("shows findings with their remedy, and hides empty categories", async () => {
    installFakeBridge({ health: REPORT });
    render(<HealthDialog onClose={() => undefined} />);

    expect(await screen.findByText("Unprocessed captures")).toBeInTheDocument();
    expect(
      screen.getByText(/run “Process into knowledge”/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/untouched for 90/)).not.toBeInTheDocument();
  });

  it("is honest that locked layers are not included", async () => {
    installFakeBridge({ health: REPORT });
    render(<HealthDialog onClose={() => undefined} />);

    expect(
      await screen.findByText(/2 locked layer\(s\) not included/),
    ).toBeInTheDocument();
  });

  it("celebrates a healthy workspace instead of showing an empty table", async () => {
    installFakeBridge({
      health: { items: [], duplicates: [], total_notes: 10, locked_layers: 0 },
    });
    render(<HealthDialog onClose={() => undefined} />);

    expect(
      await screen.findByText(/Nothing needs attention/),
    ).toBeInTheDocument();
  });
});
