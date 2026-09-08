import { join } from "node:path";
import type { ActionLog, HumazieFlow, HumazieIssue } from "../types.js";
import { appendJsonl, ensureDir, nowIso, redactSecrets, writeJson } from "../util/paths.js";
import { classifyFromFailure } from "../issues/classify.js";

export type RunPaths = {
  root: string;
  screenshots: string;
  videos: string;
  traces: string;
  console: string;
  network: string;
  patches: string;
  actionsJsonl: string;
};

export function createRunPaths(runsDir: string, runId: string): RunPaths {
  const root = join(runsDir, runId);
  const paths: RunPaths = {
    root,
    screenshots: join(root, "screenshots"),
    videos: join(root, "videos"),
    traces: join(root, "traces"),
    console: join(root, "console"),
    network: join(root, "network"),
    patches: join(root, "patches"),
    actionsJsonl: join(root, "actions.jsonl"),
  };
  for (const dir of [
    paths.root,
    paths.screenshots,
    paths.videos,
    paths.traces,
    paths.console,
    paths.network,
    paths.patches,
  ]) {
    ensureDir(dir);
  }
  return paths;
}

export function logAction(paths: RunPaths, action: ActionLog): void {
  appendJsonl(paths.actionsJsonl, action);
}

export function writeRunArtifact(
  paths: RunPaths,
  name: string,
  data: unknown,
): void {
  writeJson(join(paths.root, name), data);
}

export function issueSkeleton(
  runId: string,
  flow: HumazieFlow,
  title: string,
  actual: string,
  extras: Partial<HumazieIssue> = {},
): HumazieIssue {
  const classified = classifyFromFailure(title, actual, flow);
  const stamp = nowIso();
  return {
    id: `issue_${flow.id}_${Date.now().toString(36)}`,
    runId,
    flowId: flow.id,
    title,
    category: classified.category,
    severity: classified.severity,
    confidence: classified.confidence,
    status: "discovered",
    route: flow.startingRoute,
    userImpact: classified.userImpact,
    expectedBehavior: flow.expectedResults.join("; "),
    actualBehavior: redactSecrets(actual),
    reproductionSteps: flow.actions.map((a) => a.description),
    consoleErrors: [],
    networkErrors: [],
    screenshots: [],
    suspectedFiles: flow.relatedFiles,
    autoRepairSafe: classified.autoRepairSafe,
    createdAt: stamp,
    updatedAt: stamp,
    ...extras,
  };
}
