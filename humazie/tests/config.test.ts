import { describe, expect, it } from "vitest";
import { validateConfig, HumazieConfigSchema } from "../src/config.js";

describe("config validation", () => {
  it("accepts a complete config", () => {
    const config = validateConfig({
      baseUrl: "http://127.0.0.1:5173/humazie.html",
      startCommand: "npm --prefix frontend run humazie:serve",
      mobileViewport: { width: 390, height: 844 },
      desktopViewport: { width: 1440, height: 900 },
      autoRepair: {
        enabled: false,
        maxFilesChanged: 2,
        maxLinesChanged: 50,
        requireManualReviewCategories: ["payment"],
      },
      screenshots: { enabled: true, fullPage: false },
      video: { enabled: true, mode: "retain-on-failure" },
      trace: { enabled: true, mode: "retain-on-failure" },
      accessibility: { enabled: true, failOn: ["critical"] },
      cleanup: { enabled: true, tagPrefix: "humazie-run-" },
      commands: {
        lint: "npm --prefix frontend run lint",
        typecheck: "npm --prefix frontend run typecheck",
        test: "npm --prefix frontend test",
        build: "npm --prefix frontend run build",
      },
      discovery: {
        frontendSrc: "frontend/src",
        featureRoots: ["frontend/src/app"],
        existingTestsGlob: "frontend/src/tests/**/*.test.ts",
      },
      logging: { runsDir: ".humazie/runs" },
    });
    expect(config.maxFlows).toBe(20);
    expect(config.auth.strategy).toBe("none");
  });

  it("rejects invalid baseUrl", () => {
    expect(() =>
      HumazieConfigSchema.parse({
        baseUrl: "not-a-url",
        startCommand: "x",
        mobileViewport: { width: 1, height: 1 },
        desktopViewport: { width: 1, height: 1 },
        autoRepair: {
          enabled: false,
          maxFilesChanged: 1,
          maxLinesChanged: 1,
          requireManualReviewCategories: [],
        },
        screenshots: { enabled: true, fullPage: false },
        video: { enabled: false, mode: "off" },
        trace: { enabled: false, mode: "off" },
        accessibility: { enabled: false, failOn: [] },
        cleanup: { enabled: true, tagPrefix: "x" },
        commands: { lint: "a", typecheck: "b", test: "c", build: "d" },
        discovery: {
          frontendSrc: "x",
          featureRoots: [],
          existingTestsGlob: "y",
        },
        logging: { runsDir: ".humazie/runs" },
      }),
    ).toThrow();
  });
});
