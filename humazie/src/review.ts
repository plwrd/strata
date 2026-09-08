import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { applyVisualPace, type HumazieConfig, type VisualPace } from "./config.js";
import { discoverProduct, enrichMapFromDom } from "./discovery/productDiscovery.js";
import { generateFlows } from "./flows/generateFlows.js";
import { BrowserExecutionAgent } from "./browser/executor.js";
import { writeRunArtifact } from "./logging/runLogger.js";
import { renderMarkdownReport } from "./report/markdownReport.js";
import { attemptRepair } from "./repair/repairAgent.js";
import type {
  FixRecord,
  FlowExecutionResult,
  HumazieFlow,
  HumazieIssue,
  ProductMap,
  RunSummary,
} from "./types.js";
import { gitCommit, makeRunId, nowIso, writeJson } from "./util/paths.js";
import { runsDir } from "./util/loadConfig.js";
import { ensureAppServer } from "./util/server.js";
import { persistRun } from "./db/history.js";
import { chromium } from "playwright";

export type ReviewOptions = {
  config: HumazieConfig;
  routeFilter?: string;
  mobile?: boolean;
  autoFix?: boolean;
  visual?: boolean;
  pace?: VisualPace;
  flowIds?: string[];
  runId?: string;
};

export type ReviewResult = {
  summary: RunSummary;
  productMap: ProductMap;
  flows: HumazieFlow[];
  results: FlowExecutionResult[];
  issues: HumazieIssue[];
  fixes: FixRecord[];
  reportPath: string;
};

async function runtimeEnrich(config: HumazieConfig, map: ProductMap): Promise<ProductMap> {
  try {
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.goto(config.baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.getByText("STRATA").first().waitFor({ timeout: 15_000 });
    const observed = await page.evaluate(() => {
      const text = (el: Element | null) => el?.textContent?.trim() ?? "";
      return {
        headings: Array.from(document.querySelectorAll("h1,h2,h3"))
          .map((h) => text(h))
          .filter(Boolean),
        buttons: Array.from(document.querySelectorAll("button"))
          .map((b) => text(b) || b.getAttribute("aria-label") || "")
          .filter(Boolean)
          .slice(0, 40),
        links: Array.from(document.querySelectorAll("a"))
          .map((a) => text(a))
          .filter(Boolean),
      };
    });
    await browser.close();
    return enrichMapFromDom(map, observed);
  } catch {
    return map;
  }
}

export async function runDiscover(config: HumazieConfig): Promise<ProductMap> {
  const server = await ensureAppServer(config);
  try {
    const map = await runtimeEnrich(config, discoverProduct(config));
    const outDir = join(runsDir(config), "latest-discovery");
    writeJson(join(outDir, "product-map.json"), map);
    return map;
  } finally {
    await server.stop();
  }
}

export async function runReview(options: ReviewOptions): Promise<ReviewResult> {
  const { config } = options;
  const runId = options.runId ?? makeRunId();
  const mobile = options.mobile ?? false;
  const autoFix = options.autoFix ?? config.autoRepair.enabled;
  const visual = options.visual ?? config.visual.enabled;
  const pacedVisual = applyVisualPace(config.visual, options.pace);
  const startedAt = nowIso();
  const startedMs = Date.now();

  const server = await ensureAppServer(config);
  const agent = new BrowserExecutionAgent(
    { ...config, visual: pacedVisual },
    runId,
    {
      mobile,
      visualOverride: { ...pacedVisual, enabled: visual, headed: visual },
    },
  );
  await agent.start();
  if (visual) {
    console.log(
      `[humazie] Visual pace=${pacedVisual.pace} (type ${pacedVisual.typeDelayMs}ms, pause ${pacedVisual.pauseAfterActionMs}ms)`,
    );
  }

  let productMap = discoverProduct(config);
  productMap = await runtimeEnrich(config, productMap);

  let flows = generateFlows(productMap, config, {
    routeFilter: options.routeFilter,
    mobile,
    runId,
  });
  if (options.flowIds?.length) {
    flows = flows.filter((f) => options.flowIds!.includes(f.id));
  }

  writeRunArtifact(agent.paths, "product-map.json", productMap);
  writeRunArtifact(agent.paths, "flows.json", flows);

  const results: FlowExecutionResult[] = [];
  const issues: HumazieIssue[] = [];
  const fixes: FixRecord[] = [];

  try {
    for (const flow of flows) {
      console.log(`[humazie] running flow: ${flow.name} (${flow.id})`);
      const result = await agent.executeFlow(flow);
      console.log(`[humazie] ${flow.id} -> ${result.status} (${result.durationMs}ms)`);
      results.push(result);
      issues.push(...result.issues);

      if (autoFix) {
        for (const issue of result.issues) {
          if (issue.confidence < 0.75 || !issue.autoRepairSafe) {
            issue.status = "manual_review_required";
            continue;
          }
          const { issue: updated, fix } = await attemptRepair({
            config: { ...config, autoRepair: { ...config.autoRepair, enabled: autoFix } },
            issue,
            flow,
            patchesDir: agent.paths.patches,
            rerunFlow: async () => {
              const rerun = await agent.executeFlow(flow);
              return {
                passed: rerun.status === "passed",
                details: [`rerun ${flow.id}: ${rerun.status}`],
              };
            },
          });
          const idx = issues.findIndex((i) => i.id === issue.id);
          if (idx >= 0) issues[idx] = updated;
          else issues.push(updated);
          fixes.push(fix);
        }
      }
    }
  } finally {
    await agent.stop();
    await server.stop();
  }

  const passedFlows = results.filter((r) => r.status === "passed").length;
  const failedFlows = results.filter((r) => r.status === "failed").length;
  const summary: RunSummary = {
    runId,
    startedAt,
    finishedAt: nowIso(),
    gitCommit: gitCommit(),
    environment: "humazie-harness",
    baseUrl: config.baseUrl,
    mobile,
    autoFix,
    routesReviewed: [...new Set(flows.flatMap((f) => f.relatedRoutes))],
    totalFlows: flows.length,
    passedFlows,
    failedFlows,
    issuesFound: issues.length,
    issuesFixed: issues.filter((i) => i.status === "fixed").length,
    issuesManualReview: issues.filter((i) => i.status === "manual_review_required").length,
    durationMs: Date.now() - startedMs,
  };

  const untestedReasons = [
    "Remote AI provider calls are excluded (unsafeActions).",
    "Encryption key wipe / recovery flows require manual fixtures.",
    "Qt WebEngine desktop shell e2e remains covered by pytest, not Humazie.",
    ...(options.routeFilter
      ? [`Route filter ${options.routeFilter} limited the generated set.`]
      : []),
  ];

  const report = renderMarkdownReport({
    summary,
    flows,
    results,
    issues,
    fixes,
    productMap,
    untestedReasons,
  });
  const reportPath = join(agent.paths.root, "summary.md");
  writeFileSync(reportPath, report, "utf8");

  writeRunArtifact(agent.paths, "run.json", summary);
  writeRunArtifact(agent.paths, "issues.json", issues);
  writeRunArtifact(agent.paths, "fixes.json", fixes);
  writeRunArtifact(agent.paths, "results.json", results);

  await persistRun({
    summary,
    productMap,
    flows,
    issues,
    fixes,
  });

  return { summary, productMap, flows, results, issues, fixes, reportPath };
}

export async function rerunFlows(
  options: ReviewOptions & { flowId: string },
): Promise<ReviewResult> {
  return runReview({
    ...options,
    flowIds: [options.flowId],
    runId: makeRunId("rerun"),
  });
}

export async function writeLatestReport(config: HumazieConfig): Promise<string> {
  const { readdirSync, statSync, readFileSync, existsSync } = await import("node:fs");
  const dir = runsDir(config);
  if (!existsSync(dir)) throw new Error(`No runs directory at ${dir}`);
  const runs = readdirSync(dir)
    .map((name) => ({ name, path: join(dir, name) }))
    .filter((e) => {
      try {
        return statSync(e.path).isDirectory() && e.name.startsWith("run-");
      } catch {
        return false;
      }
    })
    .sort((a, b) => statSync(b.path).mtimeMs - statSync(a.path).mtimeMs);
  const latest = runs[0];
  if (!latest) throw new Error("No review runs found.");
  const summaryPath = join(latest.path, "summary.md");
  if (!existsSync(summaryPath)) throw new Error(`Missing summary at ${summaryPath}`);
  return readFileSync(summaryPath, "utf8");
}
