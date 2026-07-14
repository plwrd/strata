/**
 * Application state.
 *
 * One store, sliced by concern. Business rules do not live here — the store holds
 * *what the user has selected and is looking at*, and calls the bridge for
 * anything that needs a decision. If a rule can be got wrong (what counts as
 * private, what may go to a model, what a locked layer reveals), it belongs in
 * Python, where it is tested and cannot be bypassed by a devtools console.
 */

import { create } from "zustand";
import { bridge, BridgeCallError } from "../bridge/client";
import type {
  AppSettings,
  ContentMode,
  ContextDepth,
  ContextPlan,
  ExportShape,
  ExportTarget,
  GraphSnapshot,
  HealthResponse,
  LayerDescriptor,
  Note,
  ProviderCapability,
  SearchResult,
  TreeResponse,
  WorkspaceState,
} from "../bridge/types";

export type AppMode = "focus" | "explore" | "command";
export type GraphDimension = "2d" | "3d";
export type ConnectionState = "connecting" | "ready" | "unavailable";

export interface SelectionSummary {
  count: number;
  noteCount: number;
  layerIds: string[];
  lockedCount: number;
  privateCount: number;
}

interface StrataState {
  // connection
  connection: ConnectionState;
  connectionMessage: string;
  health: HealthResponse | null;

  // workspace
  workspace: WorkspaceState | null;
  layers: LayerDescriptor[];
  tree: TreeResponse | null;
  graph: GraphSnapshot | null;
  loadingGraph: boolean;

  // view
  mode: AppMode;
  dimension: GraphDimension;
  settings: AppSettings | null;
  activeLensId: string;

  // selection — the single source of truth for "what the AI would see"
  selectedIds: string[];
  lastAnchorId: string | null;
  hoveredId: string | null;
  openNote: Note | null;

  // search
  searchQuery: string;
  searchResults: SearchResult[];
  searching: boolean;

  // composer
  prompt: string;
  target: ExportTarget;
  shape: ExportShape;
  depth: ContextDepth;
  contentMode: ContentMode;
  tokenBudget: number | null;
  providers: ProviderCapability[];
  plan: ContextPlan | null;
  planning: boolean;
  planError: string | null;

  // actions
  initialise: () => Promise<void>;
  setMode: (mode: AppMode) => void;
  setDimension: (dimension: GraphDimension) => void;
  applySettings: (values: Partial<AppSettings>) => Promise<void>;

  reloadGraph: () => Promise<void>;
  openNoteById: (noteId: string) => Promise<void>;

  select: (id: string) => void;
  toggleSelect: (id: string) => void;
  rangeSelect: (id: string) => void;
  selectMany: (ids: string[], mode?: "replace" | "add") => void;
  deselect: (id: string) => void;
  clearSelection: () => void;
  selectNeighbours: (id: string) => Promise<void>;
  setHovered: (id: string | null) => void;

  runSearch: (query: string) => Promise<void>;
  selectSearchResults: () => void;

  setPrompt: (prompt: string) => void;
  setTarget: (target: ExportTarget) => void;
  setShape: (shape: ExportShape) => void;
  setDepth: (depth: ContextDepth) => void;
  setContentMode: (mode: ContentMode) => void;
  setTokenBudget: (budget: number | null) => void;
  refreshPlan: () => Promise<void>;
}

const initialSelection = {
  selectedIds: [] as string[],
  lastAnchorId: null as string | null,
};

function describeError(error: unknown): string {
  if (error instanceof BridgeCallError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export const useStore = create<StrataState>((set, get) => ({
  connection: "connecting",
  connectionMessage: "",
  health: null,

  workspace: null,
  layers: [],
  tree: null,
  graph: null,
  loadingGraph: false,

  mode: "explore",
  dimension: "3d",
  settings: null,
  activeLensId: "lens_all",

  ...initialSelection,
  hoveredId: null,
  openNote: null,

  searchQuery: "",
  searchResults: [],
  searching: false,

  prompt: "",
  target: "generic",
  shape: "single-file",
  depth: "selected-only",
  contentMode: "full",
  tokenBudget: null,
  providers: [],
  plan: null,
  planning: false,
  planError: null,

  async initialise() {
    try {
      const health = await bridge.workspace.health();
      const settings = (await bridge.settings.get()).settings;
      const state = await bridge.workspace.openDefault();
      const providers = (await bridge.ai.providers()).providers;

      set({
        connection: "ready",
        health,
        settings,
        workspace: state,
        layers: state.workspace?.layers ?? [],
        providers,
        activeLensId: settings.default_lens_id,
      });
      applyDocumentSettings(settings);
      await get().reloadGraph();
    } catch (error) {
      set({
        connection: "unavailable",
        connectionMessage: describeError(error),
      });
    }
  },

  setMode: (mode) => set({ mode }),
  setDimension: (dimension) => set({ dimension }),

  async applySettings(values) {
    const settings = (await bridge.settings.update(values)).settings;
    set({ settings });
    applyDocumentSettings(settings);
  },

  async reloadGraph() {
    set({ loadingGraph: true });
    try {
      const [{ graph }, tree] = await Promise.all([
        bridge.graph.load({}),
        bridge.notes.tree(),
      ]);
      set({ graph, tree, loadingGraph: false });
    } catch (error) {
      set({ loadingGraph: false, connectionMessage: describeError(error) });
    }
  },

  async openNoteById(noteId) {
    try {
      const { note } = await bridge.notes.get(noteId);
      set({ openNote: note, mode: "focus" });
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  select: (id) => {
    set({ selectedIds: [id], lastAnchorId: id });
    void get().refreshPlan();
  },

  toggleSelect: (id) => {
    const { selectedIds } = get();
    const next = selectedIds.includes(id)
      ? selectedIds.filter((candidate) => candidate !== id)
      : [...selectedIds, id];
    set({ selectedIds: next, lastAnchorId: id });
    void get().refreshPlan();
  },

  // Shift-click selects the shortest path between the anchor and the target, which
  // in a graph is the useful analogue of a "range".
  rangeSelect: (id) => {
    const { lastAnchorId, graph, selectedIds } = get();
    if (!lastAnchorId || !graph || lastAnchorId === id) {
      get().toggleSelect(id);
      return;
    }
    const path = shortestPath(graph, lastAnchorId, id);
    const next = Array.from(new Set([...selectedIds, ...(path ?? [id])]));
    set({ selectedIds: next, lastAnchorId: id });
    void get().refreshPlan();
  },

  selectMany: (ids, mode = "replace") => {
    const next =
      mode === "add"
        ? Array.from(new Set([...get().selectedIds, ...ids]))
        : Array.from(new Set(ids));
    set({ selectedIds: next, lastAnchorId: next[next.length - 1] ?? null });
    void get().refreshPlan();
  },

  deselect: (id) => {
    set({
      selectedIds: get().selectedIds.filter((candidate) => candidate !== id),
    });
    void get().refreshPlan();
  },

  clearSelection: () =>
    set({ ...initialSelection, plan: null, planError: null }),

  async selectNeighbours(id) {
    try {
      const { node_ids } = await bridge.graph.neighbours(id);
      get().selectMany([id, ...node_ids], "add");
    } catch (error) {
      set({ planError: describeError(error) });
    }
  },

  setHovered: (id) => set({ hoveredId: id }),

  async runSearch(query) {
    set({ searchQuery: query, searching: true });
    if (!query.trim()) {
      set({ searchResults: [], searching: false });
      return;
    }
    try {
      const response = await bridge.search.query(query);
      set({ searchResults: response.results, searching: false });
    } catch (error) {
      set({
        searching: false,
        searchResults: [],
        connectionMessage: describeError(error),
      });
    }
  },

  selectSearchResults: () => {
    get().selectMany(
      get().searchResults.map((result) => result.object_id),
      "add",
    );
  },

  setPrompt: (prompt) => set({ prompt }),
  setTarget: (target) => {
    set({ target });
    void get().refreshPlan();
  },
  setShape: (shape) => {
    set({ shape });
    void get().refreshPlan();
  },
  setDepth: (depth) => {
    set({ depth });
    void get().refreshPlan();
  },
  setContentMode: (contentMode) => {
    set({ contentMode });
    void get().refreshPlan();
  },
  setTokenBudget: (tokenBudget) => {
    set({ tokenBudget });
    void get().refreshPlan();
  },

  // The plan is recomputed by Python on every change, so what the review panel
  // shows is always what an export would actually contain.
  async refreshPlan() {
    const {
      selectedIds,
      prompt,
      target,
      shape,
      depth,
      contentMode,
      tokenBudget,
      graph,
    } = get();
    const selectable = selectedIds.filter((id) => isExportable(graph, id));
    if (selectable.length === 0) {
      set({ plan: null, planError: null, planning: false });
      return;
    }
    set({ planning: true, planError: null });
    try {
      const { plan } = await bridge.ai.planContext({
        object_ids: selectable,
        prompt,
        target,
        shape,
        depth,
        content_mode: contentMode,
        token_budget: tokenBudget,
      });
      set({ plan, planning: false });
    } catch (error) {
      set({ plan: null, planning: false, planError: describeError(error) });
    }
  },
}));

/** Tags, folders and locked markers are selectable in the UI but are not sources. */
function isExportable(graph: GraphSnapshot | null, id: string): boolean {
  const node = graph?.nodes.find((candidate) => candidate.id === id);
  if (!node) return false;
  return !node.locked && node.type !== "tag" && node.type !== "folder";
}

export function summariseSelection(state: StrataState): SelectionSummary {
  const nodes = (state.graph?.nodes ?? []).filter((node) =>
    state.selectedIds.includes(node.id),
  );
  const privateLayers = new Set(
    state.layers
      .filter((layer) => layer.visibility === "private")
      .map((layer) => layer.id),
  );
  return {
    count: nodes.length,
    noteCount: nodes.filter(
      (node) => node.type !== "tag" && node.type !== "folder",
    ).length,
    layerIds: Array.from(new Set(nodes.map((node) => node.layer_id))),
    lockedCount: nodes.filter((node) => node.locked).length,
    privateCount: nodes.filter(
      (node) => privateLayers.has(node.layer_id) && !node.locked,
    ).length,
  };
}

export function shortestPath(
  graph: GraphSnapshot,
  from: string,
  to: string,
): string[] | null {
  const adjacency = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
    adjacency.get(edge.source)!.push(edge.target);
    adjacency.get(edge.target)!.push(edge.source);
  }
  const previous = new Map<string, string | null>([[from, null]]);
  const queue = [from];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current === to) {
      const path: string[] = [];
      let cursor: string | null = to;
      while (cursor !== null) {
        path.unshift(cursor);
        cursor = previous.get(cursor) ?? null;
      }
      return path;
    }
    for (const neighbour of adjacency.get(current) ?? []) {
      if (!previous.has(neighbour)) {
        previous.set(neighbour, current);
        queue.push(neighbour);
      }
    }
  }
  return null;
}

export function applyDocumentSettings(settings: AppSettings): void {
  const root = document.documentElement;
  root.dataset["appearance"] = settings.appearance;
  root.dataset["motion"] =
    settings.motion === "system" ? "system" : settings.motion;
  root.dataset["graphQuality"] = settings.graph_quality;
}
