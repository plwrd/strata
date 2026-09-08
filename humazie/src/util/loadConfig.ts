import { pathToFileURL } from "node:url";
import { resolveFromRoot, repoRoot } from "./paths.js";
import { validateConfig, type HumazieConfig } from "../config.js";

export async function loadConfig(configPath?: string): Promise<HumazieConfig> {
  const path = configPath
    ? resolveFromRoot(configPath)
    : resolveFromRoot("humazie.config.ts");
  const mod = await import(pathToFileURL(path).href);
  const raw = (mod.default ?? mod.config ?? mod) as unknown;
  return validateConfig(raw);
}

export function runsDir(config: HumazieConfig): string {
  return resolveFromRoot(config.logging.runsDir);
}

export { repoRoot };
