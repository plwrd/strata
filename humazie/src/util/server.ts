import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import type { HumazieConfig } from "../config.js";
import { repoRoot } from "./paths.js";

export type ServerHandle = {
  process: ChildProcess | null;
  stop: () => Promise<void>;
};

async function isReachable(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { method: "GET" });
    return response.ok || response.status < 500;
  } catch {
    return false;
  }
}

export async function ensureAppServer(config: HumazieConfig): Promise<ServerHandle> {
  if (config.reuseExistingServer && (await isReachable(config.baseUrl))) {
    return {
      process: null,
      stop: async () => undefined,
    };
  }

  const child = spawn(config.startCommand, {
    cwd: repoRoot(),
    shell: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
  });

  const started = Date.now();
  while (Date.now() - started < config.startTimeoutMs) {
    if (await isReachable(config.baseUrl)) {
      return {
        process: child,
        stop: async () => {
          if (!child.killed) {
            child.kill("SIGTERM");
            await delay(500);
            if (!child.killed) child.kill("SIGKILL");
          }
        },
      };
    }
    if (child.exitCode !== null) {
      throw new Error(
        `Start command exited before the app became reachable (code ${child.exitCode}).`,
      );
    }
    await delay(500);
  }

  child.kill("SIGTERM");
  throw new Error(
    `Timed out waiting for ${config.baseUrl} after ${config.startTimeoutMs}ms.`,
  );
}
