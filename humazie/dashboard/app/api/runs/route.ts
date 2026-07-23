import { NextResponse } from "next/server";
import { listRuns, readRunJson } from "../../../lib/runs";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const runs = listRuns().map((run) => ({
    id: run.id,
    summary: run.summary,
    issues: readRunJson(run.id, "issues.json"),
  }));
  return NextResponse.json({ runs });
}
