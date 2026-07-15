/**
 * A fake WebChannel transport, for tests only.
 *
 * This exists so the frontend can be tested without Qt. It is *not* a development
 * mode: `npm run dev` points the real desktop shell at the Vite server, so dev
 * still talks to real Python. A mock that ships in the product is a lie the
 * product tells itself.
 *
 * The fake speaks the same envelope protocol as `app/bridge/envelope.py`,
 * including the error shape, so a test can exercise the client's failure paths.
 */

import { __resetChannelForTests } from "../bridge/client";
import type {
  ContextPlan,
  GraphSnapshot,
  LayerDescriptor,
  Note,
  NoteMetadata,
  PlanReview,
} from "../bridge/types";

type Handler = (payload: Record<string, unknown>) => unknown;
type Signal = { connect: (listener: (value: string) => void) => void };

export interface FakeBridgeOptions {
  graph?: GraphSnapshot;
  plan?: ContextPlan;
  review?: PlanReview;
  failWith?: { code: string; message: string };
  /** Receives the raw request envelope, so a test can assert on the wire format. */
  onRequest?: (objectName: string, method: string, raw: string) => void;
}

export const SAMPLE_GRAPH: GraphSnapshot = {
  nodes: [
    node("n1", "Encryption Architecture", "concept", 3),
    node("n2", "Threat Model", "concept", 2),
    node("n3", "Marketing Claims", "decision", 1),
    node("n4", "Knowledge Graph", "note", 1),
    {
      ...node("locked:layer_p", "Locked knowledge object", "note", 0),
      locked: true,
      layer_id: "layer_p",
    },
  ],
  edges: [
    edge("e1", "n1", "n2", "depends_on"),
    edge("e2", "n3", "n2", "contradicts"),
    edge("e3", "n1", "n4", "references"),
  ],
  truncated: false,
  total_nodes: 5,
  total_edges: 3,
  locked_layer_ids: ["layer_p"],
};

function node(
  id: string,
  label: string,
  type: GraphSnapshot["nodes"][number]["type"],
  degree: number,
): GraphSnapshot["nodes"][number] {
  return {
    id,
    layer_id: "layer_a",
    type,
    label,
    locked: false,
    folder_path: "Security",
    tags: ["security"],
    degree,
    updated_at: "2026-07-14T10:00:00+00:00",
    word_count: 120,
    cluster: -1,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  relationship: string,
): GraphSnapshot["edges"][number] {
  return {
    id,
    source,
    target,
    type: "relationship",
    relationship,
    origin: "explicit",
    confidence: null,
    weight: 1,
  };
}

export function planFor(objectIds: string[], prompt = ""): ContextPlan {
  const sources = objectIds.map((objectId, index) => {
    const found = SAMPLE_GRAPH.nodes.find(
      (candidate) => candidate.id === objectId,
    );
    return {
      source_id: `STRATA-SOURCE-${String(index + 1).padStart(3, "0")}`,
      object_id: objectId,
      layer_id: "layer_a",
      layer_name: "Knowledge",
      is_private: false,
      title: found?.label ?? objectId,
      path: `Security/${found?.label ?? objectId}.md`,
      tags: ["security"],
      properties: {},
      updated_at: "2026-07-14T10:00:00+00:00",
      content: "Body text.",
      truncated: false,
    };
  });

  return {
    export_id: "exp_test",
    target: "generic",
    shape: "single-file",
    depth: "selected-only",
    content_mode: "full",
    prompt,
    workspace_name: "Test",
    created_at: "2026-07-14T10:00:00+00:00",
    sources,
    relationships: [],
    excluded_locked_count: 0,
    private_source_count: 0,
    private_layer_names: [],
    estimated_tokens: sources.length * 40,
    token_budget: null,
    part_count: 1,
    warnings: [],
  };
}

export const PUBLIC_LAYER: LayerDescriptor = {
  id: "layer_a",
  display_name: "Knowledge",
  visibility: "public",
  state: "mounted",
  sharing_mode: "personal",
  storage: "markdown",
  storage_version: 1,
  created_at: "",
  updated_at: "",
  color: "layer-public",
  ai_policy: {} as LayerDescriptor["ai_policy"],
};

export const PRIVATE_LAYER: LayerDescriptor = {
  id: "layer_p",
  display_name: "Deals",
  visibility: "private",
  state: "locked",
  sharing_mode: "personal",
  storage: "encrypted-objects",
  storage_version: 1,
  created_at: "",
  updated_at: "",
  color: "layer-private",
  ai_policy: {} as LayerDescriptor["ai_policy"],
};

export const FAKE_RECOVERY_KEY = "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG";

/** Note bodies the fake bridge was asked to persist, for autosave assertions. */
export const saved: string[] = [];

/** Listeners registered against the `notes.changed` signal. */
export const changeListeners: ((value: string) => void)[] = [];

/** Listeners registered against the `ai.aiEvent` signal. */
export const aiListeners: ((value: string) => void)[] = [];

/** Listeners registered against the `operations.planEvent` signal. */
export const planListeners: ((value: string) => void)[] = [];

/** Fire an AI stream event the way Python would. */
export function emitAIEvent(payload: Record<string, unknown>): void {
  const raw = JSON.stringify(payload);
  for (const listener of aiListeners) listener(raw);
}

/** Fire a plan-generation event the way Python would. */
export function emitPlanEvent(payload: Record<string, unknown>): void {
  const raw = JSON.stringify(payload);
  for (const listener of planListeners) listener(raw);
}

/** Fire the external-change signal the way the file watcher would. */
export function emitChanged(origin: "strata" | "external"): void {
  for (const listener of changeListeners) listener(origin);
}

function noteMeta(id: string, title: string): NoteMetadata {
  return {
    id,
    layer_id: "layer_a",
    parent_id: null,
    title,
    folder_path: "Security",
    aliases: [],
    tags: ["security"],
    properties: { type: "decision", status: "proposed" },
    links: [],
    created_at: "2026-07-14T10:00:00+00:00",
    updated_at: "2026-07-14T10:00:00+00:00",
    size_bytes: 100,
    word_count: 20,
  };
}

function fakeNote(
  id: string,
  content = "# Body\n\nLinks to [[Threat Model]].\n",
  title?: string,
): Note {
  const found = SAMPLE_GRAPH.nodes.find((node) => node.id === id);
  return {
    metadata: noteMeta(id, title ?? found?.label ?? "Untitled"),
    content,
  };
}

export function installFakeBridge(options: FakeBridgeOptions = {}): void {
  const graph = options.graph ?? SAMPLE_GRAPH;
  saved.length = 0;
  changeListeners.length = 0;
  aiListeners.length = 0;
  planListeners.length = 0;
  // The client memoises its channel, so a fresh fake must invalidate it or the
  // client keeps talking to the previous test's transport.
  __resetChannelForTests();

  // The fake tracks lock state, because the store re-reads it after every
  // lock/unlock — a fake that always answered "locked" would make an unlock look
  // like it failed.
  let privateState: LayerDescriptor["state"] = PRIVATE_LAYER.state;
  const privateLayer = (): LayerDescriptor => ({
    ...PRIVATE_LAYER,
    state: privateState,
  });

  const handlers: Record<string, Record<string, Handler | Signal>> = {
    workspace: {
      health: () => ({
        ok: true,
        app: "strata",
        version: "0.1.0",
        protocol_version: 1,
        environment: "test",
        python_version: "3.10.11",
        qt_version: "6.8.1",
        workspace_open: true,
      }),
      open_default_workspace: () => ({
        is_open: true,
        workspace: {
          format_version: 1,
          id: "ws_1",
          name: "Test",
          created_at: "",
          updated_at: "",
          layer_order: ["layer_a"],
          layers: [
            {
              id: "layer_a",
              display_name: "Knowledge",
              visibility: "public",
              state: "mounted",
              sharing_mode: "personal",
              storage: "markdown",
              storage_version: 1,
              created_at: "",
              updated_at: "",
              color: "layer-public",
              ai_policy: {},
            },
          ],
          lenses: [],
        },
        lenses: [],
      }),
      get_state: () => ({
        is_open: true,
        workspace: {
          format_version: 1,
          id: "ws_1",
          name: "Test",
          created_at: "",
          updated_at: "",
          layer_order: [PUBLIC_LAYER.id, PRIVATE_LAYER.id],
          layers: [PUBLIC_LAYER, privateLayer()],
          lenses: [],
        },
        lenses: [],
      }),
    },
    settings: {
      get_settings: () => ({
        settings: {
          format_version: 1,
          appearance: "cyberpunk-dark",
          motion: "system",
          graph_quality: "balanced",
          particles_enabled: true,
          bloom_enabled: true,
          battery_saver: false,
          telemetry_enabled: false,
          default_lens_id: "lens_all",
          last_workspace_path: "",
          developer_tools: false,
        },
      }),
      update_settings: (payload) => ({
        settings: {
          format_version: 1,
          appearance: "cyberpunk-dark",
          motion: "system",
          graph_quality: "balanced",
          particles_enabled: true,
          bloom_enabled: true,
          battery_saver: false,
          telemetry_enabled: false,
          default_lens_id: "lens_all",
          last_workspace_path: "",
          developer_tools: false,
          ...(payload["values"] as object),
        },
      }),
    },
    graph: {
      load_graph: () => ({ graph }),
      expand_neighbours: (payload) => ({
        node_ids: graph.edges
          .filter(
            (e) =>
              e.source === payload["node_id"] ||
              e.target === payload["node_id"],
          )
          .map((e) => (e.source === payload["node_id"] ? e.target : e.source)),
      }),
      shortest_path: (payload) => ({
        node_ids: [payload["source_id"], "n1", payload["target_id"]],
      }),
      cluster_of: (payload) => ({
        node_ids: [payload["node_id"], "n2"],
      }),
    },
    notes: {
      get_tree: () => ({
        folders: [
          {
            id: "f1",
            layer_id: "layer_a",
            name: "Security",
            path: "Security",
            parent_id: null,
          },
        ],
        notes: [
          noteMeta("n1", "Encryption Architecture"),
          noteMeta("n2", "Threat Model"),
        ],
        locked_layer_ids: ["layer_p"],
      }),
      get_note: (payload) => ({
        note: fakeNote(payload["note_id"] as string),
        schema_id: "decision",
        issues: [],
      }),
      update_note: (payload) => {
        saved.push(payload["content"] as string);
        return {
          note: fakeNote(
            payload["note_id"] as string,
            payload["content"] as string,
          ),
          schema_id: "decision",
          issues: [],
        };
      },
      update_properties: (payload) => ({
        note: fakeNote(payload["note_id"] as string),
        schema_id: "decision",
        issues: [],
      }),
      create_note: () => ({
        note: fakeNote("n9"),
        schema_id: null,
        issues: [],
      }),
      rename_note: (payload) => ({
        note: fakeNote("n1-renamed", "body", payload["title"] as string),
        links_rewritten: 2,
      }),
      move_note: () => ({
        note: fakeNote("n1-moved"),
        schema_id: null,
        issues: [],
      }),
      duplicate_note: () => ({
        note: fakeNote("n1-copy"),
        schema_id: null,
        issues: [],
      }),
      delete_note: () => ({
        trash_entry: "layer_a__Security__Threat Model.md",
      }),
      list_trash: () => ({ entries: [] }),
      restore_note: () => ({
        note: fakeNote("n2"),
        schema_id: null,
        issues: [],
      }),
      empty_trash: () => ({ count: 0 }),
      create_folder: () => ({
        folder: {
          id: "f2",
          layer_id: "layer_a",
          name: "New folder",
          path: "New folder",
          parent_id: null,
        },
      }),
      rename_folder: () => ({
        folder: {
          id: "f1",
          layer_id: "layer_a",
          name: "Renamed",
          path: "Renamed",
          parent_id: null,
        },
      }),
      delete_folder: () => ({ count: 1 }),
      get_links: () => ({
        backlinks: [
          {
            source_id: "n2",
            source_title: "Threat Model",
            layer_id: "layer_a",
            relationship: "contradicts",
            context: "…the threat model says…",
          },
        ],
        unlinked_mentions: [
          {
            source_id: "n4",
            source_title: "Knowledge Graph",
            layer_id: "layer_a",
            context: "…mentions encryption architecture in prose…",
          },
        ],
        outgoing: [{ target: "Threat Model", relationship: "depends_on" }],
      }),
      get_link_health: () => ({
        broken: [{ source_id: "n1", target: "Nowhere" }],
        orphans: [],
      }),
      list_schemas: () => ({
        schemas: [
          {
            id: "decision",
            name: "Decision record",
            icon: "◇",
            node_style: "decision",
            builtin: true,
            template: "",
            allowed_relationships: [],
            properties: [
              {
                key: "status",
                label: "Status",
                type: "status",
                required: true,
                default: "proposed",
                options: ["proposed", "accepted", "rejected"],
                minimum: null,
                maximum: null,
                formula: "",
                description: "",
              },
            ],
          },
        ],
      }),
      save_attachment: () => ({
        path: "attachments/a.png",
        markdown: "![a.png](attachments/a.png)",
      }),
      changed: {
        connect: (listener: (value: string) => void) =>
          changeListeners.push(listener),
      },
    },
    search: {
      search: () => ({ results: [], total: 0, locked_layers_excluded: 1 }),
    },
    operations: {
      generate_plan: () => ({ request_id: "req_plan_1" }),
      review_plan: (payload) => ({
        review: options.review ?? {
          plan: payload["plan"],
          entries: [
            {
              index: 0,
              type: "create_note",
              layer_id: "layer_a",
              layer_name: "Knowledge",
              is_private: false,
              is_destructive: false,
              title: "Proposed Note",
              summary: "create note: Proposed Note",
              rationale: "capture the idea",
              before: "",
              after: "# Proposed Note",
              valid: true,
              problem: "",
            },
            {
              index: 1,
              type: "delete_note",
              layer_id: "layer_a",
              layer_name: "Knowledge",
              is_private: false,
              is_destructive: true,
              title: "Old Note",
              summary: "delete note: Old Note",
              rationale: "no longer needed",
              before: "Old Note",
              after: "",
              valid: true,
              problem: "",
            },
          ],
          valid_count: 2,
          invalid_count: 0,
          destructive_count: 1,
          private_layers_touched: [],
          warnings: ["1 operation(s) change or remove existing content."],
        },
      }),
      apply_plan: () => ({
        applied: {
          plan_id: "plan_test",
          snapshot_id: "snap_1",
          applied_at: "",
          results: [],
          summary: "Proposed plan",
          provider: "ollama",
          model: "llama3",
          prompt: "",
          undone: false,
        },
      }),
      undo_plan: () => ({
        applied: {
          plan_id: "plan_test",
          snapshot_id: "snap_1",
          applied_at: "",
          results: [],
          summary: "Proposed plan",
          provider: "ollama",
          model: "llama3",
          prompt: "",
          undone: true,
        },
      }),
      audit_log: () => ({ entries: [] }),
      planEvent: {
        connect: (listener: (value: string) => void) =>
          planListeners.push(listener),
      },
    },
    views: {
      run_view: (payload) => ({
        result: {
          config: payload["config"],
          rows: [
            {
              object_id: "n1",
              layer_id: "layer_a",
              layer_name: "Knowledge",
              is_private: false,
              title: "Alpha",
              folder_path: "Projects",
              tags: ["security"],
              properties: { status: "in progress", priority: "3" },
              created_at: "2026-07-14T10:00:00+00:00",
              updated_at: "2026-07-14T10:00:00+00:00",
              snippet: "Alpha body",
            },
            {
              object_id: "n2",
              layer_id: "layer_a",
              layer_name: "Knowledge",
              is_private: false,
              title: "Beta",
              folder_path: "Projects",
              tags: [],
              properties: { status: "done", priority: "1" },
              created_at: "2026-07-14T10:00:00+00:00",
              updated_at: "2026-07-14T10:00:00+00:00",
              snippet: "Beta body",
            },
          ],
          groups:
            (payload["config"] as { group_by?: string }).group_by === "status"
              ? [
                  {
                    key: "in progress",
                    label: "in progress",
                    rows: [
                      {
                        object_id: "n1",
                        layer_id: "layer_a",
                        layer_name: "Knowledge",
                        is_private: false,
                        title: "Alpha",
                        folder_path: "Projects",
                        tags: ["security"],
                        properties: { status: "in progress" },
                        created_at: "",
                        updated_at: "",
                        snippet: "Alpha body",
                      },
                    ],
                  },
                  {
                    key: "done",
                    label: "done",
                    rows: [
                      {
                        object_id: "n2",
                        layer_id: "layer_a",
                        layer_name: "Knowledge",
                        is_private: false,
                        title: "Beta",
                        folder_path: "Projects",
                        tags: [],
                        properties: { status: "done" },
                        created_at: "",
                        updated_at: "",
                        snippet: "Beta body",
                      },
                    ],
                  },
                ]
              : [],
          total: 2,
          available_properties: ["priority", "status", "tags", "title"],
          locked_layers_excluded: 0,
        },
      }),
      list_saved_views: () => ({ views: [] }),
      save_view: (payload) => ({ view: payload["view"] }),
      delete_view: () => ({ deleted: true }),
    },
    snapshots: {
      list_snapshots: () => ({ snapshots: [] }),
      create_snapshot: (payload) => ({
        snapshot: {
          id: "snap_new",
          name: payload["name"] as string,
          created_at: "",
          kind: "manual",
          layer_count: 1,
          note_count: 5,
        },
      }),
      restore_snapshot: () => ({
        snapshot: {
          id: "snap_new",
          name: "Checkpoint",
          created_at: "",
          kind: "manual",
          layer_count: 1,
          note_count: 5,
        },
      }),
      delete_snapshot: () => ({ deleted: true }),
    },
    layers: {
      list_layers: () => ({
        layers: [PUBLIC_LAYER, privateLayer()],
        layer_order: [PUBLIC_LAYER.id, PRIVATE_LAYER.id],
      }),
      create_layer: (payload) => ({
        layer: {
          ...(payload["visibility"] === "private"
            ? PRIVATE_LAYER
            : PUBLIC_LAYER),
          id: "layer_new",
          display_name: payload["display_name"] as string,
          state: payload["visibility"] === "private" ? "unlocked" : "mounted",
        },
        recovery_key:
          payload["visibility"] === "private" &&
          payload["with_recovery_key"] !== false
            ? FAKE_RECOVERY_KEY
            : null,
      }),
      unlock_layer: () => {
        privateState = "unlocked";
        return { layer: privateLayer() };
      },
      unlock_with_recovery_key: () => {
        privateState = "unlocked";
        return { layer: privateLayer() };
      },
      lock_layer: () => {
        privateState = "locked";
        return { layer: privateLayer() };
      },
      lock_all_layers: () => {
        privateState = "locked";
        return { locked: 1 };
      },
      change_password: () => ({ layer: privateLayer() }),
      reissue_recovery_key: () => ({ recovery_key: FAKE_RECOVERY_KEY }),
      rotate_key: () => ({ objects_reencrypted: 12, layer: privateLayer() }),
    },
    ai: {
      list_providers: () => ({
        providers: [
          {
            provider_id: "ollama",
            display_name: "Ollama",
            is_local: true,
            configured: true,
            requires_api_key: false,
            capabilities: ["text", "streaming", "embeddings"],
            max_context_tokens: 32768,
            note: "Runs on this machine. Nothing leaves it.",
          },
        ],
        any_configured: true,
        keychain_available: true,
      }),
      check_health: () => ({
        provider_id: "ollama",
        reachable: true,
        configured: true,
        detail: "1 model available.",
        models: [
          {
            id: "llama3",
            display_name: "llama3",
            context_tokens: 32768,
            is_local: true,
          },
        ],
      }),
      store_credential: () => ({
        stored: true,
        keychain_available: true,
        detail: "Stored in the system keychain.",
      }),
      delete_credential: () => ({ stored: false, detail: "Removed." }),
      check_policy: () => ({
        verdict: "allowed",
        reason: "This provider runs on your machine.",
        blocking_layers: [],
        is_remote: false,
        private_object_count: 0,
        object_count: 1,
      }),
      route: () => ({
        provider_id: "ollama",
        reason: "Ollama runs on this machine.",
      }),
      send_request: () => ({ request_id: "req_ai_1" }),
      cancel_request: () => ({ cancelled: true }),
      privacy_receipts: () => ({ receipts: [] }),
      aiEvent: {
        connect: (listener: (value: string) => void) =>
          aiListeners.push(listener),
      },
      plan_context: (payload) => ({
        plan:
          options.plan ??
          planFor(
            payload["object_ids"] as string[],
            (payload["prompt"] as string) ?? "",
          ),
      }),
    },
    export: {
      render_export: (payload) => ({
        result: {
          export_id: "exp_test",
          target: "generic",
          shape: "single-file",
          parts: [
            {
              filename: "strata-context.md",
              content: `# User Prompt\n\n${payload["prompt"] as string}\n`,
              source_ids: [],
              estimated_tokens: 10,
            },
          ],
          manifest: {},
          estimated_tokens: 10,
          private_source_count: 0,
          warnings: [],
        },
      }),
      write_export: () => ({
        files_written: 1,
        directory_name: "Exports",
        export_id: "exp_test",
        private_source_count: 0,
      }),
    },
  };

  const objects: Record<string, Record<string, unknown>> = {};
  for (const [objectName, methods] of Object.entries(handlers)) {
    const target: Record<string, unknown> = {};
    for (const [methodName, handler] of Object.entries(methods)) {
      // Qt Signals appear on the channel as objects with `.connect`, not as
      // callable slots, so they pass straight through.
      if (typeof handler !== "function") {
        target[methodName] = handler;
        continue;
      }
      target[methodName] = (
        raw: string,
        callback: (response: string) => void,
      ) => {
        options.onRequest?.(objectName, methodName, raw);
        const request = JSON.parse(raw) as {
          requestId: string;
          payload: Record<string, unknown>;
        };
        if (options.failWith) {
          callback(
            JSON.stringify({
              v: 1,
              requestId: request.requestId,
              ok: false,
              error: { ...options.failWith, retryable: false, details: {} },
            }),
          );
          return;
        }
        callback(
          JSON.stringify({
            v: 1,
            requestId: request.requestId,
            ok: true,
            data: handler(request.payload),
          }),
        );
      };
    }
    objects[objectName] = target;
  }

  window.qt = { webChannelTransport: {} };
  window.QWebChannel = function (
    _transport: unknown,
    callback: (channel: { objects: typeof objects }) => void,
  ) {
    callback({ objects });
    return { objects };
  } as unknown as typeof window.QWebChannel;
}
