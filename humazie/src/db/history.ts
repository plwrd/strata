import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { repoRoot } from "../util/paths.js";
import type {
  FixRecord,
  HumazieFlow,
  HumazieIssue,
  ProductMap,
  RunSummary,
} from "../types.js";

let prisma: import("@prisma/client").PrismaClient | null = null;

function databaseUrl(): string {
  const dbFile = join(repoRoot(), ".humazie", "humazie.db").replace(/\\/g, "/");
  mkdirSync(dirname(dbFile), { recursive: true });
  return `file:${dbFile}`;
}

async function getPrisma(): Promise<import("@prisma/client").PrismaClient | null> {
  try {
    process.env.HUMAZIE_DATABASE_URL = databaseUrl();
    const { PrismaClient } = await import("@prisma/client");
    if (!prisma) prisma = new PrismaClient();
    return prisma;
  } catch {
    return null;
  }
}

export async function persistRun(data: {
  summary: RunSummary;
  productMap: ProductMap;
  flows: HumazieFlow[];
  issues: HumazieIssue[];
  fixes: FixRecord[];
}): Promise<void> {
  const client = await getPrisma();
  if (!client) return;

  try {
    await client.reviewRun.upsert({
      where: { id: data.summary.runId },
      create: {
        id: data.summary.runId,
        status: data.summary.failedFlows > 0 ? "completed_with_failures" : "completed",
        baseUrl: data.summary.baseUrl,
        gitCommit: data.summary.gitCommit,
        mobile: data.summary.mobile,
        autoFix: data.summary.autoFix,
        totalFlows: data.summary.totalFlows,
        passedFlows: data.summary.passedFlows,
        failedFlows: data.summary.failedFlows,
        issuesFound: data.summary.issuesFound,
        issuesFixed: data.summary.issuesFixed,
        durationMs: data.summary.durationMs,
        summaryPath: join(repoRoot(), ".humazie", "runs", data.summary.runId, "summary.md"),
        productMap: JSON.stringify(data.productMap),
        flowsJson: JSON.stringify(data.flows),
        issuesJson: JSON.stringify(data.issues),
        fixesJson: JSON.stringify(data.fixes),
      },
      update: {
        status: data.summary.failedFlows > 0 ? "completed_with_failures" : "completed",
        totalFlows: data.summary.totalFlows,
        passedFlows: data.summary.passedFlows,
        failedFlows: data.summary.failedFlows,
        issuesFound: data.summary.issuesFound,
        issuesFixed: data.summary.issuesFixed,
        durationMs: data.summary.durationMs,
        issuesJson: JSON.stringify(data.issues),
        fixesJson: JSON.stringify(data.fixes),
      },
    });

    for (const issue of data.issues) {
      await client.issueRecord.upsert({
        where: { id: issue.id },
        create: {
          id: issue.id,
          runId: data.summary.runId,
          flowId: issue.flowId,
          title: issue.title,
          category: issue.category,
          severity: issue.severity,
          status: issue.status,
          payload: JSON.stringify(issue),
        },
        update: {
          status: issue.status,
          payload: JSON.stringify(issue),
        },
      });
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("does not exist")) {
      console.warn(
        "[humazie] SQLite schema missing — run `npm --prefix humazie run db:push` (file artifacts still saved).",
      );
    } else {
      console.warn(`[humazie] SQLite history unavailable: ${message}`);
    }
  } finally {
    try {
      await client.$disconnect();
    } catch {
      // ignore
    }
    prisma = null;
  }
}

export async function listRuns(limit = 20): Promise<unknown[]> {
  const client = await getPrisma();
  if (!client) return [];
  try {
    return await client.reviewRun.findMany({
      orderBy: { createdAt: "desc" },
      take: limit,
    });
  } catch {
    return [];
  } finally {
    try {
      await client.$disconnect();
    } catch {
      // ignore
    }
    prisma = null;
  }
}
