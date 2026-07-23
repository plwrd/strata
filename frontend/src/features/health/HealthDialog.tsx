/**
 * Knowledge health: what needs attention, and the action that fixes it.
 *
 * All arithmetic, no model — the counts come from the workspace itself, and
 * every finding names its remedy rather than just scolding. Locked layers
 * contribute nothing; the dialog says so instead of pretending the picture
 * is complete.
 */

import { useEffect, useState } from "react";
import { bridge } from "../../bridge/client";
import type { HealthReport } from "../../bridge/types";
import { useStore } from "../../state/store";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; report: HealthReport }
  | { kind: "error"; message: string };

export function HealthDialog(props: { onClose: () => void }): JSX.Element {
  const { openNoteById, setMode } = useStore();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { report } = await bridge.workspace.knowledgeHealth();
        if (!cancelled) setState({ kind: "ready", report });
      } catch (error) {
        if (!cancelled)
          setState({
            kind: "error",
            message:
              error instanceof Error
                ? error.message
                : "The health report failed.",
          });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const open = async (noteId: string): Promise<void> => {
    await openNoteById(noteId);
    setMode("focus");
    props.onClose();
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <div
        className="dialog health-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Knowledge health"
      >
        <header className="dialog__header">
          <h2 className="dialog__title">Knowledge health</h2>
          {state.kind === "ready" && (
            <span className="mono">
              {state.report.total_notes} note(s)
              {state.report.locked_layers > 0 &&
                ` · ${state.report.locked_layers} locked layer(s) not included`}
            </span>
          )}
        </header>

        {state.kind === "loading" && (
          <p className="composer__status" role="status">
            Taking the workspace's pulse…
          </p>
        )}

        {state.kind === "error" && (
          <p className="composer__status composer__status--error" role="alert">
            {state.message}
          </p>
        )}

        {state.kind === "ready" && (
          <div className="health-dialog__body scroll-y">
            {state.report.items.every((item) => item.count === 0) &&
              state.report.duplicates.length === 0 && (
                <p className="empty-state">
                  Nothing needs attention. Every capture is processed, every AI
                  page reviewed, every link intact.
                </p>
              )}

            {state.report.items
              .filter((item) => item.count > 0)
              .map((item) => (
                <section key={item.key} className="health-dialog__item">
                  <div className="health-dialog__item-head">
                    <span className="tag tag--warning">{item.count}</span>
                    <span className="health-dialog__label">{item.label}</span>
                  </div>
                  <p className="health-dialog__recommendation">
                    {item.recommendation}
                  </p>
                  {item.note_ids.length > 0 && (
                    <ul className="health-dialog__notes">
                      {item.note_titles.map((title, index) => {
                        const id = item.note_ids[index];
                        return (
                          <li key={`${item.key}:${title}:${index}`}>
                            {id ? (
                              <button
                                type="button"
                                className="links__item"
                                onClick={() => void open(id)}
                              >
                                {title}
                              </button>
                            ) : (
                              <span className="links__context">{title}</span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              ))}

            {state.report.duplicates.length > 0 && (
              <section className="health-dialog__item">
                <div className="health-dialog__item-head">
                  <span className="tag tag--warning">
                    {state.report.duplicates.length}
                  </span>
                  <span className="health-dialog__label">
                    Probable duplicate pairs
                  </span>
                </div>
                <p className="health-dialog__recommendation">
                  Open one of each pair and review — nothing merges
                  automatically.
                </p>
                <ul className="health-dialog__notes">
                  {state.report.duplicates.map((duplicate) => (
                    <li key={`${duplicate.note_a}:${duplicate.note_b}`}>
                      <button
                        type="button"
                        className="links__item"
                        onClick={() => void open(duplicate.note_a)}
                      >
                        {duplicate.note_a_title} ↔ {duplicate.note_b_title} (
                        {Math.round(duplicate.score * 100)}%)
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}

        <div className="dialog__actions">
          <button
            type="button"
            className="button button--ghost"
            onClick={props.onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
