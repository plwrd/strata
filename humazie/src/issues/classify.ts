import type { HumazieFlow } from "../types.js";
import type { IssueCategory, Severity } from "../types.js";

export type Classification = {
  category: IssueCategory;
  severity: Severity;
  confidence: number;
  userImpact: string;
  autoRepairSafe: boolean;
};

export function classifyFromFailure(
  title: string,
  actual: string,
  flow: HumazieFlow,
): Classification {
  const hay = `${title} ${actual} ${flow.category}`.toLowerCase();

  if (hay.includes("axe") || hay.includes("accessible") || hay.includes("aria")) {
    return {
      category: "accessibility",
      severity: "high",
      confidence: 0.8,
      userImpact: "Assistive technology users may be blocked or confused.",
      autoRepairSafe: true,
    };
  }
  if (hay.includes("console")) {
    return {
      category: "functional",
      severity: "medium",
      confidence: 0.7,
      userImpact: "Unexpected client errors during normal use.",
      autoRepairSafe: false,
    };
  }
  if (hay.includes("network") || hay.includes("4xx") || hay.includes("5xx")) {
    return {
      category: "api_integration",
      severity: "high",
      confidence: 0.75,
      userImpact: "Actions may silently fail after a request error.",
      autoRepairSafe: false,
    };
  }
  if (hay.includes("overflow") || hay.includes("mobile") || hay.includes("viewport")) {
    return {
      category: "responsive",
      severity: "medium",
      confidence: 0.7,
      userImpact: "Layout is hard to use on smaller screens.",
      autoRepairSafe: true,
    };
  }
  if (flow.category === "form_validation" || hay.includes("disabled") || hay.includes("validation")) {
    return {
      category: "form_validation",
      severity: "medium",
      confidence: 0.85,
      userImpact: "Users can submit invalid data or cannot tell why submit is blocked.",
      autoRepairSafe: true,
    };
  }
  if (flow.category === "navigation" || hay.includes("navigate") || hay.includes("dialog")) {
    return {
      category: "navigation",
      severity: "high",
      confidence: 0.75,
      userImpact: "Users cannot reach or dismiss a primary surface.",
      autoRepairSafe: true,
    };
  }
  if (hay.includes("loading") || hay.includes("stuck")) {
    return {
      category: "state_management",
      severity: "high",
      confidence: 0.7,
      userImpact: "UI appears stuck with no way forward.",
      autoRepairSafe: false,
    };
  }

  return {
    category: "functional",
    severity: flow.riskLevel,
    confidence: 0.6,
    userImpact: "A primary user journey does not produce the expected visible result.",
    autoRepairSafe: false,
  };
}
