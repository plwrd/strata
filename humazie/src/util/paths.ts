import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";

export function repoRoot(): string {
  // humazie/src/util/paths.ts -> repo root is ../../..
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "../../..");
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function makeRunId(prefix = "run"): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const salt = randomBytes(3).toString("hex");
  return `${prefix}-${stamp}-${salt}`;
}

export function makeId(prefix: string, seed: string): string {
  const hash = createHash("sha1").update(seed).digest("hex").slice(0, 10);
  return `${prefix}_${hash}`;
}

export function ensureDir(path: string): void {
  if (!existsSync(path)) mkdirSync(path, { recursive: true });
}

export function writeJson(path: string, data: unknown): void {
  ensureDir(dirname(path));
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

export function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

export function appendJsonl(path: string, row: unknown): void {
  ensureDir(dirname(path));
  appendFileSync(path, `${JSON.stringify(row)}\n`, "utf8");
}

export function gitCommit(cwd = repoRoot()): string {
  try {
    return execSync("git rev-parse --short HEAD", { cwd, encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}

export function redactSecrets(value: string): string {
  return value
    .replace(
      /(password|token|secret|api[_-]?key|authorization)\s*[:=]\s*["']?[^\s"']+/gi,
      "$1=[REDACTED]",
    )
    .replace(/Bearer\s+[A-Za-z0-9._\-]+/gi, "Bearer [REDACTED]");
}

export function uniqueTaggedValue(runId: string, label: string): string {
  return `${label} [${runId}]`;
}

export function resolveFromRoot(...parts: string[]): string {
  return join(repoRoot(), ...parts);
}
