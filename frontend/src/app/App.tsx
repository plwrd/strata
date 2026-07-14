/**
 * The application shell: three columns, three modes, one selection.
 *
 * The layout is responsive by *collapsing structure*, not by hiding function:
 * below 1200px the inspector becomes a drawer, below 900px the navigator does
 * too, and every control remains reachable from the keyboard at every size.
 */

import { useEffect, useState } from "react";
import { AIComposerPanel } from "../features/ai-composer/AIComposerPanel";
import { NoteView } from "../features/editor/NoteView";
import { Graph2D } from "../features/graph-2d/Graph2D";
import { GraphScene } from "../features/graph-3d/GraphScene";
import { GraphErrorBoundary } from "../features/graph/GraphErrorBoundary";
import { GraphList } from "../features/graph/GraphList";
import { useGraphLayout } from "../features/graph/useGraphLayout";
import { isWebGLAvailable } from "../features/graph/webgl";
import { LayerPanel } from "../features/layers/LayerPanel";
import { SearchPanel } from "../features/search/SearchPanel";
import { CommandBar } from "../features/workspace/CommandBar";
import { StatusBar } from "../features/workspace/StatusBar";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { useStore } from "../state/store";
import { SelectionRing } from "./SelectionRing";

export function App(): JSX.Element {
  const state = useStore();
  const reducedMotion = useReducedMotion();
  const [navOpen, setNavOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);

  useEffect(() => {
    void state.initialise();
    // Deliberately once: initialise() is the app's cold start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const quality = state.settings?.graph_quality ?? "balanced";
  // Low-GPU mode is a user choice; missing WebGL is a fact. Either one means the
  // 3D canvas is never mounted, rather than mounted and then crashing.
  const webgl = isWebGLAvailable();
  const render3d = state.dimension === "3d" && webgl && quality !== "low-gpu";
  const { positions, computing } = useGraphLayout(
    state.graph,
    render3d ? "3d" : "2d",
    quality,
  );

  const handleSelect = (
    id: string,
    modifiers: { ctrl: boolean; shift: boolean },
  ): void => {
    if (modifiers.shift) state.rangeSelect(id);
    else if (modifiers.ctrl) state.toggleSelect(id);
    else state.select(id);
  };

  if (state.connection === "connecting") {
    return (
      <div className="boot" role="status">
        <span className="boot__pulse" aria-hidden="true" />
        <p>Connecting to the Strata host…</p>
      </div>
    );
  }

  if (state.connection === "unavailable") {
    return (
      <div className="boot boot--error" role="alert">
        <h1>The Strata host is not reachable</h1>
        <p>{state.connectionMessage}</p>
        <p className="mono">
          Strata runs inside its desktop shell. Start it with:
          <br />
          python -m app.main
        </p>
      </div>
    );
  }

  return (
    <div className="shell" data-mode={state.mode}>
      <CommandBar />

      <div className="shell__body">
        <button
          type="button"
          className="drawer-toggle drawer-toggle--left"
          aria-expanded={navOpen}
          aria-controls="navigator"
          onClick={() => setNavOpen((open) => !open)}
        >
          {navOpen ? "◀" : "▶"}
          <span className="visually-hidden">Toggle the navigator</span>
        </button>

        <aside
          id="navigator"
          className={`navigator ${navOpen ? "" : "navigator--closed"}`}
          aria-label="Navigator"
        >
          <div className="scroll-y navigator__scroll">
            <LayerPanel />
            <SearchPanel />
            {state.graph && (
              <GraphList
                graph={state.graph}
                selectedIds={state.selectedIds}
                onSelect={handleSelect}
                onOpen={(id) => void state.openNoteById(id)}
                onSelectAll={(ids) => state.selectMany(ids)}
              />
            )}
          </div>
        </aside>

        <main className="stage" aria-label="Workspace">
          {state.mode === "focus" ? (
            <NoteView />
          ) : (
            <div className="stage__graph">
              {state.loadingGraph || computing ? (
                <p className="stage__loading mono" role="status">
                  {computing ? "computing layout…" : "loading graph…"}
                </p>
              ) : null}

              {state.graph && state.graph.nodes.length === 0 && (
                <p className="empty-state">This workspace has no notes yet.</p>
              )}

              {state.graph && state.graph.nodes.length > 0 && (
                <>
                  {state.dimension === "3d" && !webgl && (
                    <p className="stage__fallback mono" role="status">
                      This display has no WebGL, so the 2D graph is shown.
                      Everything else works normally.
                    </p>
                  )}

                  {render3d ? (
                    <GraphErrorBoundary
                      fallback={
                        <Graph2D
                          graph={state.graph}
                          positions={positions}
                          selectedIds={state.selectedIds}
                          onSelect={handleSelect}
                          onOpen={(id) => void state.openNoteById(id)}
                        />
                      }
                    >
                      <GraphScene
                        graph={state.graph}
                        positions={positions}
                        selectedIds={state.selectedIds}
                        hoveredId={state.hoveredId}
                        reducedMotion={reducedMotion}
                        onSelect={handleSelect}
                        onHover={state.setHovered}
                        onOpen={(id) => void state.openNoteById(id)}
                      />
                    </GraphErrorBoundary>
                  ) : (
                    <Graph2D
                      graph={state.graph}
                      positions={positions}
                      selectedIds={state.selectedIds}
                      onSelect={handleSelect}
                      onOpen={(id) => void state.openNoteById(id)}
                    />
                  )}
                </>
              )}

              <SelectionRing />
            </div>
          )}
        </main>

        <button
          type="button"
          className="drawer-toggle drawer-toggle--right"
          aria-expanded={inspectorOpen}
          aria-controls="inspector"
          onClick={() => setInspectorOpen((open) => !open)}
        >
          {inspectorOpen ? "▶" : "◀"}
          <span className="visually-hidden">Toggle the inspector</span>
        </button>

        <aside
          id="inspector"
          className={`inspector ${inspectorOpen ? "" : "inspector--closed"}`}
          aria-label="Inspector"
        >
          <AIComposerPanel />
        </aside>
      </div>

      <StatusBar />
    </div>
  );
}
