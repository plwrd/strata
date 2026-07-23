import Link from "next/link";
import { listRuns } from "../lib/runs";

export const dynamic = "force-dynamic";

export default function HomePage(): JSX.Element {
  const runs = listRuns();

  return (
    <div>
      <h1>Review runs</h1>
      <p className="muted">
        Local history from <span className="mono">.humazie/runs</span>. Start a
        new review to exercise Strata through Playwright.
      </p>

      {runs.length === 0 ? (
        <div className="panel">
          <p>No runs yet.</p>
          <Link className="button primary" href="/review/new">
            Start a review
          </Link>
        </div>
      ) : (
        <div className="grid">
          {runs.map((run) => {
            const failed = Number(run.summary?.failedFlows ?? 0);
            const passed = Number(run.summary?.passedFlows ?? 0);
            return (
              <article key={run.id} className="panel row">
                <div>
                  <Link href={`/runs/${run.id}`}>
                    <strong className="mono">{run.id}</strong>
                  </Link>
                  <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                    {passed} passed · {failed} failed ·{" "}
                    {String(run.summary?.issuesFound ?? 0)} issues
                  </p>
                </div>
                <span
                  className={`badge ${failed > 0 ? "badge--bad" : "badge--good"}`}
                >
                  {failed > 0 ? "has failures" : "clean"}
                </span>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
