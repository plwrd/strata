import { defineConfig, devices } from "@playwright/test";

/**
 * Optional Playwright project config for ad-hoc specs under humazie/tests/e2e.
 * The primary review loop uses the programmatic BrowserExecutionAgent.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "npm --prefix ../frontend run humazie:serve",
    url: "http://127.0.0.1:5173/humazie.html",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
