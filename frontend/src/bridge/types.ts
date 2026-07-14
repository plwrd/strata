/**
 * The wire types. These mirror the Pydantic models in `app/bridge` exactly; when
 * one side changes, the other fails to compile or fails validation — which is the
 * point of keeping them boringly duplicated rather than generated at runtime.
 */

export const PROTOCOL_VERSION = 1;

export type ErrorCode =
  | "invalid_request"
  | "payload_too_large"
  | "not_found"
  | "permission_denied"
  | "layer_locked"
  | "conflict"
  | "unsupported"
  | "cancelled"
  | "provider_error"
  | "internal";

export interface BridgeError {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface RequestEnvelope {
  v: number;
  requestId: string;
  payload: unknown;
}

export interface ResponseEnvelope<T> {
  v: number;
  requestId: string;
  ok: boolean;
  data?: T;
  error?: BridgeError;
}

// --- domain ---------------------------------------------------------------

export type LayerVisibility = "public" | "private";
export type LayerState = "mounted" | "unmounted" | "locked" | "unlocked";

export interface LayerAIPolicy {
  access:
    | "disabled"
    | "local-only"
    | "remote-with-confirmation"
    | "remote-always";
  embeddings: "disabled" | "local-only" | "remote-allowed";
  may_read: boolean;
  may_summarize: boolean;
  may_propose_edits: boolean;
  may_apply_approved_edits: boolean;
  may_create_links: boolean;
  may_reorganize_structure: boolean;
  may_process_attachments: boolean;
}

/** How a layer's bytes are kept. Not the same axis as visibility. */
export type LayerStorage = "markdown" | "encrypted-objects";

export interface LayerDescriptor {
  id: string;
  display_name: string;
  visibility: LayerVisibility;
  state: LayerState;
  sharing_mode: "personal" | "shared-password" | "identity-managed";
  storage: LayerStorage;
  storage_version: number;
  created_at: string;
  updated_at: string;
  color: string;
  ai_policy: LayerAIPolicy;
}

export interface KnowledgeLens {
  id: string;
  name: string;
  visible_layer_ids: string[];
  layer_order: string[];
  ai_readable_layer_ids: string[];
  is_default: boolean;
  time_range_days: number | null;
  mode: string;
}

export interface WorkspaceDescriptor {
  format_version: number;
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  layer_order: string[];
  layers: LayerDescriptor[];
  lenses: KnowledgeLens[];
}

export interface HealthResponse {
  ok: boolean;
  app: string;
  version: string;
  protocol_version: number;
  environment: string;
  python_version: string;
  qt_version: string;
  workspace_open: boolean;
}

export interface WorkspaceState {
  is_open: boolean;
  workspace: WorkspaceDescriptor | null;
  lenses: KnowledgeLens[];
}

export type NodeType =
  | "note"
  | "folder"
  | "tag"
  | "person"
  | "project"
  | "task"
  | "attachment"
  | "concept"
  | "source"
  | "decision"
  | "cluster"
  | "view";

export interface GraphNode {
  id: string;
  layer_id: string;
  type: NodeType;
  label: string;
  locked: boolean;
  folder_path: string;
  tags: string[];
  degree: number;
  updated_at: string;
  word_count: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  relationship: string;
  origin: "explicit" | "derived" | "ai-suggested";
  confidence: number | null;
  weight: number;
}

export interface GraphSnapshot {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  total_nodes: number;
  total_edges: number;
  locked_layer_ids: string[];
}

export interface NoteMetadata {
  id: string;
  layer_id: string;
  parent_id: string | null;
  title: string;
  folder_path: string;
  aliases: string[];
  tags: string[];
  properties: Record<string, unknown>;
  links: { target_title: string; alias: string | null; relationship: string }[];
  created_at: string;
  updated_at: string;
  size_bytes: number;
  word_count: number;
}

export interface Note {
  metadata: NoteMetadata;
  content: string;
}

export interface TreeFolder {
  id: string;
  layer_id: string;
  name: string;
  path: string;
  parent_id: string | null;
}

export interface TreeResponse {
  folders: TreeFolder[];
  notes: NoteMetadata[];
  locked_layer_ids: string[];
}

export type PropertyType =
  | "text"
  | "number"
  | "boolean"
  | "date"
  | "datetime"
  | "tags"
  | "relation"
  | "url"
  | "email"
  | "select"
  | "multi-select"
  | "formula"
  | "status"
  | "person"
  | "location"
  | "duration"
  | "rating"
  | "progress";

export interface PropertyDefinition {
  key: string;
  label: string;
  type: PropertyType;
  required: boolean;
  default: unknown;
  options: string[];
  minimum: number | null;
  maximum: number | null;
  formula: string;
  description: string;
}

export interface NoteSchema {
  id: string;
  name: string;
  icon: string;
  node_style: string;
  properties: PropertyDefinition[];
  allowed_relationships: string[];
  template: string;
  builtin: boolean;
}

export interface ValidationIssue {
  key: string;
  problem: string;
}

export interface NoteResponse {
  note: Note;
  schema_id: string | null;
  issues: ValidationIssue[];
}

export interface TrashEntry {
  entry: string;
  layer_id: string;
  folder_path: string;
  title: string;
}

export interface Backlink {
  source_id: string;
  source_title: string;
  layer_id: string;
  relationship: string;
  context: string;
}

export interface UnlinkedMention {
  source_id: string;
  source_title: string;
  layer_id: string;
  context: string;
}

export interface LinksResponse {
  backlinks: Backlink[];
  unlinked_mentions: UnlinkedMention[];
  outgoing: { target: string; relationship: string }[];
}

export interface LinkHealthResponse {
  broken: { source_id: string; target: string }[];
  orphans: string[];
}

export interface SearchResult {
  object_id: string;
  layer_id: string;
  title: string;
  path: string;
  snippet: string;
  score: number;
  tags: string[];
  reasons: string[];
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  locked_layers_excluded: number;
}

export type ExportTarget =
  | "chatgpt"
  | "claude"
  | "gemini"
  | "generic"
  | "local";
export type ExportShape = "single-file" | "package";
export type ContextDepth =
  | "selected-only"
  | "plus-links"
  | "plus-backlinks"
  | "one-hop"
  | "two-hops";
export type ContentMode = "full" | "summary" | "titles-only";

export interface ExportSource {
  source_id: string;
  object_id: string;
  layer_id: string;
  layer_name: string;
  is_private: boolean;
  title: string;
  path: string;
  tags: string[];
  properties: Record<string, string>;
  updated_at: string;
  content: string;
  truncated: boolean;
}

export interface ContextPlan {
  export_id: string;
  target: ExportTarget;
  shape: ExportShape;
  depth: ContextDepth;
  content_mode: ContentMode;
  prompt: string;
  workspace_name: string;
  created_at: string;
  sources: ExportSource[];
  relationships: { source: string; target: string; relationship: string }[];
  excluded_locked_count: number;
  private_source_count: number;
  private_layer_names: string[];
  estimated_tokens: number;
  token_budget: number | null;
  part_count: number;
  warnings: string[];
}

export interface ExportPart {
  filename: string;
  content: string;
  source_ids: string[];
  estimated_tokens: number;
}

export interface ExportResult {
  export_id: string;
  target: ExportTarget;
  shape: ExportShape;
  parts: ExportPart[];
  manifest: Record<string, unknown>;
  estimated_tokens: number;
  private_source_count: number;
  warnings: string[];
}

export interface WriteExportResponse {
  files_written: number;
  directory_name: string;
  export_id: string;
  private_source_count: number;
}

export interface ProviderCapability {
  provider_id: string;
  display_name: string;
  is_local: boolean;
  configured: boolean;
  streaming: boolean;
  structured_output: boolean;
  embeddings: boolean;
  vision: boolean;
  max_context_tokens: number;
  note: string;
}

export interface AppSettings {
  format_version: number;
  appearance: "cyberpunk-dark" | "cyberpunk-dim" | "high-contrast";
  motion: "full" | "reduced" | "system";
  graph_quality: "high" | "balanced" | "low-gpu";
  particles_enabled: boolean;
  bloom_enabled: boolean;
  battery_saver: boolean;
  telemetry_enabled: boolean;
  default_lens_id: string;
  last_workspace_path: string;
  developer_tools: boolean;
}

export interface JobRecord {
  id: string;
  type: string;
  title: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  detail: string;
  layer_id: string | null;
  privacy: "public" | "private" | "mixed" | "none";
  cancellable: boolean;
  started_at: string | null;
  ended_at: string | null;
  error_code: string | null;
  error_message: string | null;
}
