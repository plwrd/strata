/**
 * Human-like review heuristics applied after or during flow execution.
 */

export type Observation = {
  description: string;
  severity: "critical" | "high" | "medium" | "low";
  category: string;
};

export function detectNoVisibleFeedback(options: {
  actionLabel: string;
  urlBefore: string;
  urlAfter: string;
  domFingerprintBefore: string;
  domFingerprintAfter: string;
  loadingStuckMs?: number;
}): Observation | null {
  const unchanged =
    options.urlBefore === options.urlAfter &&
    options.domFingerprintBefore === options.domFingerprintAfter;
  if (unchanged && /click|submit|save|capture/i.test(options.actionLabel)) {
    return {
      description: `Action "${options.actionLabel}" produced no visible URL or DOM change.`,
      severity: "high",
      category: "usability",
    };
  }
  if ((options.loadingStuckMs ?? 0) > 15_000) {
    return {
      description: "Loading indicator remained visible longer than 15s.",
      severity: "high",
      category: "state_management",
    };
  }
  return null;
}

export function detectTechnicalErrorLeak(message: string): Observation | null {
  if (/stack trace|TypeError|at\s+\w+\s+\(|ECONNREFUSED|prisma\./i.test(message)) {
    return {
      description: "Error message exposes technical implementation details to the user.",
      severity: "medium",
      category: "usability",
    };
  }
  return null;
}

export function detectEmptyStateWithoutAction(options: {
  isEmpty: boolean;
  hasCta: boolean;
  surface: string;
}): Observation | null {
  if (options.isEmpty && !options.hasCta) {
    return {
      description: `Empty ${options.surface} provides no next action.`,
      severity: "medium",
      category: "usability",
    };
  }
  return null;
}
