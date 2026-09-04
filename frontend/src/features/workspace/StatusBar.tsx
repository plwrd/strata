/** The bottom bar: security, sync, model activity, selection, performance. */

import { useShallow } from "zustand/react/shallow";
import { summariseSelection, useStore } from "../../state/store";

export function StatusBar(): JSX.Element {
  const {
    layers,
    providers,
    selectedIds,
    graph,
    loadingGraph,
    health,
    collab,
    settings,
  } = useStore(
    useShallow((state) => ({
      layers: state.layers,
      providers: state.providers,
      selectedIds: state.selectedIds,
      graph: state.graph,
      loadingGraph: state.loadingGraph,
      health: state.health,
      collab: state.collab,
      settings: state.settings,
    })),
  );
  const summary = summariseSelection({
    selectedIds,
    graph,
    layers,
  });
  const lockedLayers = layers.filter(
    (layer) => layer.visibility === "private" && layer.state !== "unlocked",
  ).length;

  const shared = Object.values(collab).filter(
    (entry) => entry.enabled && entry.mode === "shared",
  );
  const relay = (settings?.relay_url ?? "").trim();
  let syncLabel = "sync: personal (offline)";
  if (shared.length > 0) {
    const peers = shared.reduce((sum, entry) => sum + entry.peers.length, 0);
    syncLabel =
      shared.length === 1
        ? `sync: shared${peers > 0 ? ` · ${peers} peer(s)` : ""}`
        : `sync: shared (${shared.length} layers)`;
  } else if (relay) {
    syncLabel = "sync: personal (relay)";
  }

  const graphLabel = graph?.truncated
    ? `graph: ${graph.nodes.length}/${graph.total_nodes} nodes (truncated)`
    : `graph: ${graph?.total_nodes ?? 0} nodes / ${graph?.total_edges ?? 0} edges`;

  return (
    <footer className="statusbar" aria-label="Status">
      <span className="statusbar__item">
        <span
          className={`tag ${lockedLayers > 0 ? "tag--locked" : "tag--public"}`}
        >
          {lockedLayers > 0 ? `${lockedLayers} locked` : "no private layers"}
        </span>
      </span>

      <span className="statusbar__item mono">{syncLabel}</span>

      <span className="statusbar__item mono">
        model:{" "}
        {providers.some((p) => p.configured) ? "configured" : "none configured"}
      </span>

      <span className="statusbar__item mono">
        selection: {summary.count}
        {summary.privateCount > 0 ? ` (${summary.privateCount} private)` : ""}
      </span>

      <span className="statusbar__item mono">
        {graphLabel}
        {loadingGraph ? " · loading" : ""}
      </span>

      <span className="statusbar__item statusbar__item--right mono">
        {health ? `strata ${health.version} · qt ${health.qt_version}` : ""}
      </span>
    </footer>
  );
}
