/**
 * Command mode — AI change plans and bulk operations in the centre stage.
 *
 * Explore keeps the graph; Command is the workspace where you describe a change,
 * review the plan, and apply it. Selection still comes from the graph or the
 * navigator — this stage just acts on it.
 */

import { summariseSelection, useStore } from "../../state/store";
import { AIComposerPanel } from "../ai-composer/AIComposerPanel";
import { OperationsPanel } from "./OperationsPanel";
import { useState } from "react";

type CommandTab = "changes" | "ask";

export function CommandStage(): JSX.Element {
  const state = useStore();
  const summary = summariseSelection(state);
  const [tab, setTab] = useState<CommandTab>("changes");

  return (
    <section className="command-stage" aria-label="Command">
      <header className="command-stage__header">
        <div className="command-stage__intro">
          <h1 className="command-stage__title">Command</h1>
          <p className="command-stage__lede">
            Propose AI changes, review the plan, and apply them as a transaction.
          </p>
        </div>

        <div className="command-stage__selection" aria-live="polite">
          {summary.count === 0 ? (
            <p className="command-stage__selection-empty">
              No selection — pick notes in Explore or the Graph list, then return
              here.
            </p>
          ) : (
            <p className="command-stage__selection-summary mono">
              {summary.noteCount} note{summary.noteCount === 1 ? "" : "s"}
              {summary.privateCount > 0
                ? ` · ${summary.privateCount} private`
                : ""}
              {summary.lockedCount > 0
                ? ` · ${summary.lockedCount} locked`
                : ""}
              {` · ${state.selectedIds.length} selected`}
            </p>
          )}
          <div className="command-stage__selection-actions">
            <button
              type="button"
              className="button button--ghost"
              onClick={() => state.setMode("explore")}
            >
              Open graph
            </button>
            {summary.count > 0 && (
              <button
                type="button"
                className="button button--ghost"
                onClick={() => state.clearSelection()}
              >
                Clear selection
              </button>
            )}
          </div>
        </div>
      </header>

      <div className="command-stage__tabs" role="tablist" aria-label="Command tools">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "changes"}
          className={`command-stage__tab ${tab === "changes" ? "command-stage__tab--active" : ""}`}
          onClick={() => setTab("changes")}
        >
          Changes
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "ask"}
          className={`command-stage__tab ${tab === "ask" ? "command-stage__tab--active" : ""}`}
          onClick={() => setTab("ask")}
        >
          Ask / export
        </button>
      </div>

      <div className="command-stage__body scroll-y">
        {tab === "changes" ? <OperationsPanel /> : <AIComposerPanel />}
      </div>
    </section>
  );
}
