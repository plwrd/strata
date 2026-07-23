#!/usr/bin/env node
import { Command } from "commander";
import { loadConfig } from "./util/loadConfig.js";
import { runDiscover, runReview, rerunFlows, writeLatestReport } from "./review.js";

const program = new Command();

program
  .name("humazie")
  .description("Humazie Bot — autonomous product review for Strata")
  .option("-c, --config <path>", "Path to humazie.config.ts", "humazie.config.ts");

program
  .command("discover")
  .description("Discover product map from sources and runtime")
  .action(async () => {
    const opts = program.opts<{ config: string }>();
    const config = await loadConfig(opts.config);
    const map = await runDiscover(config);
    console.log(
      JSON.stringify(
        {
          nodes: map.nodes.length,
          edges: map.edges.length,
          modes: map.modes,
          dialogs: map.dialogs.slice(0, 20),
        },
        null,
        2,
      ),
    );
  });

program
  .command("review")
  .description("Discover, generate flows, execute, optionally auto-fix, and report")
  .option("--route <route>", "Limit flows related to a route/mode")
  .option("--mobile", "Use mobile viewport", false)
  .option("--no-fix", "Disable automatic repair")
  .option(
    "--visual",
    "Watch human-like actions in a visible browser (highlights, typing, narration)",
    true,
  )
  .option("--headless", "Run without opening a browser window (CI mode)", false)
  .option(
    "--pace <pace>",
    "Visual tempo: demo (slow & clear), balanced (default), brisk (faster suite)",
    "balanced",
  )
  .action(
    async (cmdOpts: {
      route?: string;
      mobile?: boolean;
      fix?: boolean;
      visual?: boolean;
      headless?: boolean;
      pace?: "demo" | "balanced" | "brisk";
    }) => {
      const opts = program.opts<{ config: string }>();
      const config = await loadConfig(opts.config);
      const visual = cmdOpts.headless ? false : (cmdOpts.visual ?? config.visual.enabled);
      const result = await runReview({
        config,
        routeFilter: cmdOpts.route,
        mobile: Boolean(cmdOpts.mobile),
        autoFix: cmdOpts.fix !== false && config.autoRepair.enabled,
        visual,
        pace: cmdOpts.pace,
      });
      console.log(`Run ${result.summary.runId}`);
      console.log(
        `Flows: ${result.summary.passedFlows}/${result.summary.totalFlows} passed, ${result.summary.failedFlows} failed`,
      );
      console.log(
        `Issues: ${result.summary.issuesFound} (fixed ${result.summary.issuesFixed})`,
      );
      console.log(`Report: ${result.reportPath}`);
      console.log(`Videos: ${result.reportPath.replace(/summary\.md$/, "videos/")}`);
      if (result.summary.failedFlows > 0) process.exitCode = 1;
    },
  );

program
  .command("rerun")
  .description("Rerun a single flow by id")
  .requiredOption("--flow <flowId>", "Flow id")
  .option("--mobile", "Use mobile viewport", false)
  .option("--no-fix", "Disable automatic repair")
  .option("--visual", "Watch human-like actions in a visible browser", true)
  .option("--headless", "Run without opening a browser window", false)
  .action(
    async (cmdOpts: {
      flow: string;
      mobile?: boolean;
      fix?: boolean;
      visual?: boolean;
      headless?: boolean;
    }) => {
      const opts = program.opts<{ config: string }>();
      const config = await loadConfig(opts.config);
      const visual = cmdOpts.headless ? false : (cmdOpts.visual ?? config.visual.enabled);
      const result = await rerunFlows({
        config,
        flowId: cmdOpts.flow,
        mobile: Boolean(cmdOpts.mobile),
        autoFix: cmdOpts.fix !== false && config.autoRepair.enabled,
        visual,
      });
      console.log(`Rerun ${result.summary.runId} flow=${cmdOpts.flow}`);
      console.log(`Report: ${result.reportPath}`);
      if (result.summary.failedFlows > 0) process.exitCode = 1;
    },
  );

program
  .command("report")
  .description("Print the latest Markdown review report")
  .action(async () => {
    const opts = program.opts<{ config: string }>();
    const config = await loadConfig(opts.config);
    const md = await writeLatestReport(config);
    console.log(md);
  });

await program.parseAsync(process.argv);
// Ensure Playwright/Prisma handles cannot keep the process alive.
process.exit(process.exitCode ?? 0);
