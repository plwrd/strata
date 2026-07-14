/** The bottom bar: security, sync, model activity, selection, performance. */

import { summariseSelection, useStore } from "../../state/store";

export function StatusBar(): JSX.Element {
  const state = useStore();
  const summary = summariseSelection(state);
  const lockedLayers = state.layers.filter(
    (layer) => layer.visibility === "private" && layer.state !== "unlocked",
  ).length;

  return (
    <footer className="statusbar" aria-label="Status">
      <span className="statusbar__item">
        <span
          className={`tag ${lockedLayers > 0 ? "tag--locked" : "tag--public"}`}
        >
          {lockedLayers > 0 ? `${lockedLayers} locked` : "no private layers"}
        </span>
      </span>

      <span className="statusbar__item mono">sync: personal (offline)</span>

      <span className="statusbar__item mono">
        model:{" "}
        {state.providers.some((p) => p.configured)
          ? "configured"
          : "none configured"}
      </span>

      <span className="statusbar__item mono">
        selection: {summary.count}
        {summary.privateCount > 0 ? ` (${summary.privateCount} private)` : ""}
      </span>

      <span className="statusbar__item mono">
        graph: {state.graph?.total_nodes ?? 0} nodes /{" "}
        {state.graph?.total_edges ?? 0} edges
        {state.loadingGraph ? " · loading" : ""}
      </span>

      <span className="statusbar__item statusbar__item--right mono">
        {state.health
          ? `strata ${state.health.version} · qt ${state.health.qt_version}`
          : ""}
      </span>
    </footer>
  );
}
