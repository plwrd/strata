/**
 * Graph controls: display toggles and advanced selection.
 *
 * The selection tools operate on the current selection anchor (the last node the
 * user touched): expand to the connected component, the semantic cluster, or the
 * shortest path to another selected node. In 2D, shift-drag lassoes a region.
 */

import { useShallow } from "zustand/react/shallow";
import { useStore } from "../../state/store";

export function GraphControls(): JSX.Element {
  const {
    lastAnchorId,
    semanticEdges,
    clusterColors,
    setSemanticEdges,
    setClusterColors,
    selectConnected,
    selectCluster,
    selectNeighbours,
    selectShortestPath,
    selectedIds,
    dimension,
    graph,
  } = useStore(
    useShallow((state) => ({
      lastAnchorId: state.lastAnchorId,
      semanticEdges: state.semanticEdges,
      clusterColors: state.clusterColors,
      setSemanticEdges: state.setSemanticEdges,
      setClusterColors: state.setClusterColors,
      selectConnected: state.selectConnected,
      selectCluster: state.selectCluster,
      selectNeighbours: state.selectNeighbours,
      selectShortestPath: state.selectShortestPath,
      selectedIds: state.selectedIds,
      dimension: state.dimension,
      graph: state.graph,
    })),
  );
  const anchor = lastAnchorId;

  return (
    <div className="graph-controls" role="toolbar" aria-label="Graph controls">
      <div className="graph-controls__group">
        <label className="search__toggle">
          <input
            type="checkbox"
            checked={semanticEdges}
            onChange={(event) => void setSemanticEdges(event.target.checked)}
          />
          <span>Semantic edges</span>
        </label>
        <label className="search__toggle">
          <input
            type="checkbox"
            checked={clusterColors}
            onChange={(event) => void setClusterColors(event.target.checked)}
          />
          <span>Cluster colours</span>
        </label>
      </div>

      {graph?.truncated && (
        <span className="graph-controls__hint mono" role="status">
          Showing {graph.nodes.length} of {graph.total_nodes} nodes
        </span>
      )}

      {anchor && (
        <div className="graph-controls__group">
          <span className="graph-controls__label mono">from selection:</span>
          <button
            type="button"
            className="button button--ghost"
            title="Select every node reachable from the anchor"
            onClick={() => selectConnected(anchor)}
          >
            Connected
          </button>
          <button
            type="button"
            className="button button--ghost"
            title="Select the anchor's semantic cluster"
            onClick={() => void selectCluster(anchor)}
          >
            Cluster
          </button>
          <button
            type="button"
            className="button button--ghost"
            title="Select every neighbour"
            onClick={() => void selectNeighbours(anchor)}
          >
            Neighbours
          </button>
          {selectedIds.length >= 2 && (
            <button
              type="button"
              className="button button--ghost"
              title="Select the shortest path between the first and last selected nodes"
              onClick={() =>
                void selectShortestPath(
                  selectedIds[0]!,
                  selectedIds[selectedIds.length - 1]!,
                )
              }
            >
              Path
            </button>
          )}
        </div>
      )}

      {dimension === "2d" && (
        <span className="graph-controls__hint mono">
          scroll to zoom · drag empty space to pan · shift-drag to lasso
        </span>
      )}
    </div>
  );
}
