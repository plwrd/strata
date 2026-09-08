import { describe, expect, it } from "vitest";
import { redactSecrets } from "../src/util/paths.js";
import { renderMarkdownReport } from "../src/report/markdownReport.js";
import type { ProductMap, RunSummary } from "../src/types.js";

describe("log serializers", () => {
  it("redacts secrets", () => {
    expect(redactSecrets("Authorization: Bearer abc.def.ghi")).toContain("[REDACTED]");
    expect(redactSecrets('password=super-secret')).toContain("[REDACTED]");
  });

  it("renders a markdown report", () => {
    const summary: RunSummary = {
      runId: "run-demo",
      startedAt: new Date().toISOString(),
      finishedAt: new Date().toISOString(),
      gitCommit: "abc123",
      environment: "test",
      baseUrl: "http://127.0.0.1:5173/humazie.html",
      mobile: false,
      autoFix: false,
      routesReviewed: ["#explore"],
      totalFlows: 1,
      passedFlows: 1,
      failedFlows: 0,
      issuesFound: 0,
      issuesFixed: 0,
      issuesManualReview: 0,
      durationMs: 1200,
    };
    const map: ProductMap = {
      generatedAt: summary.startedAt,
      nodes: [],
      edges: [],
      modes: ["explore"],
      dialogs: [],
      forms: [],
      sourceSummary: {},
    };
    const md = renderMarkdownReport({
      summary,
      flows: [],
      results: [],
      issues: [],
      fixes: [],
      productMap: map,
      untestedReasons: ["Remote AI excluded"],
    });
    expect(md).toContain("# Humazie Review Report");
    expect(md).toContain("run-demo");
    expect(md).toContain("Remaining Risks");
  });
});
