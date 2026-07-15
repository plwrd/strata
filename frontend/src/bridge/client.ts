/**
 * The only way the frontend talks to Python.
 *
 * No `fetch`, no WebSocket, no `eval` — the CSP forbids all three anyway. Every
 * call goes through a registered WebChannel object and returns a validated
 * envelope. A failed call throws `BridgeCallError`, which carries the closed
 * error code so callers can branch on `layer_locked` vs `not_found` without
 * string-matching a message.
 */

import type {
  BridgeError,
  CollaborationState,
  ConflictRecord,
  ContentMode,
  ContextDepth,
  ContextPlan,
  ErrorCode,
  PresencePeer,
  ShareRole,
  ExportResult,
  ExportShape,
  ExportTarget,
  GraphSnapshot,
  HealthResponse,
  JobRecord,
  LayerDescriptor,
  LinkHealthResponse,
  LinksResponse,
  AppliedPlan,
  Note,
  NoteResponse,
  NoteSchema,
  OperationPlan,
  PlanReview,
  PolicyView,
  PrivacyReceipt,
  ProviderHealthView,
  ProviderView,
  SnapshotRecord,
  TrashEntry,
  TreeFolder,
  ViewConfig,
  ViewResult,
  ResponseEnvelope,
  SearchResponse,
  AppSettings,
  TreeResponse,
  WorkspaceState,
  WriteExportResponse,
} from "./types";
import { PROTOCOL_VERSION } from "./types";

export class BridgeCallError extends Error {
  readonly code: ErrorCode;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(error: BridgeError) {
    super(error.message);
    this.name = "BridgeCallError";
    this.code = error.code;
    this.retryable = error.retryable;
    this.details = error.details ?? {};
  }
}

export class BridgeUnavailableError extends Error {
  constructor(message = "The Strata bridge is not available.") {
    super(message);
    this.name = "BridgeUnavailableError";
  }
}

type SlotFn = (payload: string, callback: (response: string) => void) => void;
type BridgeObject = Record<
  string,
  SlotFn | { connect: (cb: (value: string) => void) => void }
>;

interface QWebChannelInstance {
  objects: Record<string, BridgeObject>;
}

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (
      transport: unknown,
      callback: (channel: QWebChannelInstance) => void,
    ) => QWebChannelInstance;
  }
}

let channelPromise: Promise<QWebChannelInstance> | null = null;

/**
 * Drop the memoised channel. **Tests only.**
 *
 * The channel is memoised because connecting twice over one transport corrupts the
 * WebChannel protocol (both channels receive every reply and route it through their
 * own callback table). That memoisation is module-level, so a test that swaps the
 * fake transport underneath it would otherwise keep talking to the previous one.
 */
export function __resetChannelForTests(): void {
  channelPromise = null;
}

function connect(): Promise<QWebChannelInstance> {
  if (channelPromise) return channelPromise;

  channelPromise = new Promise((resolve, reject) => {
    const transport = window.qt?.webChannelTransport;
    const QWebChannel = window.QWebChannel;
    if (!transport || !QWebChannel) {
      reject(
        new BridgeUnavailableError(
          "Strata must run inside the desktop application. Open it with `python -m app.main`.",
        ),
      );
      return;
    }
    new QWebChannel(transport, (channel) => resolve(channel));
  });

  return channelPromise;
}

let counter = 0;
function nextRequestId(): string {
  counter += 1;
  return `req_${Date.now().toString(36)}_${counter.toString(36)}`;
}

async function call<T>(
  objectName: string,
  method: string,
  payload: unknown = {},
): Promise<T> {
  const channel = await connect();
  const target = channel.objects[objectName];
  if (!target) {
    throw new BridgeUnavailableError(
      `Bridge object "${objectName}" is not registered.`,
    );
  }
  const slot = target[method];
  if (typeof slot !== "function") {
    throw new BridgeUnavailableError(
      `Bridge method "${objectName}.${method}" does not exist.`,
    );
  }

  const requestId = nextRequestId();
  const request = JSON.stringify({ v: PROTOCOL_VERSION, requestId, payload });

  const raw = await new Promise<string>((resolve) => {
    slot(request, resolve);
  });

  let envelope: ResponseEnvelope<T>;
  try {
    envelope = JSON.parse(raw) as ResponseEnvelope<T>;
  } catch {
    throw new BridgeCallError({
      code: "internal",
      message: "The bridge returned a malformed response.",
      retryable: false,
      details: {},
    });
  }

  if (envelope.requestId !== requestId) {
    // Qt delivers the reply to the callback that made the call, so this can only
    // mean the protocol is out of step. Fail loudly rather than trusting it.
    throw new BridgeCallError({
      code: "internal",
      message: "The bridge response did not match the request.",
      retryable: false,
      details: {},
    });
  }

  if (!envelope.ok || envelope.error) {
    throw new BridgeCallError(
      envelope.error ?? {
        code: "internal",
        message: "Unknown bridge failure.",
        retryable: false,
        details: {},
      },
    );
  }

  return envelope.data as T;
}

/**
 * Subscribe to a Qt Signal exposed on a bridge object.
 *
 * Signals are the *only* way Python pushes to the frontend; everything else is
 * request/response. Payloads are strings, and the callers below parse them —
 * there is no channel through which Python can hand the page an object it did not
 * ask for.
 */
async function subscribe(
  objectName: string,
  signalName: string,
  listener: (payload: string) => void,
): Promise<void> {
  const channel = await connect();
  const target = channel.objects[objectName];
  const signal = target?.[signalName];
  if (
    !signal ||
    typeof signal === "function" ||
    typeof signal.connect !== "function"
  ) {
    throw new BridgeUnavailableError(
      `Bridge signal "${objectName}.${signalName}" does not exist.`,
    );
  }
  signal.connect(listener);
}

export interface ExportRequest {
  object_ids: string[];
  prompt: string;
  target: ExportTarget;
  shape: ExportShape;
  depth: ContextDepth;
  content_mode: ContentMode;
  token_budget: number | null;
  acknowledge_private?: boolean;
}

export const bridge = {
  isAvailable(): boolean {
    return Boolean(window.qt?.webChannelTransport && window.QWebChannel);
  },

  workspace: {
    health: () => call<HealthResponse>("workspace", "health"),
    getState: () => call<WorkspaceState>("workspace", "get_state"),
    // No `path` parameter anywhere: Python decides the default location, and the
    // user picks any other one through a native dialog.
    openDefault: (name = "Strata") =>
      call<WorkspaceState>("workspace", "open_default_workspace", { name }),
    choose: (name = "Strata") =>
      call<WorkspaceState>("workspace", "choose_workspace", { name }),
    close: () => call<WorkspaceState>("workspace", "close_workspace"),
  },

  layers: {
    list: () =>
      call<{ layers: LayerDescriptor[]; layer_order: string[] }>(
        "layers",
        "list_layers",
      ),
    create: (
      display_name: string,
      visibility: "public" | "private" = "public",
      password: string | null = null,
      with_recovery_key = true,
    ) =>
      call<{ layer: LayerDescriptor; recovery_key: string | null }>(
        "layers",
        "create_layer",
        { display_name, visibility, password, with_recovery_key },
      ),
    rename: (layer_id: string, display_name: string) =>
      call<{ layer: LayerDescriptor }>("layers", "rename_layer", {
        layer_id,
        display_name,
      }),

    unlock: (layer_id: string, password: string) =>
      call<{ layer: LayerDescriptor }>("layers", "unlock_layer", {
        layer_id,
        password,
      }),
    unlockWithRecoveryKey: (layer_id: string, recovery_key: string) =>
      call<{ layer: LayerDescriptor }>("layers", "unlock_with_recovery_key", {
        layer_id,
        recovery_key,
      }),
    lock: (layer_id: string) =>
      call<{ layer: LayerDescriptor }>("layers", "lock_layer", { layer_id }),
    lockAll: () => call<{ locked: number }>("layers", "lock_all_layers"),

    changePassword: (
      layer_id: string,
      old_password: string,
      new_password: string,
    ) =>
      call<{ layer: LayerDescriptor }>("layers", "change_password", {
        layer_id,
        old_password,
        new_password,
      }),
    reissueRecoveryKey: (layer_id: string, password: string) =>
      call<{ recovery_key: string }>("layers", "reissue_recovery_key", {
        layer_id,
        password,
      }),
    rotateKey: (layer_id: string, password: string) =>
      call<{ objects_reencrypted: number; layer: LayerDescriptor }>(
        "layers",
        "rotate_key",
        { layer_id, password },
      ),
  },

  notes: {
    tree: (layer_ids: string[] | null = null) =>
      call<TreeResponse>("notes", "get_tree", { layer_ids }),
    get: (note_id: string) =>
      call<NoteResponse>("notes", "get_note", { note_id }),
    create: (
      layer_id: string,
      title: string,
      folder_path = "",
      content = "",
      schema_id: string | null = null,
    ) =>
      call<NoteResponse>("notes", "create_note", {
        layer_id,
        title,
        folder_path,
        content,
        schema_id,
      }),
    update: (note_id: string, content: string) =>
      call<NoteResponse>("notes", "update_note", { note_id, content }),
    updateProperties: (note_id: string, properties: Record<string, unknown>) =>
      call<NoteResponse>("notes", "update_properties", { note_id, properties }),
    rename: (note_id: string, title: string) =>
      call<{ note: Note; links_rewritten: number }>("notes", "rename_note", {
        note_id,
        title,
      }),
    move: (note_id: string, folder_path: string) =>
      call<NoteResponse>("notes", "move_note", { note_id, folder_path }),
    duplicate: (note_id: string) =>
      call<NoteResponse>("notes", "duplicate_note", { note_id }),
    remove: (note_id: string) =>
      call<{ trash_entry: string }>("notes", "delete_note", { note_id }),

    listTrash: () => call<{ entries: TrashEntry[] }>("notes", "list_trash"),
    restore: (entry: string) =>
      call<NoteResponse>("notes", "restore_note", { entry }),
    emptyTrash: () => call<{ count: number }>("notes", "empty_trash"),

    createFolder: (layer_id: string, folder_path: string, name: string) =>
      call<{ folder: TreeFolder }>("notes", "create_folder", {
        layer_id,
        folder_path,
        name,
      }),
    renameFolder: (folder_id: string, name: string) =>
      call<{ folder: TreeFolder }>("notes", "rename_folder", {
        folder_id,
        name,
      }),
    deleteFolder: (folder_id: string) =>
      call<{ count: number }>("notes", "delete_folder", { folder_id }),

    links: (note_id: string) =>
      call<LinksResponse>("notes", "get_links", { note_id }),
    linkHealth: () => call<LinkHealthResponse>("notes", "get_link_health"),
    schemas: () => call<{ schemas: NoteSchema[] }>("notes", "list_schemas"),

    saveAttachment: (layer_id: string, filename: string, data_base64: string) =>
      call<{ path: string; markdown: string }>("notes", "save_attachment", {
        layer_id,
        filename,
        data_base64,
      }),

    /** Push channel: the workspace changed on disk (by Strata, or externally). */
    onChanged: (listener: (origin: string) => void) =>
      subscribe("notes", "changed", listener),
  },

  graph: {
    load: (
      options: Partial<{
        layer_ids: string[] | null;
        include_tags: boolean;
        include_folders: boolean;
        semantic_edges: boolean;
        semantic_threshold: number;
        cluster: boolean;
        cluster_count: number;
      }> = {},
    ) => call<{ graph: GraphSnapshot }>("graph", "load_graph", options),
    neighbours: (node_id: string) =>
      call<{ node_ids: string[] }>("graph", "expand_neighbours", { node_id }),
    shortestPath: (source_id: string, target_id: string) =>
      call<{ node_ids: string[] }>("graph", "shortest_path", {
        source_id,
        target_id,
      }),
    clusterOf: (node_id: string) =>
      call<{ node_ids: string[] }>("graph", "cluster_of", { node_id }),
  },

  search: {
    query: (
      query: string,
      options: {
        limit?: number;
        semantic?: boolean;
        near_object_id?: string | null;
        tags?: string[] | null;
        layer_ids?: string[] | null;
      } = {},
    ) =>
      call<SearchResponse>("search", "search", {
        query,
        limit: options.limit ?? 50,
        semantic: options.semantic ?? true,
        near_object_id: options.near_object_id ?? null,
        tags: options.tags ?? null,
        layer_ids: options.layer_ids ?? null,
      }),
    similar: (object_id: string, limit = 10) =>
      call<SearchResponse>("search", "similar", { object_id, limit }),
    clusters: (count = 6) =>
      call<{ clusters: Record<string, number> }>("search", "clusters", {
        count,
      }),
  },

  ai: {
    providers: () =>
      call<{
        providers: ProviderView[];
        any_configured: boolean;
        keychain_available: boolean;
      }>("ai", "list_providers"),
    health: (provider_id: string) =>
      call<ProviderHealthView>("ai", "check_health", { provider_id }),
    storeCredential: (provider_id: string, api_key: string) =>
      call<{ stored: boolean; keychain_available: boolean; detail: string }>(
        "ai",
        "store_credential",
        { provider_id, api_key },
      ),
    deleteCredential: (provider_id: string) =>
      call<{ stored: boolean; detail: string }>("ai", "delete_credential", {
        provider_id,
      }),
    planContext: (request: Omit<ExportRequest, "acknowledge_private">) =>
      call<{ plan: ContextPlan }>("ai", "plan_context", request),
    checkPolicy: (object_ids: string[], provider_id: string) =>
      call<PolicyView>("ai", "check_policy", { object_ids, provider_id }),
    route: (object_ids: string[], required_tokens = 0) =>
      call<{ provider_id: string | null; reason: string }>("ai", "route", {
        object_ids,
        required_tokens,
      }),
    send: (request: {
      provider_id: string;
      model: string;
      object_ids: string[];
      prompt: string;
      depth?: ContextDepth;
      content_mode?: ContentMode;
      max_output_tokens?: number;
      confirmed_remote?: boolean;
    }) => call<{ request_id: string }>("ai", "send_request", request),
    cancel: (request_id: string) =>
      call<{ cancelled: boolean }>("ai", "cancel_request", { request_id }),
    receipts: () =>
      call<{ receipts: PrivacyReceipt[] }>("ai", "privacy_receipts"),
    onEvent: (listener: (payload: string) => void) =>
      subscribe("ai", "aiEvent", listener),
  },

  export: {
    render: (request: ExportRequest) =>
      call<{ result: ExportResult }>("export", "render_export", request),
    write: (request: ExportRequest) =>
      call<WriteExportResponse>("export", "write_export", request),
  },

  settings: {
    get: () => call<{ settings: AppSettings }>("settings", "get_settings"),
    update: (values: Partial<AppSettings>) =>
      call<{ settings: AppSettings }>("settings", "update_settings", {
        values,
      }),
  },

  operations: {
    generate: (request: {
      provider_id: string;
      model: string;
      prompt: string;
      object_ids: string[];
      layer_ids: string[];
      confirmed_remote?: boolean;
    }) => call<{ request_id: string }>("operations", "generate_plan", request),
    review: (plan: OperationPlan, allowed_layer_ids: string[]) =>
      call<{ review: PlanReview }>("operations", "review_plan", {
        plan,
        allowed_layer_ids,
      }),
    apply: (
      plan: OperationPlan,
      approved_indexes: number[],
      allowed_layer_ids: string[],
    ) =>
      call<{ applied: AppliedPlan }>("operations", "apply_plan", {
        plan,
        approved_indexes,
        allowed_layer_ids,
      }),
    undo: (plan_id: string) =>
      call<{ applied: AppliedPlan }>("operations", "undo_plan", { plan_id }),
    auditLog: () => call<{ entries: AppliedPlan[] }>("operations", "audit_log"),
    onPlan: (listener: (payload: string) => void) =>
      subscribe("operations", "planEvent", listener),
  },

  snapshots: {
    list: () =>
      call<{ snapshots: SnapshotRecord[] }>("snapshots", "list_snapshots"),
    create: (name: string) =>
      call<{ snapshot: SnapshotRecord }>("snapshots", "create_snapshot", {
        name,
      }),
    restore: (snapshot_id: string) =>
      call<{ snapshot: SnapshotRecord }>("snapshots", "restore_snapshot", {
        snapshot_id,
      }),
    remove: (snapshot_id: string) =>
      call<{ deleted: boolean }>("snapshots", "delete_snapshot", {
        snapshot_id,
      }),
  },

  views: {
    run: (config: ViewConfig) =>
      call<{ result: ViewResult }>("views", "run_view", { config }),
    listSaved: () => call<{ views: ViewConfig[] }>("views", "list_saved_views"),
    save: (view: ViewConfig) =>
      call<{ view: ViewConfig }>("views", "save_view", { view }),
    remove: (view_id: string) =>
      call<{ deleted: boolean }>("views", "delete_view", { view_id }),
  },

  jobs: {
    list: () => call<{ jobs: JobRecord[] }>("jobs", "list_jobs"),
    cancel: (job_id: string) =>
      call<{ cancelled: boolean }>("jobs", "cancel_job", { job_id }),
  },

  collaboration: {
    status: (layer_id: string) =>
      call<{ state: CollaborationState }>("collaboration", "get_status", {
        layer_id,
      }),
    // The full authoritative document as a base64 Yjs update, for the renderer's
    // client Doc to load (idempotent to re-apply).
    getDocument: (layer_id: string) =>
      call<{ update: string }>("collaboration", "get_document", { layer_id }),
    // A base64 Yjs update the renderer's editor produced.
    applyUpdate: (layer_id: string, update: string) =>
      call<{ state: CollaborationState; conflicts: ConflictRecord[] }>(
        "collaboration",
        "apply_update",
        { layer_id, update },
      ),
    // Remote changes, conflicts, and presence updates are pushed here.
    onEvent: (listener: (payload: string) => void) =>
      subscribe("collaboration", "collabEvent", listener),
    share: (layer_id: string, role: ShareRole = "owner") =>
      call<{ state: CollaborationState }>("collaboration", "share_layer", {
        layer_id,
        role,
      }),
    join: (layer_id: string, doc_id: string, role: ShareRole = "editor") =>
      call<{ state: CollaborationState }>("collaboration", "join_layer", {
        layer_id,
        doc_id,
        role,
      }),
    leave: (layer_id: string) =>
      call<{ state: CollaborationState }>("collaboration", "leave_layer", {
        layer_id,
      }),
    sync: (layer_id: string) =>
      call<{ state: CollaborationState; conflicts: ConflictRecord[] }>(
        "collaboration",
        "sync",
        { layer_id },
      ),
    listConflicts: (layer_id: string) =>
      call<{ state: CollaborationState; conflicts: ConflictRecord[] }>(
        "collaboration",
        "list_conflicts",
        { layer_id },
      ),
    resolveConflict: (
      layer_id: string,
      conflict_id: string,
      action: "keep" | "confirm_delete",
    ) =>
      call<{ state: CollaborationState }>("collaboration", "resolve_conflict", {
        layer_id,
        conflict_id,
        action,
      }),
    presence: (layer_id: string) =>
      call<{ peers: PresencePeer[] }>("collaboration", "get_presence", {
        layer_id,
      }),
    announce: (layer_id: string, peer: PresencePeer) =>
      call<{ peers: PresencePeer[] }>("collaboration", "announce_presence", {
        layer_id,
        peer,
      }),
    compact: (layer_id: string) =>
      call<{ reclaimed: number }>("collaboration", "compact", { layer_id }),
  },
};

export type Bridge = typeof bridge;
