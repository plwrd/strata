/**
 * The selection constellation's action ring.
 *
 * Floats over the graph whenever something is selected: what is selected, which
 * layers it spans, how many private objects it touches, roughly what it costs in
 * tokens, and the actions that operate on it.
 *
 * The counts come from the store's summary (which reads the graph) and the token
 * estimate comes from Python's plan — the UI does not do arithmetic on privacy.
 */

import { summariseSelection, useStore } from "../state/store";

export function SelectionRing(): JSX.Element | null {
  const state = useStore();
  const summary = summariseSelection(state);

  if (summary.count === 0) return null;

  const layerNames = summary.layerIds
    .map(
      (id) =>
        state.layers.find((layer) => layer.id === id)?.display_name ??
        "unknown",
    )
    .join(", ");

  return (
    <div className="ring" role="region" aria-label="Selection">
      <div className="ring__facts">
        <span className="ring__count">{summary.count}</span>
        <span className="ring__label">selected</span>
        <span className="mono ring__detail">
          {layerNames}
          {summary.privateCount > 0 ? ` · ${summary.privateCount} private` : ""}
          {summary.lockedCount > 0 ? ` · ${summary.lockedCount} locked` : ""}
          {state.plan
            ? ` · ~${state.plan.estimated_tokens.toLocaleString()} tokens`
            : ""}
        </span>
      </div>

      <div className="ring__actions">
        <button
          type="button"
          className="button button--ai"
          onClick={() => state.setMode("command")}
        >
          Ask AI
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            const anchor = state.selectedIds[state.selectedIds.length - 1];
            if (anchor) void state.selectNeighbours(anchor);
          }}
        >
          Expand neighbours
        </button>
        <button
          type="button"
          className="button button--ghost"
          onClick={state.clearSelection}
        >
          Clear
        </button>
      </div>
    </div>
  );
}
