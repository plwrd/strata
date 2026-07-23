/**
 * Graph controls: display toggles and advanced selection.
 *
 * The selection tools operate on the current selection anchor (the last node the
 * user touched): expand to the connected component, the semantic cluster, or the
 * shortest path to another selected node. In 2D, shift-drag lassoes a region.
 */

import { useStore } from "../../state/store";

export function GraphControls(): JSX.Element {
  const state = useStore();
  const anchor = state.lastAnchorId;

  return (
    <div className="graph-controls" role="toolbar" aria-label="Graph controls">
      <div className="graph-controls__group">
        <label className="search__toggle">
          <input
            type="checkbox"
            checked={state.semanticEdges}
            onChange={(event) =>
              void state.setSemanticEdges(event.target.checked)
            }
          />
          <span>Semantic edges</span>
        </label>
        <label className="search__toggle">
          <input
            type="checkbox"
            checked={state.clusterColors}
            onChange={(event) =>
              void state.setClusterColors(event.target.checked)
            }
          />
          <span>Cluster colours</span>
        </label>
      </div>

      {anchor && (
        <div className="graph-controls__group">
          <span className="graph-controls__label mono">from selection:</span>
          <button
            type="button"
            className="button button--ghost"
            title="Select every node reachable from the anchor"
            onClick={() => state.selectConnected(anchor)}
          >
            Connected
          </button>
          <button
            type="button"
            className="button button--ghost"
            title="Select the anchor's semantic cluster"
            onClick={() => void state.selectCluster(anchor)}
          >
            Cluster
          </button>
          <button
            type="button"
            className="button button--ghost"
            title="Select every neighbour"
            onClick={() => void state.selectNeighbours(anchor)}
          >
            Neighbours
          </button>
          {state.selectedIds.length >= 2 && (
            <button
              type="button"
              className="button button--ghost"
              title="Select the shortest path between the first and last selected nodes"
              onClick={() =>
                void state.selectShortestPath(
                  state.selectedIds[0]!,
                  state.selectedIds[state.selectedIds.length - 1]!,
                )
              }
            >
              Path
            </button>
          )}
        </div>
      )}

      {state.dimension === "2d" && (
        <span className="graph-controls__hint mono">
          scroll to zoom · drag empty space to pan · shift-drag to lasso
        </span>
      )}
    </div>
  );
}
