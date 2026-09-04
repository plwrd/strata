/**
 * The application shell: three columns, three modes, one selection.
 *
 * The layout is responsive by *collapsing structure*, not by hiding function:
 * below 1280px the inspector becomes a drawer, below 960px the navigator does
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
import { CommandStage } from "../features/operations/CommandStage";
import { SearchPanel } from "../features/search/SearchPanel";
import { ViewsStage } from "../features/views/ViewsStage";
import { CommandBar } from "../features/workspace/CommandBar";
import { StatusBar } from "../features/workspace/StatusBar";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { useStore } from "../state/store";
import { AppContextMenu } from "./ContextMenu";
import { ErrorBanner } from "./ErrorBanner";
import { NavigatorAccordion } from "./NavigatorAccordion";
import { SelectionRing } from "./SelectionRing";

const INSPECTOR_TABS: { value: InspectorTab; label: string }[] = [
  { value: "ai", label: "AI" },
  { value: "operations", label: "Changes" },
  { value: "properties", label: "Properties" },
  { value: "links", label: "Links" },
];

const CHEVRON_LEFT =
  "M10.2 3.2a.75.75 0 0 1 0 1.06L6.46 8l3.74 3.74a.75.75 0 1 1-1.06 1.06l-4.27-4.27a.75.75 0 0 1 0-1.06l4.27-4.27a.75.75 0 0 1 1.06 0Z";
const CHEVRON_RIGHT =
  "M5.8 3.2a.75.75 0 0 1 1.06 0l4.27 4.27a.75.75 0 0 1 0 1.06L6.86 12.8a.75.75 0 1 1-1.06-1.06L9.54 8 5.8 4.26a.75.75 0 0 1 0-1.06Z";

function RailToggle(props: {
  kind: "nav" | "inspector";
  open: boolean;
  onToggle: () => void;
}): JSX.Element {
  const collapsing = props.open;
  const label =
    props.kind === "nav"
      ? collapsing
        ? "Collapse navigator"
        : "Expand navigator"
      : collapsing
        ? "Collapse inspector"
        : "Expand inspector";
  // Nav open → point left (collapse); nav closed → point right (expand).
  // Inspector open → point right; inspector closed → point left.
  const path =
    props.kind === "nav"
      ? collapsing
        ? CHEVRON_LEFT
        : CHEVRON_RIGHT
      : collapsing
        ? CHEVRON_RIGHT
        : CHEVRON_LEFT;

  return (
    <button
      type="button"
      className={[
        "drawer-toggle",
        `drawer-toggle--${props.kind}`,
        props.open ? "" : "drawer-toggle--recover",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-expanded={props.open}
      aria-controls={props.kind === "nav" ? "navigator" : "inspector"}
      aria-label={label}
      title={label}
      onClick={props.onToggle}
    >
      <svg
        className="drawer-toggle__icon"
        viewBox="0 0 16 16"
        width="12"
        height="12"
        aria-hidden="true"
        focusable="false"
      >
        <path fill="currentColor" d={path} />
      </svg>
    </button>
  );
}

export function App(): JSX.Element {
  const state = useStore();
  const reducedMotion = useReducedMotion();
  const [navOpen, setNavOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("ai");

  // The inspector follows the mode: Focus → Properties; otherwise AI.
  // Command hosts Changes in the centre stage, so the inspector stays on AI.
  useEffect(() => {
    setInspectorTab(state.mode === "focus" ? "properties" : "ai");
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

  // Global editor shortcuts. Qt WebEngine has no browser chrome, so these do
  // not fight the host — but we still preventDefault so nothing else claims them.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      const store = useStore.getState();

      if (key === "n" && !event.shiftKey) {
        const target = store.layers.find((layer) => layer.state !== "locked");
        if (!target) return;
        event.preventDefault();
        void store.createNote(target.id, "");
        return;
      }

      // Ctrl/Cmd+W — close the active editor tab.
      if (key === "w" && !event.shiftKey) {
        if (!store.activeNoteId || store.tabs.length === 0) return;
        event.preventDefault();
        store.closeTab(store.activeNoteId);
        return;
      }

      // Ctrl/Cmd+Shift+T — reopen the most recently closed tab.
      if (key === "t" && event.shiftKey) {
        event.preventDefault();
        void store.reopenClosedTab();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const qualitySetting = state.settings?.graph_quality ?? "balanced";
  const quality =
    state.settings?.battery_saver === true ? "low-gpu" : qualitySetting;
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
    const store = useStore.getState();
    if (modifiers.shift) {
      store.rangeSelect(id);
      return;
    }
    if (modifiers.ctrl) {
      store.toggleSelect(id);
      return;
    }
    store.select(id);
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

      <div
        className={[
          "shell__body",
          navOpen ? "" : "shell__body--nav-closed",
          inspectorOpen ? "" : "shell__body--inspector-closed",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <aside
          id="navigator"
          className={`navigator ${navOpen ? "" : "navigator--closed"}`}
          aria-label="Navigator"
          aria-hidden={!navOpen}
        >
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
          <RailToggle
            kind="nav"
            open={navOpen}
            onToggle={() => setNavOpen((open) => !open)}
          />
          <RailToggle
            kind="inspector"
            open={inspectorOpen}
            onToggle={() => setInspectorOpen((open) => !open)}
          />
          {state.mode === "focus" ? (
            <EditorPane />
          ) : state.mode === "views" ? (
            <ViewsStage />
          ) : state.mode === "command" ? (
            <CommandStage />
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
                  {state.graph.truncated && (
                    <p className="stage__fallback mono" role="status">
                      Showing {state.graph.nodes.length} of{" "}
                      {state.graph.total_nodes} nodes. The rest are omitted
                      until the graph is filtered.
                    </p>
                  )}

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
          aria-hidden={!inspectorOpen}
        >
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
            {inspectorTab === "ai" &&
              (state.mode === "command" ? (
                <p className="empty-state">
                  Ask / export lives in the Command stage under the{" "}
                  <strong>Ask / export</strong> tab.
                </p>
              ) : (
                <AIComposerPanel />
              ))}
            {inspectorTab === "operations" &&
              (state.mode === "command" ? (
                <p className="empty-state">
                  Change plans live in the Command stage under{" "}
                  <strong>Changes</strong>.
                </p>
              ) : (
                <OperationsPanel />
              ))}
            {inspectorTab === "properties" && <PropertiesPanel />}
            {inspectorTab === "links" && <LinksPanel />}
          </div>
        </aside>
      </div>

      <ErrorBanner />
      <StatusBar />
      <AppContextMenu />
      <OnboardingTour />
    </div>
  );
}
