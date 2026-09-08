import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

export type RunIndex = {
  id: string;
  path: string;
  mtimeMs: number;
  summary?: Record<string, unknown>;
  hasReport: boolean;
};

function repoRootFromDashboard(): string {
  // humazie/dashboard -> repo root
  return join(process.cwd(), "..", "..");
}

export function runsRoot(): string {
  return join(repoRootFromDashboard(), ".humazie", "runs");
}

export function listRuns(): RunIndex[] {
  const root = runsRoot();
  if (!existsSync(root)) return [];
  const runs: RunIndex[] = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    const st = statSync(path);
    if (!st.isDirectory()) continue;
    const summaryPath = join(path, "run.json");
    const reportPath = join(path, "summary.md");
    let summary: Record<string, unknown> | undefined;
    if (existsSync(summaryPath)) {
      summary = JSON.parse(readFileSync(summaryPath, "utf8")) as Record<
        string,
        unknown
      >;
    }
    runs.push({
      id: name,
      path,
      mtimeMs: st.mtimeMs,
      summary,
      hasReport: existsSync(reportPath),
    });
  }
  return runs.sort((a, b) => b.mtimeMs - a.mtimeMs);
}

export function readRunFile(runId: string, file: string): string | null {
  const path = join(runsRoot(), runId, file);
  if (!existsSync(path)) return null;
  return readFileSync(path, "utf8");
}

export function readRunJson<T>(runId: string, file: string): T | null {
  const raw = readRunFile(runId, file);
  if (!raw) return null;
  return JSON.parse(raw) as T;
}
