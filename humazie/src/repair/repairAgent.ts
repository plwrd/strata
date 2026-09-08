import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { execSync } from "node:child_process";
import type { HumazieConfig } from "../config.js";
import type { FixRecord, HumazieFlow, HumazieIssue } from "../types.js";
import { ensureDir, nowIso, repoRoot, writeJson } from "../util/paths.js";
import { isRepairSafe } from "./safety.js";
import { findSuspectedFiles, proposeMinimalFix } from "./rootCause.js";
import { verifyRepair } from "../verification/verify.js";

function run(cmd: string, cwd = repoRoot()): string {
  return execSync(cmd, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function shortSlug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
}

export async function attemptRepair(options: {
  config: HumazieConfig;
  issue: HumazieIssue;
  flow: HumazieFlow;
  patchesDir: string;
  rerunFlow: () => Promise<{ passed: boolean; details: string[] }>;
}): Promise<{ issue: HumazieIssue; fix: FixRecord }> {
  const { config, flow, patchesDir, rerunFlow } = options;
  let issue = { ...options.issue, status: "fixing" as const, updatedAt: nowIso() };

  const suspected = findSuspectedFiles(issue, flow);
  issue = { ...issue, suspectedFiles: suspected };

  const safety = isRepairSafe(config, issue, suspected);
  if (!safety.ok) {
    const fix: FixRecord = {
      issueId: issue.id,
      changedFiles: [],
      summary: safety.reason,
      testsAdded: [],
      verificationResults: [safety.reason],
      status: "manual_review_required",
      beforeScreenshots: issue.screenshots,
      afterScreenshots: [],
    };
    return {
      issue: {
        ...issue,
        status: "manual_review_required",
        proposedFix: safety.reason,
        updatedAt: nowIso(),
      },
      fix,
    };
  }

  const proposal = proposeMinimalFix(issue, suspected);
  if (!proposal) {
    return {
      issue: {
        ...issue,
        status: "manual_review_required",
        rootCause: "Could not determine a narrow automatic fix.",
        updatedAt: nowIso(),
      },
      fix: {
        issueId: issue.id,
        changedFiles: [],
        summary: "No automatic fix proposed",
        testsAdded: [],
        verificationResults: ["No proposal"],
        status: "manual_review_required",
        beforeScreenshots: issue.screenshots,
        afterScreenshots: [],
      },
    };
  }

  const branch = `humazie/fix-${flow.id.slice(0, 12)}-${shortSlug(issue.title)}`;
  const root = repoRoot();
  const backups = new Map<string, string>();

  try {
    try {
      run(`git checkout -b ${branch}`);
    } catch {
      // Branch may already exist in repeated local runs
      run(`git checkout ${branch}`);
    }

    for (const change of proposal.changes) {
      const abs = join(root, change.file);
      if (!existsSync(abs)) continue;
      backups.set(change.file, readFileSync(abs, "utf8"));
      const next = change.apply(backups.get(change.file)!);
      writeFileSync(abs, next, "utf8");
    }

    const patchPath = join(patchesDir, `${issue.id}.diff`);
    ensureDir(patchesDir);
    try {
      const diff = run("git diff");
      writeFileSync(patchPath, diff, "utf8");
    } catch {
      writeJson(patchPath.replace(/\.diff$/, ".json"), proposal);
    }

    const verification = await verifyRepair(config, {
      changedFiles: proposal.changes.map((c) => c.file),
      rerunFlow,
    });

    if (!verification.ok) {
      for (const [file, content] of backups) {
        writeFileSync(join(root, file), content, "utf8");
      }
      try {
        run("git checkout -- .");
        run("git checkout -");
      } catch {
        // best-effort rollback of branch switch
      }
      return {
        issue: {
          ...issue,
          status: "verification_failed",
          rootCause: proposal.rootCause,
          proposedFix: proposal.summary,
          changedFiles: proposal.changes.map((c) => c.file),
          verificationResults: verification.results,
          updatedAt: nowIso(),
        },
        fix: {
          issueId: issue.id,
          branch,
          changedFiles: proposal.changes.map((c) => c.file),
          summary: proposal.summary,
          testsAdded: [],
          verificationResults: verification.results,
          status: "reverted",
          beforeScreenshots: issue.screenshots,
          afterScreenshots: [],
          patchPath,
        },
      };
    }

    const relFiles = proposal.changes.map((c) => c.file);
    run(`git add ${relFiles.map((f) => `"${f}"`).join(" ")}`);
    const message = `fix(humazie): ${proposal.commitSubject}`;
    run(`git commit -m "${message.replace(/"/g, '\\"')}"`);
    const commit = run("git rev-parse --short HEAD").trim();

    return {
      issue: {
        ...issue,
        status: "fixed",
        rootCause: proposal.rootCause,
        proposedFix: proposal.summary,
        changedFiles: relFiles,
        verificationResults: verification.results,
        updatedAt: nowIso(),
      },
      fix: {
        issueId: issue.id,
        branch,
        commit,
        changedFiles: relFiles,
        summary: proposal.summary,
        testsAdded: proposal.testsAdded ?? [],
        verificationResults: verification.results,
        status: "fixed",
        beforeScreenshots: issue.screenshots,
        afterScreenshots: [],
        patchPath,
      },
    };
  } catch (error) {
    for (const [file, content] of backups) {
      writeFileSync(join(root, file), content, "utf8");
    }
    const message = error instanceof Error ? error.message : String(error);
    return {
      issue: {
        ...issue,
        status: "verification_failed",
        proposedFix: message,
        updatedAt: nowIso(),
      },
      fix: {
        issueId: issue.id,
        branch,
        changedFiles: [...backups.keys()].map((f) => relative(root, f)),
        summary: `Repair aborted: ${message}`,
        testsAdded: [],
        verificationResults: [message],
        status: "reverted",
        beforeScreenshots: issue.screenshots,
        afterScreenshots: [],
      },
    };
  }
}
