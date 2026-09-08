import type { HumazieConfigInput } from "./humazie/src/config.js";

/**
 * Humazie Bot configuration for Strata.
 *
 * Strata is a desktop SPA (Vite + React) with modes instead of URL routes.
 * Reviews run against the local Vite harness that installs the fake bridge,
 * so Playwright never needs Qt WebEngine or a real workspace on disk.
 */
const config: HumazieConfigInput = {
  baseUrl: "http://127.0.0.1:5173/humazie.html",
  startCommand: "npm --prefix frontend run humazie:serve",
  startTimeoutMs: 60_000,
  reuseExistingServer: true,

  // Strata has no accounts; the fake bridge seeds a deterministic workspace.
  auth: {
    strategy: "none",
  },

  personas: [
    {
      id: "guest",
      label: "Local user with seeded public + private layers",
      description: "Fake-bridge workspace with sample graph and notes",
    },
  ],

  // Modes act as primary "routes" in the product map.
  allowedRoutes: ["/", "/humazie.html", "#focus", "#explore", "#views", "#command"],
  excludedRoutes: [],

  safeActions: [
    "navigate-mode",
    "open-dialog",
    "close-dialog",
    "fill-form",
    "submit-capture-text",
    "toggle-navigator",
    "toggle-inspector",
    "switch-graph-dimension",
    "switch-inspector-tab",
    "open-health",
    "keyboard-nav",
  ],
  unsafeActions: [
    "remote-ai-request",
    "delete-workspace",
    "wipe-encryption-key",
    "submit-payment",
    "send-real-email",
    "import-url-live-network",
  ],

  maxFlows: 32,
  browsers: ["chromium"],
  mobileViewport: { width: 390, height: 844 },
  desktopViewport: { width: 1440, height: 900 },

  autoRepair: {
    enabled: true,
    maxFilesChanged: 4,
    maxLinesChanged: 120,
    requireManualReviewCategories: [
      "authentication",
      "authorization",
      "payment",
      "destructive",
      "infrastructure",
      "migration",
    ],
  },

  screenshots: { enabled: true, fullPage: false },
  video: { enabled: true, mode: "on" },
  trace: { enabled: true, mode: "retain-on-failure" },

  // Watch mode: typing slower to read, pauses shorter so the suite is not endless.
  // Override with --pace=demo|balanced|brisk
  visual: {
    enabled: true,
    headed: true,
    pace: "balanced",
    narrate: true,
    cursor: true,
  },

  accessibility: {
    enabled: true,
    // color-contrast on the cyberpunk theme is a known design tradeoff; fail only on critical.
    failOn: ["critical"],
  },

  cleanup: {
    enabled: true,
    tagPrefix: "humazie-run-",
  },

  commands: {
    lint: "npm --prefix frontend run lint",
    typecheck: "npm --prefix frontend run typecheck",
    test: "npm --prefix frontend test",
    build: "npm --prefix frontend run build",
    format: "npm --prefix frontend run format",
  },

  discovery: {
    frontendSrc: "frontend/src",
    featureRoots: [
      "frontend/src/app",
      "frontend/src/features",
    ],
    existingTestsGlob: "frontend/src/tests/**/*.test.{ts,tsx}",
  },

  logging: {
    runsDir: ".humazie/runs",
  },
};

export default config;
