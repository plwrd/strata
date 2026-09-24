import { z } from "zod";

export const SeveritySchema = z.enum(["critical", "high", "medium", "low"]);
export type Severity = z.infer<typeof SeveritySchema>;

export const IssueCategorySchema = z.enum([
  "functional",
  "navigation",
  "form_validation",
  "state_management",
  "api_integration",
  "rendering_hydration",
  "responsive",
  "accessibility",
  "performance",
  "usability",
  "test_infrastructure",
  "environment",
]);
export type IssueCategory = z.infer<typeof IssueCategorySchema>;

export const IssueStatusSchema = z.enum([
  "discovered",
  "reproduced",
  "fixing",
  "fixed",
  "verification_failed",
  "manual_review_required",
]);
export type IssueStatus = z.infer<typeof IssueStatusSchema>;

export const HumazieIssueSchema = z.object({
  id: z.string(),
  runId: z.string(),
  flowId: z.string(),
  title: z.string(),
  category: IssueCategorySchema,
  severity: SeveritySchema,
  confidence: z.number().min(0).max(1),
  status: IssueStatusSchema,
  route: z.string(),
  userImpact: z.string(),
  expectedBehavior: z.string(),
  actualBehavior: z.string(),
  reproductionSteps: z.array(z.string()),
  consoleErrors: z.array(z.string()),
  networkErrors: z.array(
    z.object({
      method: z.string(),
      url: z.string(),
      status: z.number().optional(),
    }),
  ),
  screenshots: z.array(z.string()),
  tracePath: z.string().optional(),
  suspectedFiles: z.array(z.string()),
  rootCause: z.string().optional(),
  proposedFix: z.string().optional(),
  changedFiles: z.array(z.string()).optional(),
  verificationResults: z.array(z.string()).optional(),
  autoRepairSafe: z.boolean().default(false),
  createdAt: z.string(),
  updatedAt: z.string(),
});
export type HumazieIssue = z.infer<typeof HumazieIssueSchema>;

export const FlowActionSchema = z.object({
  type: z.enum([
    "goto",
    "click",
    "fill",
    "press",
    "expect_visible",
    "expect_hidden",
    "expect_url",
    "expect_text",
    "wait_for",
    "screenshot",
    "axe",
    "custom",
  ]),
  description: z.string(),
  role: z.string().optional(),
  name: z.string().optional(),
  label: z.string().optional(),
  placeholder: z.string().optional(),
  text: z.string().optional(),
  testId: z.string().optional(),
  value: z.string().optional(),
  key: z.string().optional(),
  url: z.string().optional(),
  timeoutMs: z.number().optional(),
  exact: z.boolean().optional(),
  withinRole: z.string().optional(),
  withinName: z.string().optional(),
  title: z.string().optional(),
});
export type FlowAction = z.infer<typeof FlowActionSchema>;

export const HumazieFlowSchema = z.object({
  id: z.string(),
  name: z.string(),
  userGoal: z.string(),
  preconditions: z.array(z.string()),
  startingRoute: z.string(),
  testData: z.record(z.string()),
  actions: z.array(FlowActionSchema),
  expectedResults: z.array(z.string()),
  riskLevel: SeveritySchema,
  safeToExecute: z.boolean(),
  cleanup: z.array(z.string()),
  relatedRoutes: z.array(z.string()),
  relatedFiles: z.array(z.string()),
  category: z.string(),
});
export type HumazieFlow = z.infer<typeof HumazieFlowSchema>;

export const ProductNodeSchema = z.object({
  id: z.string(),
  kind: z.enum(["page", "dialog", "form", "section", "state"]),
  label: z.string(),
  route: z.string().optional(),
  sourceFiles: z.array(z.string()).default([]),
  accessibleName: z.string().optional(),
});
export type ProductNode = z.infer<typeof ProductNodeSchema>;

export const ProductEdgeSchema = z.object({
  id: z.string(),
  from: z.string(),
  to: z.string(),
  action: z.string(),
  label: z.string(),
});
export type ProductEdge = z.infer<typeof ProductEdgeSchema>;

export const ProductMapSchema = z.object({
  generatedAt: z.string(),
  nodes: z.array(ProductNodeSchema),
  edges: z.array(ProductEdgeSchema),
  modes: z.array(z.string()),
  dialogs: z.array(z.string()),
  forms: z.array(z.string()),
  sourceSummary: z.record(z.number()),
});
export type ProductMap = z.infer<typeof ProductMapSchema>;

export const ActionLogSchema = z.object({
  timestamp: z.string(),
  runId: z.string(),
  flowId: z.string(),
  url: z.string(),
  actionType: z.string(),
  target: z.string(),
  selectorStrategy: z.string(),
  inputValue: z.string().optional(),
  screenshotPath: z.string().optional(),
  consoleMessages: z.array(z.string()).default([]),
  networkFailures: z.array(z.string()).default([]),
  httpErrors: z.array(z.string()).default([]),
  expected: z.string().optional(),
  actual: z.string().optional(),
  status: z.enum(["pass", "fail", "skip", "info"]),
  durationMs: z.number(),
});
export type ActionLog = z.infer<typeof ActionLogSchema>;

export type FlowExecutionResult = {
  flowId: string;
  name: string;
  status: "passed" | "failed" | "skipped";
  durationMs: number;
  actions: ActionLog[];
  issues: HumazieIssue[];
  screenshots: string[];
  tracePath?: string;
  videoPath?: string;
  consoleErrors: string[];
  networkErrors: Array<{ method: string; url: string; status?: number }>;
};

export type FixRecord = {
  issueId: string;
  branch?: string;
  commit?: string;
  changedFiles: string[];
  summary: string;
  testsAdded: string[];
  verificationResults: string[];
  status: "fixed" | "reverted" | "manual_review_required";
  beforeScreenshots: string[];
  afterScreenshots: string[];
  patchPath?: string;
};

export type RunSummary = {
  runId: string;
  startedAt: string;
  finishedAt: string;
  gitCommit: string;
  environment: string;
  baseUrl: string;
  mobile: boolean;
  autoFix: boolean;
  routesReviewed: string[];
  totalFlows: number;
  passedFlows: number;
  failedFlows: number;
  issuesFound: number;
  issuesFixed: number;
  issuesManualReview: number;
  durationMs: number;
};
