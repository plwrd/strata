import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, type Browser, type BrowserContext, type Page } from "playwright";
import type { HumazieConfig } from "../config.js";
import type {
  ActionLog,
  FlowExecutionResult,
  HumazieFlow,
  HumazieIssue,
} from "../types.js";
import { nowIso, redactSecrets } from "../util/paths.js";
import {
  createRunPaths,
  issueSkeleton,
  logAction,
  type RunPaths,
} from "../logging/runLogger.js";
import { runsDir } from "../util/loadConfig.js";
import {
  clearHighlight,
  clearNarration,
  ensureVisualChrome,
  humanClick,
  humanPause,
  humanType,
  narrate,
  type VisualOptions,
} from "./humanVisual.js";

const require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-require-imports
const AxeBuilder = require("@axe-core/playwright").default as new (opts: {
  page: Page;
}) => {
  disableRules: (rules: string[]) => {
    analyze: () => Promise<{
      violations: Array<{ impact?: string | null; id: string; nodes: unknown[] }>;
    }>;
  };
  analyze: () => Promise<{
    violations: Array<{ impact?: string | null; id: string; nodes: unknown[] }>;
  }>;
};

type MonitorState = {
  consoleErrors: string[];
  consoleMessages: string[];
  networkErrors: Array<{ method: string; url: string; status?: number }>;
  httpErrors: string[];
};

function attachMonitors(page: Page): MonitorState {
  const state: MonitorState = {
    consoleErrors: [],
    consoleMessages: [],
    networkErrors: [],
    httpErrors: [],
  };

  page.on("console", (msg) => {
    const text = `[${msg.type()}] ${msg.text()}`;
    state.consoleMessages.push(text);
    if (msg.type() === "error") state.consoleErrors.push(text);
  });
  page.on("pageerror", (err) => {
    state.consoleErrors.push(`[pageerror] ${err.message}`);
  });
  page.on("requestfailed", (req) => {
    state.networkErrors.push({
      method: req.method(),
      url: req.url(),
    });
  });
  page.on("response", (res) => {
    if (res.status() >= 400) {
      state.httpErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      state.networkErrors.push({
        method: res.request().method(),
        url: res.url(),
        status: res.status(),
      });
    }
  });
  return state;
}

async function resolveLocator(page: Page, action: HumazieFlow["actions"][number]) {
  let root: Page | ReturnType<Page["getByRole"]> = page;
  if (action.withinRole) {
    root = page.getByRole(action.withinRole as Parameters<Page["getByRole"]>[0], {
      name: action.withinName,
    });
  }

  if (action.testId) return root.getByTestId(action.testId);
  if (action.title) return root.getByTitle(action.title);
  if (action.label) return root.getByLabel(action.label);
  if (action.placeholder) return root.getByPlaceholder(action.placeholder);
  if (action.role && action.name) {
    return root.getByRole(action.role as Parameters<Page["getByRole"]>[0], {
      name: action.name,
      exact: action.exact ?? false,
    });
  }
  if (action.role) {
    return root.getByRole(action.role as Parameters<Page["getByRole"]>[0]);
  }
  if (action.text) return root.getByText(action.text);
  return null;
}

function selectorStrategy(action: HumazieFlow["actions"][number]): string {
  if (action.testId) return "testId";
  if (action.title) return "title";
  if (action.label) return "label";
  if (action.placeholder) return "placeholder";
  if (action.role && action.name) return "role+name";
  if (action.role) return "role";
  if (action.text) return "text";
  return "none";
}

async function screenshot(
  page: Page,
  paths: RunPaths,
  config: HumazieConfig,
  name: string,
): Promise<string | undefined> {
  if (!config.screenshots.enabled) return undefined;
  const path = join(paths.screenshots, `${name}.png`);
  await page.screenshot({ path, fullPage: config.screenshots.fullPage });
  return path;
}

export type AgentOptions = {
  mobile?: boolean;
  visualOverride?: Partial<VisualOptions> & { enabled?: boolean };
};

export class BrowserExecutionAgent {
  private browser: Browser | null = null;
  private readonly mobile: boolean;
  private readonly visual: VisualOptions;

  constructor(
    private readonly config: HumazieConfig,
    private readonly runId: string,
    options: AgentOptions = {},
  ) {
    this.mobile = options.mobile ?? false;
    this.visual = {
      ...config.visual,
      ...options.visualOverride,
      enabled: options.visualOverride?.enabled ?? config.visual.enabled,
    };
  }

  get paths(): RunPaths {
    return createRunPaths(runsDir(this.config), this.runId);
  }

  async start(): Promise<void> {
    const headed = this.visual.enabled && this.visual.headed;
    // Prefer the real user browser cache so headed Chromium works outside the
    // sandbox download folder used by some CI/agent environments.
    if (!process.env.PLAYWRIGHT_BROWSERS_PATH && process.env.LOCALAPPDATA) {
      process.env.PLAYWRIGHT_BROWSERS_PATH = `${process.env.LOCALAPPDATA}\\ms-playwright`;
    }
    console.log(
      headed
        ? `[humazie] Opening visible browser (human visual mode, slowMo=${this.visual.slowMoMs}ms)`
        : "[humazie] Running headless",
    );
    this.browser = await chromium.launch({
      headless: !headed,
      slowMo: headed ? this.visual.slowMoMs : 0,
      args: headed ? ["--start-maximized"] : undefined,
    });
  }

  async stop(): Promise<void> {
    await this.browser?.close();
    this.browser = null;
  }

  private async newContext(): Promise<BrowserContext> {
    if (!this.browser) throw new Error("Browser not started");
    const viewport = this.mobile
      ? this.config.mobileViewport
      : this.config.desktopViewport;
    const recordVideo =
      this.config.video.enabled &&
      (this.config.video.mode === "on" ||
        this.config.video.mode === "retain-on-failure" ||
        this.visual.enabled);
    return this.browser.newContext({
      viewport: this.visual.enabled && this.visual.headed && !this.mobile ? null : viewport,
      recordVideo: recordVideo
        ? { dir: this.paths.videos, size: viewport }
        : undefined,
      baseURL: this.config.baseUrl.replace(/\/humazie\.html.*$/, ""),
      reducedMotion: "no-preference",
    });
  }

  async executeFlow(flow: HumazieFlow): Promise<FlowExecutionResult> {
    if (!flow.safeToExecute) {
      return {
        flowId: flow.id,
        name: flow.name,
        status: "skipped",
        durationMs: 0,
        actions: [],
        issues: [],
        screenshots: [],
        consoleErrors: [],
        networkErrors: [],
      };
    }

    const context = await this.newContext();
    const page = await context.newPage();
    const monitors = attachMonitors(page);
    const started = Date.now();
    const actions: ActionLog[] = [];
    const issues: HumazieIssue[] = [];
    const screenshots: string[] = [];
    let failed = false;
    let tracePath: string | undefined;

    if (this.config.trace.enabled) {
      await context.tracing.start({ screenshots: true, snapshots: true });
    }

    try {
      console.log(`[humazie] ▶ ${flow.name}`);
      await narrate(page, this.visual, "Flow", flow.name);
      await humanPause(page, this.visual, 500);

      for (let i = 0; i < flow.actions.length; i++) {
        const action = flow.actions[i]!;
        const actionStart = Date.now();
        let status: ActionLog["status"] = "pass";
        let actual = "ok";
        let shot: string | undefined;

        try {
          console.log(`  → ${action.description}`);
          await this.runAction(page, flow, action, monitors);
          if (action.type !== "screenshot") {
            shot = await screenshot(
              page,
              this.paths,
              this.config,
              `${flow.id}_${i}`,
            );
            if (shot) screenshots.push(shot);
          }
        } catch (error) {
          failed = true;
          status = "fail";
          actual = error instanceof Error ? error.message : String(error);
          shot = await screenshot(
            page,
            this.paths,
            this.config,
            `${flow.id}_${i}_fail`,
          );
          if (shot) screenshots.push(shot);
          issues.push(
            issueSkeleton(this.runId, flow, `Failed: ${action.description}`, actual, {
              status: "reproduced",
              screenshots: shot ? [shot] : [],
              consoleErrors: [...monitors.consoleErrors],
              networkErrors: [...monitors.networkErrors],
            }),
          );
        }

        const entry: ActionLog = {
          timestamp: nowIso(),
          runId: this.runId,
          flowId: flow.id,
          url: page.url(),
          actionType: action.type,
          target: action.description,
          selectorStrategy: selectorStrategy(action),
          inputValue: action.value ? redactSecrets(action.value) : undefined,
          screenshotPath: shot,
          consoleMessages: [...monitors.consoleMessages].slice(-10),
          networkFailures: monitors.networkErrors.map(
            (n) => `${n.method} ${n.url} ${n.status ?? ""}`.trim(),
          ),
          httpErrors: [...monitors.httpErrors],
          expected: action.description,
          actual,
          status,
          durationMs: Date.now() - actionStart,
        };
        actions.push(entry);
        logAction(this.paths, entry);
        if (failed) break;
      }

      await narrate(
        page,
        this.visual,
        failed ? "Failed" : "Passed",
        failed ? `${flow.name} hit a problem` : `${flow.name} completed`,
      );
      await humanPause(page, this.visual, failed ? 1200 : 800);
      await clearNarration(page, this.visual);
    } finally {
      if (this.config.trace.enabled && (failed || this.config.trace.mode === "on")) {
        tracePath = join(this.paths.traces, `${flow.id}.zip`);
        await context.tracing.stop({ path: tracePath });
      } else if (this.config.trace.enabled) {
        await context.tracing.stop();
      }
      await context.close();
    }

    return {
      flowId: flow.id,
      name: flow.name,
      status: failed ? "failed" : "passed",
      durationMs: Date.now() - started,
      actions,
      issues,
      screenshots,
      tracePath,
      consoleErrors: monitors.consoleErrors,
      networkErrors: monitors.networkErrors,
    };
  }

  private async runAction(
    page: Page,
    flow: HumazieFlow,
    action: HumazieFlow["actions"][number],
    monitors: MonitorState,
  ): Promise<void> {
    const timeout = action.timeoutMs ?? 10_000;
    const visual = this.visual;

    switch (action.type) {
      case "goto": {
        const target = action.url ?? flow.startingRoute;
        await narrate(page, visual, "Open", target);
        await page.goto(target.startsWith("http") ? target : target, {
          waitUntil: "domcontentloaded",
          timeout,
        });
        await page.getByText("STRATA").first().waitFor({ timeout });
        await ensureVisualChrome(page, visual);
        await narrate(page, visual, "Loaded", flow.name);
        await humanPause(page, visual);
        return;
      }
      case "click": {
        const locator = await resolveLocator(page, action);
        if (!locator) throw new Error(`No selector for click: ${action.description}`);
        await humanClick(page, locator, visual, action.description, timeout);
        return;
      }
      case "fill": {
        const locator = await resolveLocator(page, action);
        if (!locator) throw new Error(`No selector for fill: ${action.description}`);
        await humanType(
          page,
          locator,
          action.value ?? "",
          visual,
          action.description,
          timeout,
        );
        return;
      }
      case "press": {
        await narrate(page, visual, "Key", action.key ?? "Escape");
        await page.keyboard.press(action.key ?? "Escape");
        await humanPause(page, visual);
        return;
      }
      case "expect_visible": {
        const locator = await resolveLocator(page, action);
        if (!locator) throw new Error(`No selector for expect_visible: ${action.description}`);
        await narrate(page, visual, "Look for", action.description);
        const target = locator.first();
        await target.waitFor({ state: "attached", timeout });
        try {
          await target.scrollIntoViewIfNeeded();
        } catch {
          // Some nodes are display:none until a parent expands; visibility wait below covers that.
        }
        await target.waitFor({ state: "visible", timeout });
        await humanPause(page, visual, visual.pauseAfterExpectMs);
        return;
      }
      case "expect_hidden": {
        const locator = await resolveLocator(page, action);
        if (!locator) throw new Error(`No selector for expect_hidden: ${action.description}`);
        await narrate(page, visual, "Wait until gone", action.description);
        await locator.first().waitFor({ state: "hidden", timeout });
        await humanPause(page, visual, visual.pauseAfterExpectMs);
        return;
      }
      case "expect_text": {
        await narrate(page, visual, "Look for text", action.text ?? "");
        await page.getByText(action.text ?? "").first().waitFor({ state: "visible", timeout });
        await humanPause(page, visual, visual.pauseAfterExpectMs);
        return;
      }
      case "expect_url": {
        const expected = action.url ?? "";
        if (!page.url().includes(expected)) {
          throw new Error(`URL ${page.url()} does not include ${expected}`);
        }
        return;
      }
      case "wait_for": {
        await page.waitForTimeout(action.timeoutMs ?? 300);
        return;
      }
      case "screenshot": {
        await screenshot(page, this.paths, this.config, `${flow.id}_manual`);
        return;
      }
      case "axe": {
        if (!this.config.accessibility.enabled) return;
        await narrate(page, visual, "Accessibility", "Scanning the page");
        await clearHighlight(page, visual);
        const builder = new AxeBuilder({ page }).disableRules(["color-contrast"]);
        const results = await builder.analyze();
        const bad = results.violations.filter((v: { impact?: string | null }) =>
          this.config.accessibility.failOn.includes(
            (v.impact ?? "minor") as "critical" | "serious" | "moderate" | "minor",
          ),
        );
        if (bad.length > 0) {
          const summary = bad
            .map(
              (v: { impact?: string | null; id: string; nodes: unknown[] }) =>
                `${v.impact}: ${v.id} (${v.nodes.length} nodes)`,
            )
            .join("; ");
          throw new Error(`Accessibility violations: ${summary}`);
        }
        await humanPause(page, visual);
        return;
      }
      case "custom": {
        await narrate(page, visual, "Check", action.description);
        await this.runCustom(page, action.description, monitors);
        await humanPause(page, visual);
        return;
      }
      default:
        throw new Error(`Unknown action type: ${action.type}`);
    }
  }

  private async runCustom(
    page: Page,
    description: string,
    monitors: MonitorState,
  ): Promise<void> {
    if (description.includes("Import page is disabled when empty")) {
      const submit = page.getByRole("dialog", { name: "Capture" }).getByRole("button", {
        name: "Import page",
      });
      const disabled = await submit.isDisabled();
      if (!disabled) {
        throw new Error("Import page was enabled with empty URL");
      }
      return;
    }
    if (description.includes("primary Capture submit is disabled")) {
      const submit = page.getByRole("dialog", { name: "Capture" }).getByRole("button", {
        name: "Capture",
        exact: true,
      });
      const disabled = await submit.isDisabled();
      if (!disabled) {
        throw new Error("Capture submit was enabled with empty content");
      }
      return;
    }
    if (description.includes("Set Knowledge AI policy")) {
      const region = page.getByRole("region", { name: "Layers" });
      await region.scrollIntoViewIfNeeded();
      const select = region.getByLabel("AI policy for Knowledge");
      await select.scrollIntoViewIfNeeded();
      await select.selectOption({ label: "AI: local only" });
      return;
    }
    if (description.includes("no unexpected console errors")) {
      const unexpected = monitors.consoleErrors.filter((line) => {
        const lower = line.toLowerCase();
        if (lower.includes("download the react devtools")) return false;
        if (lower.includes("three.")) return false;
        if (lower.includes("favicon")) return false;
        if (lower.includes("frame-ancestors")) return false;
        if (lower.includes("importscripts")) return false;
        if (lower.includes("worker module")) return false;
        if (lower.includes("failed to rehydrate")) return false;
        if (lower.includes("init did not return a callable function")) return false;
        return true;
      });
      if (unexpected.length > 0) {
        throw new Error(`Unexpected console errors: ${unexpected.slice(0, 3).join(" | ")}`);
      }
      return;
    }
    if (description.includes("horizontal overflow")) {
      const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return doc.scrollWidth > doc.clientWidth + 1;
      });
      if (overflow) throw new Error("Page requires horizontal scrolling");
      return;
    }
    throw new Error(`Unhandled custom action: ${description}`);
  }
}
