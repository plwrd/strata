import { execSync } from "node:child_process";
import type { HumazieConfig } from "../config.js";
import { countChangedLines } from "../repair/safety.js";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repoRoot } from "../util/paths.js";

function runChecked(cmd: string): { ok: boolean; output: string } {
  try {
    const output = execSync(cmd, {
      cwd: repoRoot(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { ok: true, output };
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message?: string };
    return {
      ok: false,
      output: err.stderr || err.stdout || err.message || String(error),
    };
  }
}

export async function verifyRepair(
  config: HumazieConfig,
  options: {
    changedFiles: string[];
    beforeContents?: Map<string, string>;
    rerunFlow: () => Promise<{ passed: boolean; details: string[] }>;
  },
): Promise<{ ok: boolean; results: string[] }> {
  const results: string[] = [];
  const root = repoRoot();

  if (options.beforeContents) {
    let totalLines = 0;
    for (const [file, before] of options.beforeContents) {
      const after = readFileSync(join(root, file), "utf8");
      totalLines += countChangedLines(before, after);
    }
    if (totalLines > config.autoRepair.maxLinesChanged) {
      results.push(
        `Changed ${totalLines} lines exceeds limit ${config.autoRepair.maxLinesChanged}`,
      );
      return { ok: false, results };
    }
    results.push(`Line delta within limit (${totalLines})`);
  }

  if (config.commands.format) {
    const fmt = runChecked(config.commands.format);
    results.push(fmt.ok ? "format: ok" : `format: fail — ${fmt.output.slice(0, 200)}`);
    if (!fmt.ok) return { ok: false, results };
  }

  const lint = runChecked(config.commands.lint);
  results.push(lint.ok ? "lint: ok" : `lint: fail — ${lint.output.slice(0, 200)}`);
  if (!lint.ok) return { ok: false, results };

  const types = runChecked(config.commands.typecheck);
  results.push(types.ok ? "typecheck: ok" : `typecheck: fail — ${types.output.slice(0, 200)}`);
  if (!types.ok) return { ok: false, results };

  const unit = runChecked(config.commands.test);
  results.push(unit.ok ? "unit: ok" : `unit: fail — ${unit.output.slice(0, 200)}`);
  if (!unit.ok) return { ok: false, results };

  const flow = await options.rerunFlow();
  results.push(...flow.details);
  if (!flow.passed) {
    results.push("affected flow: failed");
    return { ok: false, results };
  }
  results.push("affected flow: passed");
  return { ok: true, results };
}
