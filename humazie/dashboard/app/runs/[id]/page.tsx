import Link from "next/link";
import { readRunFile, readRunJson } from "../../../lib/runs";
import { RunActions } from "./RunActions";

export const dynamic = "force-dynamic";

type Issue = {
  id: string;
  title: string;
  category: string;
  severity: string;
  status: string;
  userImpact: string;
  screenshots?: string[];
};

type FlowResult = {
  flowId: string;
  name: string;
  status: string;
  durationMs: number;
};

export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<JSX.Element> {
  const { id } = await params;
  const summary = readRunJson<Record<string, unknown>>(id, "run.json");
  const issues = readRunJson<Issue[]>(id, "issues.json") ?? [];
  const results = readRunJson<FlowResult[]>(id, "results.json") ?? [];
  const report = readRunFile(id, "summary.md") ?? "";
  const flows = readRunJson<Array<{ id: string; name: string }>>(id, "flows.json") ?? [];

  if (!summary) {
    return (
      <div className="panel">
        <p>Run not found.</p>
        <Link href="/">Back</Link>
      </div>
    );
  }

  return (
    <div>
      <p>
        <Link href="/">← Runs</Link>
      </p>
      <h1 className="mono">{id}</h1>
      <div className="panel">
        <div className="row">
          <div>
            <p className="muted" style={{ margin: 0 }}>
              Commit {String(summary.gitCommit)} ·{" "}
              {summary.mobile ? "mobile" : "desktop"} · auto-fix{" "}
              {String(summary.autoFix)}
            </p>
            <p>
              {String(summary.passedFlows)}/{String(summary.totalFlows)} passed ·{" "}
              {String(summary.issuesFound)} issues ·{" "}
              {String(summary.issuesFixed)} fixed ·{" "}
              {String(summary.issuesManualReview)} manual review
            </p>
          </div>
          <RunActions runId={id} flows={flows} />
        </div>
      </div>

      <h2>Flows</h2>
      <div className="grid">
        {results.map((flow) => (
          <div key={flow.flowId} className="panel row">
            <div>
              <strong>{flow.name}</strong>
              <p className="muted mono" style={{ margin: "0.25rem 0 0" }}>
                {flow.flowId}
              </p>
            </div>
            <span
              className={`badge ${
                flow.status === "passed"
                  ? "badge--good"
                  : flow.status === "failed"
                    ? "badge--bad"
                    : "badge--warn"
              }`}
            >
              {flow.status}
            </span>
          </div>
        ))}
      </div>

      <h2>Issues</h2>
      {issues.length === 0 ? (
        <div className="panel muted">No issues.</div>
      ) : (
        <div className="grid">
          {issues.map((issue) => (
            <article key={issue.id} className="panel">
              <div className="row">
                <strong>{issue.title}</strong>
                <span className="badge badge--warn">{issue.status}</span>
              </div>
              <p className="muted">
                {issue.severity} · {issue.category}
              </p>
              <p>{issue.userImpact}</p>
              <p className="mono muted">
                Distinguish:{" "}
                {issue.category === "test_infrastructure"
                  ? "Test issue"
                  : issue.category === "environment"
                    ? "Environment issue"
                    : "Product issue"}
              </p>
            </article>
          ))}
        </div>
      )}

      <h2>Report</h2>
      <pre className="logs">{report}</pre>
    </div>
  );
}
