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
    "disabled" | "local-only" | "remote-with-confirmation" | "remote-always";
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
  /** True when this device has a keychain-saved password for the layer. */
  password_remembered?: boolean;
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
  cluster: number;
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
  /** Per-signal contributions to the score. The reasons are derived from these. */
  signals: Record<string, number>;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  locked_layers_excluded: number;
}

export type ExportTarget =
  "chatgpt" | "claude" | "gemini" | "generic" | "local";
export type ExportShape = "single-file" | "package";
export type ContextDepth =
  "selected-only" | "plus-links" | "plus-backlinks" | "one-hop" | "two-hops";
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

/** The provider shape Milestone 7 sends: capabilities as a string list. */
export interface ProviderView {
  provider_id: string;
  display_name: string;
  is_local: boolean;
  configured: boolean;
  requires_api_key: boolean;
  capabilities: string[];
  max_context_tokens: number;
  note: string;
}

export interface ProviderHealthView {
  provider_id: string;
  reachable: boolean;
  configured: boolean;
  detail: string;
  models: {
    id: string;
    display_name: string;
    context_tokens: number;
    is_local: boolean;
  }[];
}

export interface PolicyView {
  verdict: "allowed" | "needs_confirmation" | "denied";
  reason: string;
  blocking_layers: string[];
  is_remote: boolean;
  private_object_count: number;
  object_count: number;
}

export interface PrivacyReceipt {
  id: string;
  created_at: string;
  kind: "export" | "ai-request";
  provider: string;
  model: string;
  is_remote: boolean;
  layer_ids: string[];
  object_count: number;
  private_object_count: number;
  attachment_count: number;
  estimated_tokens: number;
  destination: string;
  encrypted_in_transit: boolean;
  files_written: number;
  result: "completed" | "cancelled" | "failed";
  undo_reference: string | null;
}

export interface AIStreamEvent {
  requestId: string;
  kind: "start" | "delta" | "done" | "error";
  text?: string;
  model?: string;
  output_tokens?: number;
  error?: string;
}

export interface HealthItem {
  key: string;
  label: string;
  count: number;
  note_ids: string[];
  note_titles: string[];
  recommendation: string;
}

export interface HealthReport {
  items: HealthItem[];
  duplicates: ConnectionSuggestion[];
  total_notes: number;
  locked_layers: number;
}

export interface ConnectionSuggestion {
  note_a: string;
  note_a_title: string;
  note_b: string;
  note_b_title: string;
  layer_id: string;
  kind: "similar" | "duplicate" | "mention";
  score: number;
  explanation: string;
  excerpt: string;
  suggested_relationship: string;
}

export interface UsedSource {
  object_id: string;
  title: string;
  is_private: boolean;
}

export interface AISendResponse {
  request_id: string;
  execution_id: string;
  conversation_id: string;
  sources: UsedSource[];
}

export interface SavedPrompt {
  id: string;
  name: string;
  description: string;
  category: string;
  prompt_text: string;
  model_preference: string;
  temperature: number | null;
  version: number;
  usage_count: number;
  created_at: string;
  updated_at: string;
  last_used_at: string;
}

/**
 * One persisted model call — the workspace's durable AI memory. `redacted`
 * records that content fields were stripped before persisting because the
 * execution involved a private layer.
 */
export interface AIExecutionRecord {
  id: string;
  kind: "ai-request" | "plan-generation";
  created_at: string;
  provider: string;
  model: string;
  is_remote: boolean;
  layer_ids: string[];
  prompt: string;
  response_text: string;
  source_object_ids: string[];
  source_count: number;
  private_source_count: number;
  input_tokens: number;
  output_tokens: number;
  result: "completed" | "cancelled" | "failed";
  error_message: string;
  duration_ms: number;
  redacted: boolean;
}

// --- transactional AI operations ------------------------------------------

export interface Operation {
  type: string;
  layer_id: string;
  note_id?: string | null;
  folder_path: string;
  title: string;
  content: string;
  target_note_id?: string | null;
  target_title: string;
  relationship: string;
  property_key: string;
  property_value: string;
  tag: string;
  rationale: string;
  properties?: Record<string, string>;
}

// --- version history --------------------------------------------------------

export interface NoteVersionSummary {
  index: number;
  created_at: string;
  origin: string;
  change: string;
  title: string;
  size_chars: number;
}

export interface NoteVersion {
  index: number;
  created_at: string;
  origin: string;
  change: string;
  title: string;
  content: string;
  properties: Record<string, unknown>;
}

export interface VersionListResponse {
  versions: NoteVersionSummary[];
  supported: boolean;
  detail: string;
}

export interface OperationPlan {
  id: string;
  summary: string;
  operations: Operation[];
  created_at: string;
  provider: string;
  model: string;
  prompt: string;
}

export interface DiffEntry {
  index: number;
  type: string;
  layer_id: string;
  layer_name: string;
  is_private: boolean;
  is_destructive: boolean;
  title: string;
  summary: string;
  rationale: string;
  before: string;
  after: string;
  valid: boolean;
  problem: string;
}

export interface PlanReview {
  plan: OperationPlan;
  entries: DiffEntry[];
  valid_count: number;
  invalid_count: number;
  destructive_count: number;
  private_layers_touched: string[];
  warnings: string[];
}

export interface AppliedPlan {
  plan_id: string;
  snapshot_id: string;
  applied_at: string;
  results: {
    index: number;
    type: string;
    applied: boolean;
    detail: string;
    error: string;
  }[];
  summary: string;
  provider: string;
  model: string;
  prompt: string;
  undone: boolean;
  redacted: boolean;
}

export interface PlanStreamEvent {
  requestId: string;
  kind: "plan" | "error";
  plan?: OperationPlan;
  error?: string;
}

export interface SnapshotRecord {
  id: string;
  name: string;
  created_at: string;
  kind: string;
  layer_count: number;
  note_count: number;
}

// --- structured views -----------------------------------------------------

export type ViewType =
  "table" | "list" | "cards" | "kanban" | "calendar" | "timeline" | "gallery";

export type FilterOperator =
  | "equals"
  | "not_equals"
  | "contains"
  | "not_contains"
  | "is_empty"
  | "is_not_empty"
  | "greater_than"
  | "less_than"
  | "before"
  | "after"
  | "in";

export interface ViewFilter {
  field: string;
  operator: FilterOperator;
  value: string;
}

export interface ViewSort {
  field: string;
  direction: "asc" | "desc";
}

export interface ViewConfig {
  id: string;
  name: string;
  type: ViewType;
  layer_ids: string[];
  folder_scope: string;
  filters: ViewFilter[];
  sort: ViewSort[];
  group_by: string;
  visible_properties: string[];
  date_field: string;
}

export interface ViewRow {
  object_id: string;
  layer_id: string;
  layer_name: string;
  is_private: boolean;
  title: string;
  folder_path: string;
  tags: string[];
  properties: Record<string, string>;
  created_at: string;
  updated_at: string;
  snippet: string;
}

export interface ViewGroup {
  key: string;
  label: string;
  rows: ViewRow[];
}

export interface ViewResult {
  config: ViewConfig;
  rows: ViewRow[];
  groups: ViewGroup[];
  total: number;
  available_properties: string[];
  locked_layers_excluded: number;
}

export type AppearanceTemplate =
  | "cyberpunk-dark"
  | "cyberpunk-dim"
  | "high-contrast"
  | "ember"
  | "forest"
  | "slate";

export type FontBody = "inter" | "system" | "chakra";
export type FontDisplay = "chakra" | "inter" | "system";
export type FontMono = "jetbrains" | "consolas" | "system";

/** Whitelisted theme colour keys (snake_case → CSS --kebab-case). */
export type ThemeColorKey =
  | "surface_void"
  | "surface_base"
  | "surface_raised"
  | "surface_overlay"
  | "text_primary"
  | "text_secondary"
  | "text_tertiary"
  | "accent_primary"
  | "accent_ai"
  | "accent_collaboration"
  | "status_success"
  | "status_warning"
  | "status_danger"
  | "graph_background"
  | "graph_node_default"
  | "graph_node_selected"
  | "graph_glow_selected"
  | "graph_edge_default"
  | "graph_edge_selected"
  | "border_accent";

export interface AppSettings {
  format_version: number;
  appearance: AppearanceTemplate;
  motion: "full" | "reduced" | "system";
  graph_quality: "high" | "balanced" | "low-gpu";
  particles_enabled: boolean;
  bloom_enabled: boolean;
  battery_saver: boolean;
  telemetry_enabled: boolean;
  default_lens_id: string;
  last_workspace_path: string;
  developer_tools: boolean;
  /** Body UI font stack preset. */
  font_body: FontBody;
  /** Display / chrome font stack preset. */
  font_display: FontDisplay;
  /** Monospace font stack preset. */
  font_mono: FontMono;
  /** Rem cascade multiplier (0.85–1.35). */
  ui_scale: number;
  /** Optional #RRGGBB overrides layered on the appearance template. */
  theme_colors: Partial<Record<ThemeColorKey, string>>;
  relay_url: string;
  default_provider: string;
  /** Ollama id for Qwythos-9B (`qwythos`) by default. */
  default_model: string;
  /** False until the first-run tutorial is skipped or finished. */
  onboarding_tour_completed: boolean;
  /**
   * Signal-style (on by default): exclude the whole Strata window from
   * screenshots / screen shares. Enforced by the native shell, not the web UI.
   *
   * This is the *request*. What the OS granted comes back separately as
   * `CaptureProtection` — the two are not the same, and the UI must show the
   * second one.
   */
  hide_for_sharing: boolean;
  /**
   * When on, closing or minimizing hides the window to a tray icon instead of
   * quitting — it leaves the taskbar, but the process stays honestly listed.
   * `start_in_tray` launches hidden. Enforced by the native shell.
   */
  minimize_to_tray: boolean;
  start_in_tray: boolean;
  hide_from_taskbar: boolean;
  /**
   * Off by default: lets Strata launch and read a Chrome window of its own, so
   * research reaches logged-in and JavaScript-rendered pages. Blank executable
   * and profile paths mean "find Chrome yourself" and "use Strata's own
   * profile".
   */
  browser_control_enabled: boolean;
  browser_backend: BrowserBackend;
  browser_extensions: string[];
  browser_user_scripts: string[];
  browser_blocked_hosts: string[];
  browser_executable_path: string;
  browser_profile_path: string;
  browser_debug_port: number;
  browser_search_engine: string;
  /** Blur images, video and canvas in the browser pane. Amount is the radius. */
  browser_blur_media: boolean;
  browser_blur_amount: number;
  browser_mobile_mode: boolean;
  /**
   * The encrypted web archive (Ctrl+Alt+F in the WebView2 pane). An empty
   * layer id means "the first unlocked private layer".
   */
  web_archive_layer_id: string;
  web_archive_max_media_mb: number;
  web_archive_ffmpeg_path: string;
  web_archive_max_height: number;
  web_archive_allow_private_addresses: boolean;
  /** Lock private layers after this many idle minutes; 0 = never. */
  auto_lock_minutes: number;
  /** Lock on Windows lock, session disconnect and sleep. */
  auto_lock_on_system_lock: boolean;
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

// --- Collaboration (M9) -------------------------------------------------

export type ShareRole = "owner" | "editor" | "viewer";

export type ConflictKind = "move_cycle" | "move_vs_delete" | "edit_vs_delete";

export interface PresencePeer {
  peer_id: string;
  display_name: string;
  color: string;
  active_note_id: string | null;
  cursor: number | null;
}

export interface ConflictRecord {
  conflict_id: string;
  kind: ConflictKind;
  node_ids: string[];
  peers: string[];
  detected_at: string;
  previous_parent: string | null;
  summary: string;
  resolved: boolean;
}

export interface CollaborationState {
  layer_id: string | null;
  mode: "personal" | "shared";
  enabled: boolean;
  role: ShareRole;
  doc_id: string | null;
  peers: PresencePeer[];
  pending_conflicts: number;
  uncompacted_updates: number;
}

// --- browser research -----------------------------------------------------
//
// Strata drives a real Chrome over a loopback DevTools port rather than
// embedding a view, so the user's own extensions and sign-ins apply. Every
// field below describes something that happened in *that* browser; page text
// is untrusted data and is rendered as text, never as markup.

export type BrowserBackend = "embedded" | "webview2" | "chrome";
export type DigestMode = "full" | "brief" | "outline";

export interface BrowserStatus {
  enabled: boolean;
  /** "embedded" is the pane in this window; "chrome" is a real Chrome. */
  backend: BrowserBackend;
  running: boolean;
  /** Only the Chrome backend can load the user's extensions. */
  supports_extensions: boolean;
  port: number;
  browser_version: string;
  executable: string;
  profile_path: string;
  tab_count: number;
  // Media blur (embedded pane only).
  blur_enabled: boolean;
  blur_amount: number;
  blur_supported: boolean;
  // Mobile layout (embedded pane serves a mobile user-agent).
  mobile_mode: boolean;
  detail: string;
}

/** Pushed when pane blur changes — including from the application hotkey. */
export interface BlurStreamEvent {
  enabled: boolean;
  amount: number;
  supported: boolean;
}

export interface BrowserTab {
  target_id: string;
  title: string;
  url: string;
  active: boolean;
}

/** Reading a page is asynchronous: `scrape_tab` starts it, this delivers it. */
export interface PageStreamEvent {
  requestId: string;
  kind: "page" | "error";
  page?: ScrapedPage;
  note?: Note;
  error?: string;
}

export interface ScrapedPage {
  url: string;
  title: string;
  text: string;
  char_count: number;
  truncated: boolean;
  target_id: string;
  note_id: string;
}

/**
 * What the OS actually granted for "Hidden for sharing".
 *
 * `excluded` — the window is omitted from capture entirely.
 * `blacked-out` — the older `WDA_MONITOR` fallback: it appears as a black
 *   rectangle in a recording, which is still private but looks different.
 * `off` — not hiding, by the user's choice.
 * `failed` — hiding was requested and the OS refused.
 * `unsupported` — this platform has no per-window capture control at all.
 * `unknown` — asked before the native window existed.
 */
export type CaptureProtection =
  "excluded" | "blacked-out" | "off" | "failed" | "unsupported" | "unknown";
