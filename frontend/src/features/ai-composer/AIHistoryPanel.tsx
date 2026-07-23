/**
 * The workspace's persistent AI memory — every model call, as recorded on disk.
 *
 * The list is read straight from Python's persisted history, so it survives a
 * restart and shows exactly what the durable record contains: a record that ran
 * against a private layer arrives redacted (metadata only), because that is all
 * that was ever written. Clearing is the user's "forget my AI activity" control
 * and is deliberately a two-step button, not a silent action.
 */

import { useCallback, useEffect, useState } from "react";
import { bridge } from "../../bridge/client";
import type { AIExecutionRecord } from "../../bridge/types";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; executions: AIExecutionRecord[] }
  | { kind: "error"; message: string };

function formatTime(iso: string): string {
  const time = new Date(iso);
  return Number.isNaN(time.getTime()) ? iso : time.toLocaleString();
}

export function AIHistoryPanel(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [confirmingClear, setConfirmingClear] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { executions } = await bridge.ai.listHistory();
      setState({ kind: "ready", executions });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof Error
            ? error.message
            : "Could not load the AI history.",
      });
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const clear = useCallback(async () => {
    setConfirmingClear(false);
    try {
      await bridge.ai.clearHistory();
      setState({ kind: "ready", executions: [] });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof Error
            ? error.message
            : "Could not clear the AI history.",
      });
    }
  }, []);

  return (
    <section className="ai-history" aria-label="AI history">
      <button
        type="button"
        className="button button--ghost ai-history__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Hide AI history" : "AI history"}
      </button>

      {open && state.kind === "loading" && (
        <p className="composer__status" role="status">
          Loading history…
        </p>
      )}

      {open && state.kind === "error" && (
        <p className="composer__status composer__status--error" role="alert">
          {state.message}{" "}
          <button
            type="button"
            className="button button--ghost"
            onClick={() => void load()}
          >
            Retry
          </button>
        </p>
      )}

      {open && state.kind === "ready" && (
        <>
          {state.executions.length === 0 ? (
            <p className="empty-state">
              No AI activity recorded yet. Every model request made in this
              workspace is remembered here — and survives a restart.
            </p>
          ) : (
            <>
              <ul className="ai-history__list">
                {state.executions.map((record) => (
                  <li key={record.id} className="ai-history__item">
                    <div className="ai-history__meta">
                      <span className="tag tag--ai">
                        {record.kind === "plan-generation" ? "plan" : "ask"}
                      </span>
                      <span className="ai-history__provider">
                        {record.provider}
                        {record.model ? ` · ${record.model}` : ""}
                      </span>
                      <span
                        className={
                          record.is_remote ? "tag tag--warning" : "tag"
                        }
                      >
                        {record.is_remote ? "remote" : "local"}
                      </span>
                      {record.redacted && (
                        <span
                          className="tag tag--private"
                          title="This request involved a private layer, so only metadata was persisted."
                        >
                          redacted
                        </span>
                      )}
                      {record.result !== "completed" && (
                        <span className="tag tag--danger">{record.result}</span>
                      )}
                    </div>
                    {!record.redacted && record.prompt && (
                      <p className="ai-history__prompt">{record.prompt}</p>
                    )}
                    <p className="ai-history__detail">
                      {formatTime(record.created_at)}
                      {record.output_tokens > 0 &&
                        ` · ${record.output_tokens} tokens out`}
                      {record.source_count > 0 &&
                        ` · ${record.source_count} source(s)`}
                    </p>
                  </li>
                ))}
              </ul>

              {confirmingClear ? (
                <div className="ai-history__confirm" role="alertdialog">
                  <span>
                    Delete the recorded AI history for this workspace?
                  </span>
                  <button
                    type="button"
                    className="button button--danger"
                    onClick={() => void clear()}
                  >
                    Delete history
                  </button>
                  <button
                    type="button"
                    className="button button--ghost"
                    onClick={() => setConfirmingClear(false)}
                  >
                    Keep it
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => setConfirmingClear(true)}
                >
                  Clear history…
                </button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
