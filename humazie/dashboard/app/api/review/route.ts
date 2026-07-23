import { spawn } from "node:child_process";
import { join } from "node:path";
import { NextResponse } from "next/server";
import { z } from "zod";
import { readRunJson } from "../../../lib/runs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BodySchema = z.object({
  action: z.enum(["start", "rerun", "rerun-failed"]),
  route: z.string().optional(),
  mobile: z.boolean().optional(),
  autoFix: z.boolean().optional(),
  visual: z.boolean().optional(),
  flowId: z.string().optional(),
  runId: z.string().optional(),
});

function humazieRoot(): string {
  return join(process.cwd(), "..");
}

function runCli(args: string[]): Promise<{ code: number; output: string }> {
  return new Promise((resolve) => {
    const child = spawn("npx", ["tsx", "src/cli.ts", ...args], {
      cwd: humazieRoot(),
      shell: true,
      env: { ...process.env },
    });
    let output = "";
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
    });
    child.on("close", (code) => resolve({ code: code ?? 1, output }));
  });
}

export async function POST(request: Request): Promise<NextResponse> {
  const body = BodySchema.parse(await request.json());

  if (body.action === "start") {
    const args = ["review"];
    if (body.route) args.push("--route", body.route);
    if (body.mobile) args.push("--mobile");
    if (body.autoFix === false) args.push("--no-fix");
    if (body.visual === false) args.push("--headless");
    else args.push("--visual");
    const result = await runCli(args);
    const runMatch = result.output.match(/Run\s+(run-[^\s]+)/);
    return NextResponse.json({
      ok: result.code === 0,
      runId: runMatch?.[1],
      message: result.code === 0 ? "Review completed" : "Review finished with failures",
      log: result.output,
      error: result.code === 0 ? undefined : result.output,
    });
  }

  if (body.action === "rerun") {
    if (!body.flowId) {
      return NextResponse.json({ error: "flowId required" }, { status: 400 });
    }
    const result = await runCli(["rerun", "--flow", body.flowId, "--no-fix"]);
    return NextResponse.json({
      ok: result.code === 0,
      message: result.output,
      log: result.output,
      error: result.code === 0 ? undefined : result.output,
    });
  }

  if (body.action === "rerun-failed") {
    if (!body.runId) {
      return NextResponse.json({ error: "runId required" }, { status: 400 });
    }
    const results =
      readRunJson<Array<{ flowId: string; status: string }>>(body.runId, "results.json") ??
      [];
    const failed = results.filter((r) => r.status === "failed").map((r) => r.flowId);
    if (failed.length === 0) {
      return NextResponse.json({ message: "No failed flows to rerun." });
    }
    const outputs: string[] = [];
    for (const flowId of failed) {
      const result = await runCli(["rerun", "--flow", flowId, "--no-fix"]);
      outputs.push(result.output);
    }
    return NextResponse.json({
      message: `Reran ${failed.length} failed flow(s).`,
      log: outputs.join("\n\n"),
    });
  }

  return NextResponse.json({ error: "Unknown action" }, { status: 400 });
}

export async function GET(): Promise<NextResponse> {
  return NextResponse.json({
    status: "ok",
    service: "humazie-dashboard",
  });
}
