import { describe, expect, it } from "vitest";
import { generateFlows } from "../src/flows/generateFlows.js";
import type { ProductMap } from "../src/types.js";
import { validateConfig } from "../src/config.js";

const map: ProductMap = {
  generatedAt: new Date().toISOString(),
  nodes: [],
  edges: [],
  modes: ["focus", "explore", "views", "command"],
  dialogs: ["Capture"],
  forms: ["CaptureDialog"],
  sourceSummary: {},
};

const config = validateConfig({
  baseUrl: "http://127.0.0.1:5173/humazie.html",
  startCommand: "echo",
  mobileViewport: { width: 390, height: 844 },
  desktopViewport: { width: 1440, height: 900 },
  autoRepair: {
    enabled: false,
    maxFilesChanged: 2,
    maxLinesChanged: 40,
    requireManualReviewCategories: [],
  },
  screenshots: { enabled: true, fullPage: false },
  video: { enabled: false, mode: "off" },
  trace: { enabled: false, mode: "off" },
  accessibility: { enabled: true, failOn: ["critical"] },
  cleanup: { enabled: true, tagPrefix: "humazie-run-" },
  commands: { lint: "a", typecheck: "b", test: "c", build: "d" },
  discovery: {
    frontendSrc: "frontend/src",
    featureRoots: ["frontend/src/app"],
    existingTestsGlob: "x",
  },
  logging: { runsDir: ".humazie/runs" },
  maxFlows: 32,
});

describe("flow generator", () => {
  it("creates product-specific capture and mode flows", () => {
    const flows = generateFlows(map, config, { runId: "run-test" });
    expect(flows.some((f) => /Capture text/i.test(f.name))).toBe(true);
    expect(flows.some((f) => /mode navigation/i.test(f.name))).toBe(true);
    expect(flows.some((f) => /Search the workspace/i.test(f.name))).toBe(true);
    expect(flows.some((f) => /structured view types/i.test(f.name))).toBe(true);
    expect(flows.some((f) => /New layer dialog/i.test(f.name))).toBe(true);
    expect(flows.length).toBeGreaterThanOrEqual(15);
    expect(flows.every((f) => f.actions.length > 0)).toBe(true);
    expect(flows.every((f) => typeof f.id === "string")).toBe(true);
  });

  it("filters by route keyword", () => {
    const flows = generateFlows(map, config, {
      runId: "run-test",
      routeFilter: "capture",
    });
    expect(flows.length).toBeGreaterThan(0);
    expect(flows.some((f) => /capture/i.test(f.name))).toBe(true);
  });
});
