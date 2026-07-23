/**
 * The application shell: three columns, three modes, one selection.
 *
 * The layout is responsive by *collapsing structure*, not by hiding function:
 * below 1200px the inspector becomes a drawer, below 900px the navigator does
 * too, and every control remains reachable from the keyboard at every size.
 */

import { useEffect, useState } from "react";
import { AIComposerPanel } from "../features/ai-composer/AIComposerPanel";
import { CollaborationPanel } from "../features/collaboration/CollaborationPanel";
import { EditorPane } from "../features/editor/EditorPane";
import { FileTree } from "../features/explorer/FileTree";
import { LinksPanel } from "../features/links/LinksPanel";
import { OnboardingTour } from "../features/onboarding/OnboardingTour";
import {
  registerShellChrome,
  type InspectorTab,
} from "../features/onboarding/shellChrome";
import { OperationsPanel } from "../features/operations/OperationsPanel";
import { PropertiesPanel } from "../features/properties/PropertiesPanel";
import { Graph2D } from "../features/graph-2d/Graph2D";
import { GraphScene } from "../features/graph-3d/GraphScene";
import { GraphControls } from "../features/graph/GraphControls";
import { GraphErrorBoundary } from "../features/graph/GraphErrorBoundary";
import { GraphList } from "../features/graph/GraphList";
import { useGraphLayout } from "../features/graph/useGraphLayout";
import { isWebGLAvailable } from "../features/graph/webgl";
import { LayerPanel } from "../features/layers/LayerPanel";
import { SearchPanel } from "../features/search/SearchPanel";
import { ViewsStage } from "../features/views/ViewsStage";
import { CommandBar } from "../features/workspace/CommandBar";
import { StatusBar } from "../features/workspace/StatusBar";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { useStore } from "../state/store";
import { AppContextMenu } from "./ContextMenu";
import { NavigatorAccordion } from "./NavigatorAccordion";
import { SelectionRing } from "./SelectionRing";

const INSPECTOR_TABS: { value: InspectorTab; label: string }[] = [
  { value: "ai", label: "AI" },
  { value: "operations", label: "Changes" },
  { value: "properties", label: "Properties" },
  { value: "links", label: "Links" },
];

export function App(): JSX.Element {
  const state = useStore();
  const reducedMotion = useReducedMotion();
  const [navOpen, setNavOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("ai");

  // The inspector follows the mode: Focus is about the note (properties), Command
  // is about bulk AI change (Changes), Explore/Views are about selection (AI).
  useEffect(() => {
    setInspectorTab(
      state.mode === "focus"
        ? "properties"
        : state.mode === "command"
          ? "operations"
          : "ai",
    );
  }, [state.mode]);

  useEffect(() => {
    void state.initialise();
    // Deliberately once: initialise() is the app's cold start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    registerShellChrome({
      setNavOpen,
      setInspectorOpen,
      setInspectorTab,
    });
    return () => registerShellChrome(null);
  }, []);

  // Ctrl/Cmd+N: new note in the first unlocked layer — the shortcut the empty
  // editor advertises. Qt WebEngine has no browser chrome, so nothing else
  // claims the combination.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "n")
        return;
      const target = useStore
        .getState()
        .layers.find((layer) => layer.state !== "locked");
      if (!target) return;
      event.preventDefault();
      void useStore.getState().createNote(target.id, "");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
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
        <aside
          id="navigator"
          className={`navigator ${navOpen ? "" : "navigator--closed"}`}
          aria-label="Navigator"
        >
          <button
            type="button"
            className="drawer-toggle drawer-toggle--nav"
            aria-expanded={navOpen}
            aria-controls="navigator"
            onClick={() => setNavOpen((open) => !open)}
          >
            {navOpen ? "◀" : "▶"}
            <span className="visually-hidden">Toggle the navigator</span>
          </button>
          <div className="scroll-y navigator__scroll">
            <NavigatorAccordion
              sections={[
                {
                  id: "layers",
                  label: "Layers",
                  defaultOpen: true,
                  children: <LayerPanel />,
                },
                {
                  id: "files",
                  label: "Files",
                  defaultOpen: true,
                  children: <FileTree />,
                },
                {
                  id: "search",
                  label: "Search",
                  children: <SearchPanel />,
                },
                {
                  id: "collab",
                  label: "Collaboration",
                  children: <CollaborationPanel />,
                },
                {
                  id: "graph",
                  label: "Graph",
                  children: state.graph ? (
                    <GraphList
                      graph={state.graph}
                      selectedIds={state.selectedIds}
                      onSelect={handleSelect}
                      onOpen={(id) => void state.openNoteById(id)}
                      onSelectAll={(ids) => state.selectMany(ids)}
                    />
                  ) : (
                    <p className="empty-state">Graph not loaded yet.</p>
                  ),
                },
              ]}
            />
          </div>
        </aside>

        <main className="stage" aria-label="Workspace">
          {state.mode === "focus" ? (
            <EditorPane />
          ) : state.mode === "views" ? (
            <ViewsStage />
          ) : (
            <div className="stage__graph" data-tour="graph">
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
                        particles={state.settings?.particles_enabled ?? true}
                        bloom={state.settings?.bloom_enabled ?? true}
                        quality={quality}
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
                      onLasso={(ids, add) =>
                        state.selectMany(ids, add ? "add" : "replace")
                      }
                    />
                  )}
                  <GraphControls />
                </>
              )}

              <SelectionRing />
              {/* Decorative vignette + HUD frame over the galaxy. Pointer-events
                  none, hidden from AT, and removed entirely in high contrast. */}
              <div className="stage__fx" aria-hidden="true" />
            </div>
          )}
        </main>

        <aside
          id="inspector"
          className={`inspector ${inspectorOpen ? "" : "inspector--closed"}`}
          aria-label="Inspector"
        >
          <button
            type="button"
            className="drawer-toggle drawer-toggle--inspector"
            aria-expanded={inspectorOpen}
            aria-controls="inspector"
            onClick={() => setInspectorOpen((open) => !open)}
          >
            {inspectorOpen ? "▶" : "◀"}
            <span className="visually-hidden">Toggle the inspector</span>
          </button>
          <div
            className="inspector__tabs"
            role="tablist"
            aria-label="Inspector panels"
          >
            {INSPECTOR_TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                role="tab"
                aria-selected={inspectorTab === tab.value}
                className={`inspector__tab ${inspectorTab === tab.value ? "inspector__tab--active" : ""}`}
                data-tour={tab.value === "ai" ? "inspector-ai-tab" : undefined}
                onClick={() => setInspectorTab(tab.value)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="inspector__body scroll-y">
            {inspectorTab === "ai" && <AIComposerPanel />}
            {inspectorTab === "operations" && <OperationsPanel />}
            {inspectorTab === "properties" && <PropertiesPanel />}
            {inspectorTab === "links" && <LinksPanel />}
          </div>
        </aside>
      </div>

      <StatusBar />
      <AppContextMenu />
      <OnboardingTour />
    </div>
  );
}
