import { describe, expect, it } from "vitest";
import { isRepairSafe, countChangedLines } from "../src/repair/safety.js";
import type { HumazieConfig } from "../src/config.js";
import type { HumazieIssue } from "../src/types.js";

const baseConfig = {
  autoRepair: {
    enabled: true,
    maxFilesChanged: 2,
    maxLinesChanged: 40,
    requireManualReviewCategories: ["payment"],
  },
} as HumazieConfig;

function issue(partial: Partial<HumazieIssue> = {}): HumazieIssue {
  return {
    id: "issue_1",
    runId: "run_1",
    flowId: "flow_1",
    title: "t",
    category: "responsive",
    severity: "medium",
    confidence: 0.9,
    status: "reproduced",
    route: "/",
    userImpact: "u",
    expectedBehavior: "e",
    actualBehavior: "a",
    reproductionSteps: [],
    consoleErrors: [],
    networkErrors: [],
    screenshots: [],
    suspectedFiles: [],
    autoRepairSafe: true,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...partial,
  };
}

describe("repair safety", () => {
  it("blocks low confidence", () => {
    const result = isRepairSafe(baseConfig, issue({ confidence: 0.2 }), [
      "frontend/src/app/shell.css",
    ]);
    expect(result.ok).toBe(false);
  });

  it("blocks auth-related files", () => {
    const result = isRepairSafe(baseConfig, issue(), ["app/crypto/keys.py"]);
    expect(result.ok).toBe(false);
  });

  it("allows narrow frontend css fixes", () => {
    const result = isRepairSafe(baseConfig, issue(), [
      "frontend/src/app/shell.css",
    ]);
    expect(result.ok).toBe(true);
  });

  it("counts changed lines", () => {
    expect(countChangedLines("a\nb\n", "a\nc\n")).toBe(1);
  });
});
