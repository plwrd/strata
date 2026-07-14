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
  ContentMode,
  ContextDepth,
  ContextPlan,
  ErrorCode,
  ExportResult,
  ExportShape,
  ExportTarget,
  GraphSnapshot,
  HealthResponse,
  JobRecord,
  LayerDescriptor,
  Note,
  ProviderCapability,
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
    ) =>
      call<{ layer: LayerDescriptor }>("layers", "create_layer", {
        display_name,
        visibility,
      }),
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
  },

  notes: {
    tree: (layer_ids: string[] | null = null) =>
      call<TreeResponse>("notes", "get_tree", { layer_ids }),
    get: (note_id: string) =>
      call<{ note: Note }>("notes", "get_note", { note_id }),
    create: (layer_id: string, title: string, folder_path = "", content = "") =>
      call<{ note: Note }>("notes", "create_note", {
        layer_id,
        title,
        folder_path,
        content,
      }),
  },

  graph: {
    load: (
      options: Partial<{
        layer_ids: string[] | null;
        include_tags: boolean;
        include_folders: boolean;
      }> = {},
    ) => call<{ graph: GraphSnapshot }>("graph", "load_graph", options),
    neighbours: (node_id: string) =>
      call<{ node_ids: string[] }>("graph", "expand_neighbours", { node_id }),
  },

  search: {
    query: (query: string, limit = 50) =>
      call<SearchResponse>("search", "search", { query, limit }),
  },

  ai: {
    providers: () =>
      call<{ providers: ProviderCapability[]; any_configured: boolean }>(
        "ai",
        "list_providers",
      ),
    planContext: (request: Omit<ExportRequest, "acknowledge_private">) =>
      call<{ plan: ContextPlan }>("ai", "plan_context", request),
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

  jobs: {
    list: () => call<{ jobs: JobRecord[] }>("jobs", "list_jobs"),
    cancel: (job_id: string) =>
      call<{ cancelled: boolean }>("jobs", "cancel_job", { job_id }),
  },

  collaboration: {
    status: () =>
      call<{
        mode: string;
        enabled: boolean;
        peers_online: number;
        note: string;
      }>("collaboration", "get_status"),
  },
};

export type Bridge = typeof bridge;
