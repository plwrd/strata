import { z } from "zod";

const ViewportSchema = z.object({
  width: z.number().int().positive(),
  height: z.number().int().positive(),
});

const PersonaSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  description: z.string().optional(),
});

export const HumazieConfigSchema = z.object({
  baseUrl: z.string().url(),
  startCommand: z.string().min(1),
  startTimeoutMs: z.number().int().positive().default(60_000),
  reuseExistingServer: z.boolean().default(true),
  auth: z
    .object({
      strategy: z.enum(["none", "storage-state", "programmatic"]).default("none"),
      storageStatePath: z.string().optional(),
      loginUrl: z.string().optional(),
    })
    .default({ strategy: "none" }),
  personas: z.array(PersonaSchema).default([]),
  allowedRoutes: z.array(z.string()).default([]),
  excludedRoutes: z.array(z.string()).default([]),
  safeActions: z.array(z.string()).default([]),
  unsafeActions: z.array(z.string()).default([]),
  maxFlows: z.number().int().positive().default(20),
  browsers: z.array(z.enum(["chromium", "firefox", "webkit"])).default(["chromium"]),
  mobileViewport: ViewportSchema,
  desktopViewport: ViewportSchema,
  autoRepair: z.object({
    enabled: z.boolean().default(false),
    maxFilesChanged: z.number().int().positive().default(4),
    maxLinesChanged: z.number().int().positive().default(120),
    requireManualReviewCategories: z.array(z.string()).default([]),
  }),
  screenshots: z.object({
    enabled: z.boolean().default(true),
    fullPage: z.boolean().default(false),
  }),
  video: z.object({
    enabled: z.boolean().default(true),
    mode: z.enum(["off", "on", "retain-on-failure"]).default("retain-on-failure"),
  }),
  trace: z.object({
    enabled: z.boolean().default(true),
    mode: z.enum(["off", "on", "retain-on-failure"]).default("retain-on-failure"),
  }),
  accessibility: z.object({
    enabled: z.boolean().default(true),
    failOn: z.array(z.enum(["critical", "serious", "moderate", "minor"])).default([
      "critical",
      "serious",
    ]),
  }),
  cleanup: z.object({
    enabled: z.boolean().default(true),
    tagPrefix: z.string().default("humazie-run-"),
  }),
  commands: z.object({
    lint: z.string(),
    typecheck: z.string(),
    test: z.string(),
    build: z.string(),
    format: z.string().optional(),
  }),
  discovery: z.object({
    frontendSrc: z.string(),
    featureRoots: z.array(z.string()),
    existingTestsGlob: z.string(),
  }),
  logging: z.object({
    runsDir: z.string().default(".humazie/runs"),
  }),
  visual: z
    .object({
      enabled: z.boolean().default(true),
      headed: z.boolean().default(true),
      /** demo = easy to follow, balanced = default watch, brisk = long suites */
      pace: z.enum(["demo", "balanced", "brisk"]).default("balanced"),
      slowMoMs: z.number().int().nonnegative().default(180),
      highlightMs: z.number().int().nonnegative().default(380),
      pauseAfterActionMs: z.number().int().nonnegative().default(420),
      pauseAfterExpectMs: z.number().int().nonnegative().default(180),
      typeDelayMs: z.number().int().nonnegative().default(70),
      narrate: z.boolean().default(true),
      cursor: z.boolean().default(true),
    })
    .default({
      enabled: true,
      headed: true,
      pace: "balanced",
      slowMoMs: 180,
      highlightMs: 380,
      pauseAfterActionMs: 420,
      pauseAfterExpectMs: 180,
      typeDelayMs: 70,
      narrate: true,
      cursor: true,
    }),
});

export type HumazieConfig = z.infer<typeof HumazieConfigSchema>;
export type HumazieConfigInput = z.input<typeof HumazieConfigSchema>;

export type VisualPace = "demo" | "balanced" | "brisk";

export const VISUAL_PACE_PRESETS: Record<
  VisualPace,
  Pick<
    HumazieConfig["visual"],
    "slowMoMs" | "highlightMs" | "pauseAfterActionMs" | "pauseAfterExpectMs" | "typeDelayMs"
  >
> = {
  demo: {
    slowMoMs: 280,
    highlightMs: 520,
    pauseAfterActionMs: 650,
    pauseAfterExpectMs: 280,
    typeDelayMs: 90,
  },
  balanced: {
    slowMoMs: 180,
    highlightMs: 380,
    pauseAfterActionMs: 420,
    pauseAfterExpectMs: 180,
    typeDelayMs: 70,
  },
  brisk: {
    slowMoMs: 80,
    highlightMs: 220,
    pauseAfterActionMs: 220,
    pauseAfterExpectMs: 80,
    typeDelayMs: 35,
  },
};

export function applyVisualPace(
  visual: HumazieConfig["visual"],
  pace?: VisualPace,
): HumazieConfig["visual"] {
  const chosen = pace ?? visual.pace;
  return {
    ...visual,
    pace: chosen,
    ...VISUAL_PACE_PRESETS[chosen],
  };
}

export function validateConfig(input: unknown): HumazieConfig {
  return HumazieConfigSchema.parse(input);
}
