import type { HumazieConfig } from "../config.js";
import type { HumazieIssue } from "../types.js";

export function isRepairSafe(
  config: HumazieConfig,
  issue: HumazieIssue,
  files: string[],
): { ok: boolean; reason: string } {
  if (!config.autoRepair.enabled) {
    return { ok: false, reason: "Automatic repair is disabled in config." };
  }
  if (issue.confidence < 0.75) {
    return { ok: false, reason: `Confidence ${issue.confidence} is below 0.75.` };
  }
  if (!issue.autoRepairSafe) {
    return { ok: false, reason: "Issue marked unsafe for automatic repair." };
  }
  if (files.length === 0) {
    return { ok: false, reason: "No suspected files to edit." };
  }
  if (files.length > config.autoRepair.maxFilesChanged) {
    return {
      ok: false,
      reason: `Would touch ${files.length} files (limit ${config.autoRepair.maxFilesChanged}).`,
    };
  }

  const blocked = config.autoRepair.requireManualReviewCategories;
  for (const file of files) {
    const lower = file.toLowerCase();
    if (
      lower.includes("auth") ||
      lower.includes("crypto") ||
      lower.includes("migration") ||
      lower.includes("packaging") ||
      lower.includes(".github")
    ) {
      return { ok: false, reason: `File ${file} requires manual review.` };
    }
    for (const cat of blocked) {
      if (lower.includes(cat.toLowerCase())) {
        return { ok: false, reason: `Category/file matches manual-review rule: ${cat}` };
      }
    }
  }

  return { ok: true, reason: "Within safety limits." };
}

export function countChangedLines(before: string, after: string): number {
  const a = before.split("\n");
  const b = after.split("\n");
  let changes = 0;
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    if (a[i] !== b[i]) changes += 1;
  }
  return changes;
}
