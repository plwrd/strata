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
  AIStreamEvent,
  AppSettings,
  ContentMode,
  ContextDepth,
  ContextPlan,
  ExportShape,
  ExportTarget,
  GraphSnapshot,
  HealthResponse,
  LayerDescriptor,
  LinkHealthResponse,
  LinksResponse,
  Note,
  NoteSchema,
  PolicyView,
  PrivacyReceipt,
  ProviderView,
  SearchResult,
  TrashEntry,
  TreeResponse,
  ValidationIssue,
  WorkspaceState,
} from "../bridge/types";

export type AppMode = "focus" | "explore" | "command" | "views";
export type GraphDimension = "2d" | "3d";
export type ConnectionState = "connecting" | "ready" | "unavailable";
export type ViewMode = "source" | "live" | "reading";

export interface Tab {
  id: string;
  title: string;
}

const EMPTY_LINKS: LinksResponse = {
  backlinks: [],
  unlinked_mentions: [],
  outgoing: [],
};

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

  // graph display options
  semanticEdges: boolean;
  clusterColors: boolean;

  // selection — the single source of truth for "what the AI would see"
  selectedIds: string[];
  lastAnchorId: string | null;
  hoveredId: string | null;

  // editor
  openNote: Note | null;
  activeNoteId: string | null;
  tabs: Tab[];
  viewMode: ViewMode;
  draft: string | null;
  dirty: Record<string, boolean>;
  saving: boolean;
  externalChange: boolean;
  links: LinksResponse;
  linkHealth: LinkHealthResponse;
  schemas: NoteSchema[];
  schemaId: string | null;
  issues: ValidationIssue[];
  trash: TrashEntry[];

  // search
  searchQuery: string;
  searchResults: SearchResult[];
  searching: boolean;
  semanticSearch: boolean;

  // composer
  prompt: string;
  target: ExportTarget;
  shape: ExportShape;
  depth: ContextDepth;
  contentMode: ContentMode;
  tokenBudget: number | null;
  providers: ProviderView[];
  keychainAvailable: boolean;
  plan: ContextPlan | null;
  planning: boolean;
  planError: string | null;

  // AI request
  providerId: string;
  model: string;
  policy: PolicyView | null;
  aiStreaming: boolean;
  aiOutput: string;
  aiError: string | null;
  aiRequestId: string | null;
  receipts: PrivacyReceipt[];

  // actions
  initialise: () => Promise<void>;
  setMode: (mode: AppMode) => void;
  setDimension: (dimension: GraphDimension) => void;
  applySettings: (values: Partial<AppSettings>) => Promise<void>;

  reloadGraph: () => Promise<void>;
  reloadTree: () => Promise<void>;

  // editor + files
  openNoteById: (noteId: string) => Promise<void>;
  closeTab: (noteId: string) => void;
  setViewMode: (mode: ViewMode) => void;
  setDraft: (noteId: string, content: string) => void;
  saveNote: (noteId: string, content: string) => Promise<void>;
  saveProperties: (
    noteId: string,
    properties: Record<string, unknown>,
  ) => Promise<void>;
  reloadOpenNote: () => Promise<void>;
  keepLocalEdits: () => void;

  createNote: (layerId: string, folderPath: string) => Promise<void>;
  renameNote: (noteId: string, title: string) => Promise<void>;
  moveNote: (noteId: string, folderPath: string) => Promise<void>;
  duplicateNote: (noteId: string) => Promise<void>;
  deleteNote: (noteId: string) => Promise<void>;
  restoreNote: (entry: string) => Promise<void>;
  createFolder: (layerId: string, folderPath: string) => Promise<void>;
  renameFolder: (folderId: string, name: string) => Promise<void>;
  deleteFolder: (folderId: string) => Promise<void>;
  attachFile: (
    layerId: string,
    filename: string,
    base64: string,
  ) => Promise<string>;

  // layers
  //
  // Passwords are parameters, never state. Nothing below retains one, so a
  // devtools inspection of the store cannot reveal a layer password — and neither
  // can a crash report that serialises it.
  createLayer: (
    name: string,
    visibility: "public" | "private",
    password: string | null,
    withRecoveryKey: boolean,
  ) => Promise<string | null>;
  unlockLayer: (layerId: string, password: string) => Promise<void>;
  unlockLayerWithRecoveryKey: (
    layerId: string,
    recoveryKey: string,
  ) => Promise<void>;
  lockLayer: (layerId: string) => Promise<void>;
  lockAllLayers: () => Promise<void>;
  changeLayerPassword: (
    layerId: string,
    oldPassword: string,
    newPassword: string,
  ) => Promise<void>;
  reissueRecoveryKey: (layerId: string, password: string) => Promise<string>;
  rotateLayerKey: (layerId: string, password: string) => Promise<number>;
  forgetPrivateState: (layerId: string) => void;
  refreshLayers: () => Promise<void>;
  afterLockStateChanged: () => Promise<void>;

  select: (id: string) => void;
  toggleSelect: (id: string) => void;
  rangeSelect: (id: string) => void;
  selectMany: (ids: string[], mode?: "replace" | "add") => void;
  deselect: (id: string) => void;
  clearSelection: () => void;
  selectNeighbours: (id: string) => Promise<void>;
  selectCluster: (id: string) => Promise<void>;
  selectConnected: (id: string) => void;
  selectShortestPath: (fromId: string, toId: string) => Promise<void>;
  selectByTag: (tag: string) => void;
  selectByLayer: (layerId: string) => void;
  setHovered: (id: string | null) => void;
  setSemanticEdges: (on: boolean) => Promise<void>;
  setClusterColors: (on: boolean) => Promise<void>;

  runSearch: (query: string) => Promise<void>;
  selectSearchResults: () => void;
  setSemanticSearch: (enabled: boolean) => Promise<void>;
  findSimilar: (objectId: string) => Promise<void>;

  setPrompt: (prompt: string) => void;
  setTarget: (target: ExportTarget) => void;
  setShape: (shape: ExportShape) => void;
  setDepth: (depth: ContextDepth) => void;
  setContentMode: (mode: ContentMode) => void;
  setTokenBudget: (budget: number | null) => void;
  refreshPlan: () => Promise<void>;

  // AI request
  setProvider: (providerId: string) => Promise<void>;
  setModel: (model: string) => void;
  refreshPolicy: () => Promise<void>;
  storeCredential: (providerId: string, apiKey: string) => Promise<boolean>;
  sendToModel: (confirmedRemote: boolean) => Promise<void>;
  cancelAIRequest: () => Promise<void>;
  clearAIOutput: () => void;
  loadReceipts: () => Promise<void>;
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

/** "Untitled", "Untitled 2", … — never a name that collides with an existing one. */
function uniqueTitle(existing: string[], base = "Untitled"): string {
  const taken = new Set(existing);
  if (!taken.has(base)) return base;
  let counter = 2;
  while (taken.has(`${base} ${counter}`)) counter += 1;
  return `${base} ${counter}`;
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
  semanticEdges: false,
  clusterColors: false,

  ...initialSelection,
  hoveredId: null,

  openNote: null,
  activeNoteId: null,
  tabs: [],
  viewMode: "live",
  draft: null,
  dirty: {},
  saving: false,
  externalChange: false,
  links: EMPTY_LINKS,
  linkHealth: { broken: [], orphans: [] },
  schemas: [],
  schemaId: null,
  issues: [],
  trash: [],

  searchQuery: "",
  semanticSearch: true,
  searchResults: [],
  searching: false,

  prompt: "",
  target: "generic",
  shape: "single-file",
  depth: "selected-only",
  contentMode: "full",
  tokenBudget: null,
  providers: [],
  keychainAvailable: true,
  plan: null,
  planning: false,
  planError: null,

  providerId: "ollama",
  model: "",
  policy: null,
  aiStreaming: false,
  aiOutput: "",
  aiError: null,
  aiRequestId: null,
  receipts: [],

  async initialise() {
    try {
      const health = await bridge.workspace.health();
      const settings = (await bridge.settings.get()).settings;
      const state = await bridge.workspace.openDefault();
      const providerInfo = await bridge.ai.providers();
      const schemas = (await bridge.notes.schemas()).schemas;

      // The first configured provider is the sensible default; a local one wins.
      const firstConfigured =
        providerInfo.providers.find((p) => p.configured && p.is_local) ??
        providerInfo.providers.find((p) => p.configured) ??
        providerInfo.providers[0];

      set({
        connection: "ready",
        health,
        settings,
        workspace: state,
        layers: state.workspace?.layers ?? [],
        providers: providerInfo.providers,
        keychainAvailable: providerInfo.keychain_available,
        providerId: firstConfigured?.provider_id ?? "ollama",
        schemas,
        activeLensId: settings.default_lens_id,
      });

      // AI output streams in over this signal, keyed by request id.
      await bridge.ai.onEvent((raw) => {
        const event = JSON.parse(raw) as AIStreamEvent;
        if (event.requestId !== get().aiRequestId) return;
        if (event.kind === "delta") {
          set({ aiOutput: get().aiOutput + (event.text ?? "") });
        } else if (event.kind === "error") {
          set({
            aiError: event.error ?? "The request failed.",
            aiStreaming: false,
          });
        } else if (event.kind === "done") {
          set({ aiStreaming: false });
          void get().loadReceipts();
        }
      });
      applyDocumentSettings(settings);

      // The file on disk is the truth, so a change out there invalidates what we
      // are showing. An *external* change while the user has unsaved edits is the
      // one case we refuse to resolve silently.
      await bridge.notes.onChanged((origin) => {
        if (origin === "external") {
          const { activeNoteId, dirty } = get();
          if (activeNoteId && dirty[activeNoteId]) {
            set({ externalChange: true });
            return;
          }
          void get().reloadOpenNote();
        }
        void get().reloadTree();
        void get().reloadGraph();
      });

      await get().reloadGraph();
      await get().reloadTree();
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
        bridge.graph.load({
          semantic_edges: get().semanticEdges,
          cluster: get().clusterColors,
        }),
        bridge.notes.tree(),
      ]);
      set({ graph, tree, loadingGraph: false });
    } catch (error) {
      set({ loadingGraph: false, connectionMessage: describeError(error) });
    }
  },

  async reloadTree() {
    try {
      const [tree, linkHealth, trash] = await Promise.all([
        bridge.notes.tree(),
        bridge.notes.linkHealth(),
        bridge.notes.listTrash(),
      ]);
      set({ tree, linkHealth, trash: trash.entries });
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async openNoteById(noteId) {
    try {
      const response = await bridge.notes.get(noteId);
      const tabs = get().tabs.some((tab) => tab.id === noteId)
        ? get().tabs
        : [...get().tabs, { id: noteId, title: response.note.metadata.title }];

      set({
        openNote: response.note,
        activeNoteId: noteId,
        schemaId: response.schema_id,
        issues: response.issues,
        tabs,
        // A freshly-opened note has no pending draft; keeping one from the previous
        // note is how an edit ends up written into the wrong file.
        draft: null,
        externalChange: false,
        mode: "focus",
      });

      const links = await bridge.notes.links(noteId);
      set({ links });
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  closeTab: (noteId) => {
    const tabs = get().tabs.filter((tab) => tab.id !== noteId);
    const wasActive = get().activeNoteId === noteId;
    const dirty = { ...get().dirty };
    delete dirty[noteId];

    set({ tabs, dirty });
    if (!wasActive) return;

    const next = tabs[tabs.length - 1];
    if (next) {
      void get().openNoteById(next.id);
    } else {
      set({
        openNote: null,
        activeNoteId: null,
        draft: null,
        links: EMPTY_LINKS,
      });
    }
  },

  setViewMode: (viewMode) => set({ viewMode }),

  setDraft: (noteId, content) => {
    const clean = get().openNote?.content === content;
    set({
      draft: content,
      dirty: { ...get().dirty, [noteId]: !clean },
    });
  },

  async saveNote(noteId, content) {
    if (get().saving) return;
    set({ saving: true });
    try {
      const response = await bridge.notes.update(noteId, content);
      set({
        openNote: response.note,
        schemaId: response.schema_id,
        issues: response.issues,
        draft: null,
        saving: false,
        dirty: { ...get().dirty, [noteId]: false },
      });
      // Saving changes links and tags, so the graph and the tree are now stale.
      void get().reloadTree();
      void get().reloadGraph();
      const links = await bridge.notes.links(noteId);
      set({ links });
    } catch (error) {
      set({ saving: false, connectionMessage: describeError(error) });
    }
  },

  async saveProperties(noteId, properties) {
    try {
      const response = await bridge.notes.updateProperties(noteId, properties);
      set({
        openNote: response.note,
        schemaId: response.schema_id,
        issues: response.issues,
      });
      void get().reloadTree();
      void get().reloadGraph();
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async reloadOpenNote() {
    const noteId = get().activeNoteId;
    if (!noteId) return;
    try {
      const response = await bridge.notes.get(noteId);
      set({
        openNote: response.note,
        schemaId: response.schema_id,
        issues: response.issues,
        draft: null,
        externalChange: false,
        dirty: { ...get().dirty, [noteId]: false },
      });
    } catch {
      // The note is gone (deleted or moved outside Strata). Close its tab rather
      // than leaving a phantom editor pointing at nothing.
      get().closeTab(noteId);
    }
  },

  keepLocalEdits: () => {
    // The user chose their buffer over the disk. Persist it now, so the two agree
    // again and the next external event is not a false alarm.
    const { activeNoteId, draft } = get();
    set({ externalChange: false });
    if (activeNoteId && draft !== null)
      void get().saveNote(activeNoteId, draft);
  },

  async createNote(layerId, folderPath) {
    try {
      const title = uniqueTitle(
        get().tree?.notes.map((note) => note.title) ?? [],
      );
      const response = await bridge.notes.create(
        layerId,
        title,
        folderPath,
        "",
      );
      await get().reloadTree();
      await get().reloadGraph();
      await get().openNoteById(response.note.metadata.id);
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async renameNote(noteId, title) {
    try {
      const { note } = await bridge.notes.rename(noteId, title);
      // The id is derived from the path, so a rename produces a *new* id: the tab
      // and the open note must follow it or they point at a file that is gone.
      const newId = note.metadata.id;
      set({
        tabs: get().tabs.map((tab) =>
          tab.id === noteId ? { id: newId, title: note.metadata.title } : tab,
        ),
      });
      await get().reloadTree();
      await get().reloadGraph();
      if (get().activeNoteId === noteId) await get().openNoteById(newId);
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async moveNote(noteId, folderPath) {
    try {
      const response = await bridge.notes.move(noteId, folderPath);
      const newId = response.note.metadata.id;
      set({
        tabs: get().tabs.map((tab) =>
          tab.id === noteId ? { ...tab, id: newId } : tab,
        ),
      });
      await get().reloadTree();
      await get().reloadGraph();
      if (get().activeNoteId === noteId) await get().openNoteById(newId);
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async duplicateNote(noteId) {
    try {
      const response = await bridge.notes.duplicate(noteId);
      await get().reloadTree();
      await get().reloadGraph();
      await get().openNoteById(response.note.metadata.id);
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async deleteNote(noteId) {
    try {
      await bridge.notes.remove(noteId);
      get().closeTab(noteId);
      await get().reloadTree();
      await get().reloadGraph();
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async restoreNote(entry) {
    try {
      const response = await bridge.notes.restore(entry);
      await get().reloadTree();
      await get().reloadGraph();
      await get().openNoteById(response.note.metadata.id);
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async createFolder(layerId, folderPath) {
    try {
      const existing = (get().tree?.folders ?? []).map((folder) => folder.name);
      await bridge.notes.createFolder(
        layerId,
        folderPath,
        uniqueTitle(existing, "New folder"),
      );
      await get().reloadTree();
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async renameFolder(folderId, name) {
    try {
      await bridge.notes.renameFolder(folderId, name);
      await get().reloadTree();
      await get().reloadGraph();
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async deleteFolder(folderId) {
    try {
      await bridge.notes.deleteFolder(folderId);
      await get().reloadTree();
      await get().reloadGraph();
    } catch (error) {
      set({ connectionMessage: describeError(error) });
    }
  },

  async attachFile(layerId, filename, base64) {
    const response = await bridge.notes.saveAttachment(
      layerId,
      filename,
      base64,
    );
    await get().reloadTree();
    return response.markdown;
  },

  // -- layers ---------------------------------------------------------------

  async createLayer(name, visibility, password, withRecoveryKey) {
    const response = await bridge.layers.create(
      name,
      visibility,
      password,
      withRecoveryKey,
    );
    await get().refreshLayers();
    // Returned to the caller to display once, and never written into the store.
    return response.recovery_key;
  },

  async unlockLayer(layerId, password) {
    await bridge.layers.unlock(layerId, password);
    await get().afterLockStateChanged();
  },

  async unlockLayerWithRecoveryKey(layerId, recoveryKey) {
    await bridge.layers.unlockWithRecoveryKey(layerId, recoveryKey);
    await get().afterLockStateChanged();
  },

  async lockLayer(layerId) {
    await bridge.layers.lock(layerId);
    get().forgetPrivateState(layerId);
    await get().afterLockStateChanged();
  },

  async lockAllLayers() {
    const locked = get()
      .layers.filter(
        (layer) => layer.visibility === "private" && layer.state === "unlocked",
      )
      .map((layer) => layer.id);
    await bridge.layers.lockAll();
    locked.forEach((layerId) => get().forgetPrivateState(layerId));
    await get().afterLockStateChanged();
  },

  /**
   * Drop everything in the *frontend* that came from a layer that just locked.
   *
   * Python forgets the key, but the tab that is open, the draft being typed, the
   * search results on screen and the selected nodes are all decrypted content
   * living in this process. Locking has to reach them too, or "locked" is a lie
   * the UI is telling.
   */
  forgetPrivateState: (layerId) => {
    const state = get();
    const fromLayer = new Set(
      (state.tree?.notes ?? [])
        .filter((note) => note.layer_id === layerId)
        .map((note) => note.id),
    );

    const openNoteWasPrivate =
      state.openNote?.metadata.layer_id === layerId ||
      (state.activeNoteId !== null && fromLayer.has(state.activeNoteId));

    const dirty = { ...state.dirty };
    for (const id of fromLayer) delete dirty[id];

    set({
      tabs: state.tabs.filter((tab) => !fromLayer.has(tab.id)),
      dirty,
      selectedIds: state.selectedIds.filter((id) => !fromLayer.has(id)),
      searchResults: state.searchResults.filter(
        (result) => result.layer_id !== layerId,
      ),
      plan: null,
      ...(openNoteWasPrivate
        ? {
            openNote: null,
            activeNoteId: null,
            draft: null,
            links: EMPTY_LINKS,
            issues: [],
            schemaId: null,
          }
        : {}),
    });
  },

  async refreshLayers() {
    const state = await bridge.workspace.getState();
    set({ workspace: state, layers: state.workspace?.layers ?? [] });
  },

  async afterLockStateChanged() {
    await get().refreshLayers();
    await get().reloadTree();
    await get().reloadGraph();
  },

  async changeLayerPassword(layerId, oldPassword, newPassword) {
    await bridge.layers.changePassword(layerId, oldPassword, newPassword);
  },

  async reissueRecoveryKey(layerId, password) {
    const response = await bridge.layers.reissueRecoveryKey(layerId, password);
    return response.recovery_key;
  },

  async rotateLayerKey(layerId, password) {
    const response = await bridge.layers.rotateKey(layerId, password);
    await get().afterLockStateChanged();
    return response.objects_reencrypted;
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

  async selectCluster(id) {
    try {
      const { node_ids } = await bridge.graph.clusterOf(id);
      get().selectMany(node_ids.length ? node_ids : [id]);
    } catch (error) {
      set({ planError: describeError(error) });
    }
  },

  // Connected component: everything reachable from the node, computed on the
  // client from the graph already in memory (no round trip needed).
  selectConnected: (id) => {
    const graph = get().graph;
    if (!graph) return;
    const adjacency = new Map<string, string[]>();
    for (const edge of graph.edges) {
      adjacency.set(edge.source, [
        ...(adjacency.get(edge.source) ?? []),
        edge.target,
      ]);
      adjacency.set(edge.target, [
        ...(adjacency.get(edge.target) ?? []),
        edge.source,
      ]);
    }
    const seen = new Set<string>([id]);
    const stack = [id];
    while (stack.length) {
      const current = stack.pop()!;
      for (const neighbour of adjacency.get(current) ?? []) {
        if (!seen.has(neighbour)) {
          seen.add(neighbour);
          stack.push(neighbour);
        }
      }
    }
    get().selectMany([...seen]);
  },

  async selectShortestPath(fromId, toId) {
    try {
      const { node_ids } = await bridge.graph.shortestPath(fromId, toId);
      if (node_ids.length) get().selectMany(node_ids);
    } catch (error) {
      set({ planError: describeError(error) });
    }
  },

  selectByTag: (tag) => {
    const graph = get().graph;
    if (!graph) return;
    const ids = graph.nodes
      .filter((node) => node.tags.includes(tag) && !node.locked)
      .map((node) => node.id);
    get().selectMany(ids);
  },

  selectByLayer: (layerId) => {
    const graph = get().graph;
    if (!graph) return;
    const ids = graph.nodes
      .filter((node) => node.layer_id === layerId && !node.locked)
      .map((node) => node.id);
    get().selectMany(ids);
  },

  setHovered: (id) => set({ hoveredId: id }),

  async setSemanticEdges(on) {
    set({ semanticEdges: on });
    await get().reloadGraph();
  },

  async setClusterColors(on) {
    set({ clusterColors: on });
    await get().reloadGraph();
  },

  async runSearch(query) {
    set({ searchQuery: query, searching: true });
    if (!query.trim()) {
      set({ searchResults: [], searching: false });
      return;
    }
    try {
      const response = await bridge.search.query(query, {
        semantic: get().semanticSearch,
        // Boost what is near the note being read: "what else is relevant to this"
        // rather than "what contains this string".
        near_object_id: get().activeNoteId,
      });
      set({ searchResults: response.results, searching: false });
    } catch (error) {
      set({
        searching: false,
        searchResults: [],
        connectionMessage: describeError(error),
      });
    }
  },

  async setSemanticSearch(enabled) {
    set({ semanticSearch: enabled });
    const query = get().searchQuery;
    if (query.trim()) await get().runSearch(query);
  },

  async findSimilar(objectId) {
    set({ searching: true });
    try {
      const response = await bridge.search.similar(objectId);
      set({
        searchResults: response.results,
        searching: false,
        searchQuery: "",
      });
    } catch (error) {
      set({ searching: false, connectionMessage: describeError(error) });
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

    // The plan changed what would be sent, so the policy verdict may have too.
    void get().refreshPolicy();
  },

  // -- AI request -----------------------------------------------------------

  async setProvider(providerId) {
    set({ providerId, model: "" });
    await get().refreshPolicy();
  },

  setModel: (model) => set({ model }),

  async refreshPolicy() {
    const { selectedIds, graph, providerId } = get();
    const selectable = selectedIds.filter((id) => isExportable(graph, id));
    try {
      const policy = await bridge.ai.checkPolicy(selectable, providerId);
      set({ policy });
    } catch (error) {
      set({ policy: null, aiError: describeError(error) });
    }
  },

  async storeCredential(providerId, apiKey) {
    const response = await bridge.ai.storeCredential(providerId, apiKey);
    // Re-read providers so the "configured" badges update.
    const info = await bridge.ai.providers();
    set({
      providers: info.providers,
      keychainAvailable: info.keychain_available,
    });
    return response.stored;
  },

  async sendToModel(confirmedRemote) {
    const {
      selectedIds,
      graph,
      prompt,
      providerId,
      model,
      depth,
      contentMode,
    } = get();
    const selectable = selectedIds.filter((id) => isExportable(graph, id));
    if (!prompt.trim()) {
      set({ aiError: "Write a prompt first." });
      return;
    }

    set({ aiOutput: "", aiError: null, aiStreaming: true, aiRequestId: null });
    try {
      const { request_id } = await bridge.ai.send({
        provider_id: providerId,
        // The CLI picks its own model; everything else needs one chosen.
        model: model || "default",
        object_ids: selectable,
        prompt,
        depth,
        content_mode: contentMode,
        confirmed_remote: confirmedRemote,
      });
      set({ aiRequestId: request_id });
    } catch (error) {
      // A denial or a missing key surfaces here, before any streaming starts.
      set({
        aiStreaming: false,
        aiError:
          error instanceof BridgeCallError
            ? error.message
            : "The request could not start.",
      });
    }
  },

  async cancelAIRequest() {
    const id = get().aiRequestId;
    if (!id) return;
    await bridge.ai.cancel(id);
    set({ aiStreaming: false });
    void get().loadReceipts();
  },

  clearAIOutput: () => set({ aiOutput: "", aiError: null }),

  async loadReceipts() {
    try {
      const { receipts } = await bridge.ai.receipts();
      set({ receipts });
    } catch {
      // Receipts are diagnostic; a failure to load them is not worth surfacing.
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
