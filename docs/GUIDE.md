# The Strata Guide

A complete tour of Strata for people who use it, not people who build it. Every
feature described here exists in the current build; where a capability is
planned but not yet reachable from the UI, the guide says so explicitly.

> Strata is a **local-first, selectively encrypted, AI-native knowledge
> workspace**. Your notes are Markdown files on your own disk, private material
> is encrypted per layer, and AI only ever sees what you explicitly select.

---

## Table of contents

1. [Starting Strata](#1-starting-strata)
2. [The window](#2-the-window)
3. [Layers: public, private, locked](#3-layers-public-private-locked)
4. [Files and folders](#4-files-and-folders)
5. [Drag and drop](#5-drag-and-drop)
6. [Writing notes](#6-writing-notes)
7. [Linking, tags, and properties](#7-linking-tags-and-properties)
8. [Search](#8-search)
9. [The knowledge graph](#9-the-knowledge-graph)
10. [The 3D galaxy](#10-the-3d-galaxy)
11. [Views: table, cards, kanban, calendar, timeline](#11-views-table-cards-kanban-calendar-timeline)
12. [AI: the Context Composer](#12-ai-the-context-composer)
13. [AI operations: reorganise and generate notes](#13-ai-operations-reorganise-and-generate-notes)
14. [Exporting context](#14-exporting-context)
15. [Collaboration](#15-collaboration)
16. [Trash, undo, and other safety nets](#16-trash-undo-and-other-safety-nets)
17. [Settings](#17-settings)
18. [Keyboard shortcuts](#18-keyboard-shortcuts)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Starting Strata

Start Strata from a terminal with:

```
python -m app.main
```

(or the packaged `Strata` executable). The window shows *"Connecting to the
Strata host…"* for a moment, then opens your workspace. If you ever see *"The
Strata host is not reachable"*, the React frontend was opened without its
desktop shell — Strata is a desktop app, not a web page.

**The workspace.** Strata opens a default workspace automatically (named
*Strata*) and shows its name in the top bar. A workspace is an ordinary
directory on disk: you can back it up, put it in Git, or copy it to a USB
stick. There is currently no workspace picker in the UI — one default
workspace opens on launch.

---

## 2. The window

Strata is three columns under a command bar, with a status bar at the bottom.

```
┌─────────────────────────────────────────────────────────────┐
│  ▚ STRATA   Focus │ Explore │ Views │ Command    2D│3D  ⋯  │  ← command bar
├───────────┬────────────────────────────────────┬────────────┤
│ Navigator │              Stage                 │ Inspector  │
│  Layers   │   (editor, graph, or views)        │  AI        │
│  Files    │                                    │  Changes   │
│  Search   │                                    │  Properties│
│  Collab   │                                    │  Links     │
│  Graph    │                                    │            │
├───────────┴────────────────────────────────────┴────────────┤
│ 2 locked · sync · model · selection · graph: 41 nodes …     │  ← status bar
└─────────────────────────────────────────────────────────────┘
```

### Modes

The command bar switches the centre stage between four modes:

| Mode | Tooltip | What the stage shows |
| --- | --- | --- |
| **Focus** | "Read and write" | The Markdown editor with its tab bar. |
| **Explore** | "Navigate the graph" | The knowledge graph (3D or 2D). |
| **Views** | "Table, kanban, calendar, timeline" | Database-style views over your notes. |
| **Command** | "AI and bulk operations" | The graph, with the **Changes** inspector active for AI operation plans. |

The **2D / 3D** segmented control chooses the graph dimension. Strata falls
back to 2D automatically when the machine has no WebGL or when graph quality
is set to `low-gpu` — a notice explains the fallback when it happens.

The **Motion** button toggles reduced motion: with it on, decorative animation
(auto-rotation, twinkling, particles, pulses) stops while every state signal
stays visible statically.

### Panels

- **Navigator** (left): Layers, Files, Search, Collaboration, and the Graph
  list — a fully keyboard-accessible mirror of the graph.
- **Inspector** (right): four tabs — **AI**, **Changes**, **Properties**,
  **Links**. The active tab follows the mode (Focus → Properties, Command →
  Changes, otherwise AI), and you can switch it manually at any time.
- Both side panels collapse with the ◀ / ▶ toggles; on narrow windows they
  become drawers automatically.

### The status bar

Left to right: locked-layer count (or *"no private layers"*), sync state,
whether an AI model is configured, the current selection size (with a count of
private objects in it), and graph size. The right edge shows the Strata and Qt
versions.

---

## 3. Layers: public, private, locked

A **layer** is the container everything lives in — every note, folder, and
attachment belongs to exactly one layer. But a layer is more than a top-level
folder: it is a **permission and encryption boundary**.

| | Public layer | Private layer |
| --- | --- | --- |
| Storage | Plain Markdown files | Encrypted objects |
| Readable outside Strata | Yes, with any editor | No — filenames, folder tree, titles, tags, and attachments are all opaque on disk |
| Needs a password | No | Yes — chosen at creation, no reset |
| While locked | n/a | Contributes **nothing**: no titles, no search results, no graph nodes, and nothing to an AI model |

### Creating a layer

Click **＋** in the Layers panel. The dialog asks for:

- **Name** — the display name.
- **Visibility** — *Public* (plain Markdown) or *Private* (encrypted).
- For a private layer: a **password** (minimum 8 characters, typed twice) and
  optionally a **recovery key**. The recovery key is shown exactly once, and
  the dialog will not let you dismiss it until you confirm you saved it. There
  is **no reset**: Strata keeps no copy of the password and no copy of the
  key. Lose both and the contents are gone permanently.
- **Start with** — optional starter content, so the layer opens ready to use:
  - **Folders**: a comma-separated list (e.g. `Ideas, Research, Archive`)
    created at the layer root.
  - **Create a first note**: a *Welcome* note to start writing in
    immediately. On by default.

Starter content works identically for public and private layers — in a private
layer the starter folders and note are encrypted like everything else.

### Locking and unlocking

- **Unlock** on a locked private layer asks for the password (or the recovery
  key). A failed unlock deliberately does not say *why* it failed.
- **🔒** locks a layer instantly. Locking also purges everything decrypted
  from the interface: open tabs, drafts, search results, and graph selections
  from that layer vanish.
- **Lock all** (shown whenever any private layer is unlocked) locks every
  private layer with one click.
- **⚙** on an unlocked private layer opens key management: change the
  password, reissue the recovery key, or rotate the encryption key. The dialog
  explains which of these actually revokes a leaked key.

---

## 3½. Capture and the knowledge loop

The **⇣ Capture** button in the command bar is the fastest way in: paste text
(or import a page by URL) and it lands in the **Inbox/** folder as a raw
capture — with the source URL, when you saved it, and *why* you kept it. New
workspaces start with four conventional folders: **Inbox** (unprocessed
material), **Knowledge** (processed concepts, people, decisions), **Reports**
(finished AI-assisted outputs), and **Templates**.

URL import fetches a page's *text* once, over a guarded fetch that refuses
private and local addresses, redirects, and oversized pages. The page is stored
as untrusted data. It can be switched off entirely (`url_import_enabled`).

To turn raw captures into knowledge, select them and run **Process into
knowledge** in the Changes tab (§13): the AI proposes concept pages, people and
organizations, decision records, tasks and tags — every one marked
`ai-inferred` with the AI execution that produced it, and every one subject to
your review before anything is created.

Every edit to a note in a public layer also leaves a **version** — see the
History section of the Properties tab, where each prior state shows who
replaced it (you, an AI plan, or a restore) and can be restored without ever
silently overwriting anything. Private layers keep no version files on disk by
design; snapshots remain their recovery mechanism.

---

## 4. Files and folders

The **Files** panel is a real tree over the real folders on disk. In a public
layer, what you see is literally the directory structure — rename a folder in
Strata and the directory on disk is renamed.

Each layer has its own section, headed by the layer's name. Hover the layer
name for its actions:

- **＋** — new note at this layer's root.
- **🗀** — new folder at this layer's root.

Every layer has these actions, so a layer you just created can be filled
immediately.

Folder rows offer, on hover:

- **＋** — new note in this folder.
- **🗀** — new subfolder. Folders nest to any depth.
- **✎** — rename the folder.
- **🗑** — move the folder *and its notes* to the trash.

Note rows offer **✎** rename (or press `F2`), **⧉** duplicate, and **🗑**
move to trash (or press `Delete`). Click a note's name (or press `Enter` on
it) to open it in the editor. New notes are titled *Untitled*, *Untitled 2*, …
— rename them at will; links to a renamed note are rewritten automatically.

Because a note's identity is derived from its path, renaming or moving a note
is safe: open tabs follow the note to its new identity.

---

## 5. Drag and drop

The file tree supports two kinds of drop, and the target folder highlights
while you hover over it:

**Moving notes.** Drag a note onto any folder to move it there. Drop it onto a
layer's *name* to move it to the layer root. The move is validated by the
Python backend, which re-checks the destination path — a dragged note cannot
escape its layer or be dropped somewhere unsafe.

**Importing files from your computer.** Drag files from your file manager
(Explorer, Finder, …) onto a folder or a layer name:

- **Markdown and plain text** (`.md`, `.markdown`, `.txt`) become notes. The
  filename (minus extension) becomes the title; the content is imported
  verbatim.
- **Everything else** (PDFs, images, any binary) is stored as an
  **attachment** inside the layer, wrapped in a new note that embeds it — so
  the file is visible in the tree and linkable like any note.
- Name collisions are resolved automatically (`notes`, `notes 2`, …), and
  files import in the order you dropped them.

Imports respect the layer boundary: a file dropped into a **private layer is
encrypted before it touches the disk** — its original filename never appears
in plaintext. Attachments are capped at 64 MB each.

---

## 6. Writing notes

Switch to **Focus** mode (or open any note) for the editor. Open notes appear
as tabs; a `•` on a tab means unsaved changes.

### View modes

The editor bar offers three views: **Source** (Markdown only), **Live**
(editor beside a rendered preview), and **Reading** (rendered only).

### Markdown

The editor is CodeMirror 6 with full Markdown syntax highlighting. The
preview renders:

- **Math** — KaTeX, inline `$…$` and block `$$…$$`.
- **Diagrams** — fenced ` ```mermaid ` blocks (a failed diagram falls back to
  showing its source).
- Sanitised HTML — scripts and active content are stripped.

### Slash commands

Type `/` at the start of a line for quick insertions: `/h1 /h2 /h3`, `/todo`,
`/table`, `/code`, `/quote`, `/callout`, `/mermaid`, `/math`, `/link`,
`/divider`, and the typed-relationship shortcuts `/supports`, `/depends`,
`/contradicts`. Slash commands only insert Markdown — they never call the
backend.

### Saving

Notes autosave about a second after you stop typing, and also on tab switch
and `Ctrl+S`. The save pipeline guarantees the *last* content always wins,
even during rapid tab switching.

If a file changes **on disk outside Strata** while you have unsaved edits, a
bar appears: *"This file changed on disk outside Strata."* with **Reload from
disk** and **Keep my edits**. Without unsaved edits, external changes are
picked up silently — editing a public layer with another editor alongside
Strata is a supported workflow.

---

## 7. Linking, tags, and properties

### Wiki links

`[[Note Title]]` links to another note. Typing `[[` opens title
autocompletion. `Ctrl+click` a link in the editor (or plain click in the
preview) opens the target. Renaming a note rewrites incoming links.

### Typed relationships

A line like:

```
supports:: [[Encryption Architecture]]
```

creates a *typed* link that the graph understands. Suggested relationship
keys: `supports`, `contradicts`, `depends_on`, `expands`, `supersedes`,
`blocks`, `evidence_for`, `derived_from`, `relates_to`.

### Tags

`#tag` anywhere in the body tags the note; typing `#` autocompletes existing
tags. Tags appear as their own nodes in the graph.

### Properties and schemas

The **Properties** inspector tab edits a note's YAML frontmatter with typed
controls (dates, numbers, checkboxes, status selects, tag lists…). Assigning
a **schema** from the dropdown gives the note a shape. Ten built-ins ship:
Meeting ◷, Project ▣, Person ☺, Research source ❝, Daily note ☀, Task ☐,
Decision record ◇, Architecture component ◆, Security threat ⚠, Incident
report ✸.

Schema violations are **reported, not corrected** — your file remains the
source of truth; Strata only tells you where it disagrees with the schema.

### The Links panel

The **Links** inspector tab shows, for the open note: **Backlinks**,
**Outgoing** links, **Broken links** (targets that do not exist yet), and
**Unlinked mentions** — notes that mention this one in prose without linking.

---

## 8. Search

The Search panel searches titles, tags, properties, and body text across all
readable layers. Locked layers contribute nothing, and the result list says
how many layers were excluded.

- **Semantic** (on by default) blends meaning-based similarity into ranking,
  so a search can find notes that say the same thing in different words.
- **Show signals** reveals *why* each result matched: bars for text, meaning,
  tag, property, linked, and recency signals.
- **Similar to this** (shown while a note is open) finds the open note's
  semantic neighbours.

Results are selectable — click to select the note in the graph/composer,
`Ctrl+click` to add, or **Select all** to select every result. Search is the
fastest way to build an AI context selection.

---

## 9. The knowledge graph

**Explore** mode shows your knowledge as a graph: notes, folders, and tags are
nodes; wiki links, typed relationships, folder membership, tag membership, and
(optionally) semantic similarity are edges. A locked layer appears as a single
redacted marker — nothing about its contents leaks into the picture.

### Selection

Selection is the graph's central verb — it drives the AI composer, exports,
and bulk operations.

- **Click** — select a node. **Ctrl+click** — add/remove from the selection.
- **Shift+click** — select the *shortest path* between the anchor and the
  clicked node.
- **Double-click** — open the note in the editor.
- **Shift+drag** (2D only) — lasso-select a region; hold `Ctrl` to add.

The **from selection** buttons in the graph controls expand a selection
structurally: **Connected** (everything reachable), **Cluster** (the anchor's
semantic cluster), **Neighbours** (direct connections), and **Path** (shortest
path between the first and last selected nodes).

The toolbar also toggles **Semantic edges** (AI-inferred similarity edges) and
**Cluster colours** (colour nodes by semantic cluster).

### The accessible graph

The **Graph list** in the navigator is the same graph as a real tree — for
screen readers, and for anyone who prefers a list. Arrow keys move, `Enter`
opens, `Space` toggles selection, `Ctrl+A` selects all. Locked objects are
announced as such.

---

## 10. The 3D galaxy

In 3D, the graph is rendered as a galaxy: nodes are glowing stars, edges are
lines of light with particles flowing along them, behind everything a
starfield drifts and faint nebula clouds breathe. The scene is engineered to
stay smooth at ten thousand nodes.

What the visuals *mean*:

- **Node colour and size** encode type and importance; selected nodes ignite
  gold and pulse.
- **Hover** a node and it swells, its name appears, its connections light up,
  and the cursor becomes a hand — you can see a node's neighbourhood without
  committing to a selection.
- **Select** a node and the camera glides over and re-centres on it; the
  flight eases out and then stops, so it never fights your own navigation.
  With several nodes selected, edges *between* selected nodes burn brightest —
  the "constellation" is exactly the shape you are about to send to a model —
  while unrelated edges recede.
- **Labels** name the landmarks: selected and hovered nodes always, then the
  most-connected hubs.
- The galaxy **auto-rotates while idle** and holds still the moment you select
  something (or enable reduced motion).

Orbit with the left mouse button, zoom with the wheel, pan with the right
button. Double-click opens a note.

**Quality tiers.** The `graph_quality` setting (`high` / `balanced` /
`low-gpu`) scales the star, label, nebula, and particle budgets. `low-gpu`
skips the 3D scene entirely in favour of the 2D graph. Reduced motion stops
the drift, twinkle, breathing, and flow particles while keeping every state
signal visible.

---

## 11. Views: table, cards, kanban, calendar, timeline

**Views** mode turns notes into a live database. Five view types: **Table**,
**Cards**, **Kanban**, **Calendar**, **Timeline**. The toolbar offers:

- **Sort** — any property, ascending/descending.
- **Group** — kanban columns / table groups by a property (e.g. `status`).
- **Date field** — which property drives calendar/timeline placement.
- **Filters** — chainable conditions: *is, is not, contains, is set, is
  empty, >, <, before, after*.

Queries run in Python against the live notes — a view is a lens, not a copy.
Locked layers are excluded and the view says how many were hidden. View
configurations are currently session-only (there is no saved-views UI yet).

---

## 12. AI: the Context Composer

The **AI** inspector tab is not a chat box wired to your whole vault. It is a
**context composer** built around one principle: *whatever you illuminate is
exactly what a model sees — nothing more.*

### Building context

Select nodes anywhere — graph, file tree, search results. The **context
tray** lists every source that would be sent, each with its layer badge
(public/private) and a remove button. The plan is recomputed by the backend on
every change, so the tray is never an approximation.

A locked layer **cannot** be included — not by selection, not by expansion,
not by accident. The tray shows how many objects were excluded because their
layer is locked.

### Options

- **Target**: Generic Markdown / ChatGPT / Claude / Gemini / Local model
  (changes framing, never content).
- **Shape**: single Markdown file or a multi-file package.
- **Context depth**: selected only → + outgoing links → + backlinks → one or
  two graph hops.
- **Content**: full text, summarised, or titles only.
- **Token budget**: No limit / 8k / 32k / 128k / 200k, with a live estimate.
  Over budget, Strata **splits into parts — it never silently truncates**.

The prompt box accepts free text, plus starter templates behind the
**/ commands** button: `/summarize`, `/compare`, `/find-gaps`,
`/create-structure`, `/create-tasks`, `/suggest-links`, `/create-prd`,
`/create-architecture`.

### Providers

Strata routes requests to a provider you configure — each is labelled
**local** or **remote**:

| Provider | Kind | API key |
| --- | --- | --- |
| Ollama | local | none |
| llama.cpp server | local | none |
| LM Studio | local | none |
| OpenAI | remote | keychain |
| Anthropic (Claude) | remote | keychain |
| OpenAI-compatible endpoint | remote | none |
| Claude CLI | remote¹ | none |

¹ The Claude CLI runs on your machine but sends content to Anthropic, so
Strata counts it as remote — the policy gate treats it accordingly.

API keys are stored in the **operating system keychain**, never in a Strata
file, log, or export. On systems without a keychain, Strata refuses to
configure key-based providers rather than writing a key to a plain file.

### The policy gate

Before anything is sent, Python — not the UI — checks every source layer's AI
policy. The verdicts:

- **Allowed** — local providers, typically.
- **Needs confirmation** — a remote provider with private content: the
  *"Leaving this device"* dialog states the provider, how many objects, and how
  many are private, and defaults focus to **Cancel**.
- **Blocked** — e.g. any locked layer, or a layer whose policy is local-only
  when a remote provider is chosen. The send button is disabled and the
  reason is shown.

Ask with **Ask locally** / **Ask (remote)**; answers stream in with a **Stop**
button. Every remote request also writes a privacy receipt on the backend
(there is no receipts browser in the UI yet).

### AI history

Every model request — asks and plan generations alike — is recorded in the
workspace's **AI history**, at the bottom of the AI panel. It persists on disk
(`.strata/ai/` inside the workspace) and survives a restart, so "what did I ask
last week, with which sources, and what came back?" is always answerable.

Two privacy rules apply:

- A request that involved a **private layer** is stored **redacted**: provider,
  model, token counts and timestamps only — never the prompt, the sources, or
  the answer. The record shows a `redacted` badge.
- **Clear history…** deletes the recorded history for the workspace (it is a
  two-step action and touches only history, never notes).

The same persistence covers privacy receipts and the applied-plans audit log —
which means **Undo** for an applied AI plan now works even after restarting
Strata.

---

## 13. AI operations: reorganise and generate notes

The **Changes** inspector tab (front and centre in **Command** mode) lets a
model *change* the workspace — under a review-first contract: **the model
proposes; only you apply.**

Choose what the AI should do:

- **Reorganise the workspace** — describe an outcome ("group these notes by
  project", "extract the decisions into decision records") and the model
  designs a plan of operations.
- **Generate new notes** — the model writes new Markdown notes (choose how
  many, or let it decide), using your currently selected notes as source
  material. Each generated note records where it came from with a
  `derived_from::` link.

### Review

The proposed plan appears as a checklist:

- Every operation shows its type, a rationale, and a **before/after diff**.
- Safe, valid operations are pre-ticked. **Destructive operations** (anything
  that changes or removes existing content) are tagged *"changes content"* and
  are **never pre-ticked** — you must opt in to each one.
- Invalid operations are flagged with the reason and cannot be applied.
- Operations touching a private layer are labelled.

**Apply n change(s)** applies the ticked operations **transactionally** — all
or nothing. A snapshot is taken first, so the applied state shows: *"A
snapshot was taken first, so this is reversible"* with an **Undo** button that
restores the pre-apply state.

---

## 14. Exporting context

Instead of (or as well as) asking a model directly, export the composed
context for use anywhere:

- **Export Markdown…** writes the file(s) to disk.
- **Copy to clipboard** copies them (with a reminder that clipboard managers
  may retain content).

Exports follow a documented format (`docs/export-format/README.md`): an
instructions header, your prompt, the selected knowledge, a Mermaid graph
summary, and a source-index audit table. Sources are numbered
`STRATA-SOURCE-001…` — internal ids never leak.

**Private content in exports.** Locked layers can never be exported — their
content is simply not available. Unlocked private content can be, but only
through the **privacy review**: a dialog listing *every private source by
name*, stating plainly that the export will not be encrypted, before you
confirm **Export decrypted Markdown**.

---

## 15. Collaboration

The Collaboration panel syncs a layer with other people **without a server
that can read it**: peers exchange encrypted CRDT updates through a *relay* (or
a shared folder), and the relay never holds a key.

- **Sync relay** — a relay URL, or blank to sync through a local/shared
  folder. See the README for self-hosting the bundled relay.
- **Share this layer** — makes the layer shared and produces an **invite
  (document id)** to give collaborators.
- **Join…** — paste an invite's document id to join someone else's layer.
- **Sync now** exchanges updates; **Peers** shows who is present; **Leave**
  returns the layer to personal mode.

Conflicting edits are never silently discarded: a conflict appears in the
panel (*"n conflict(s) — nothing was lost"*) with explicit choices such as
**Keep in Conflicts/** versus **Confirm delete**.

---

## 16. Trash, undo, and other safety nets

- **Trash, not deletion.** Deleting a note or folder moves it to the
  workspace trash. The **Trash (n)** section at the bottom of the file tree
  lists entries with a **Restore** button. (Emptying the trash is not yet
  exposed in the UI — trashed files simply remain recoverable.)
- **Snapshots before AI applies.** Every applied operation plan takes a
  snapshot first; **Undo** restores it.
- **Transactional applies.** A plan applies fully or not at all.
- **The disk is the truth.** Public layers are plain files — any backup tool,
  Git repository, or file sync you already trust works on a Strata workspace.

---

## 17. Settings

Settings exposed in the UI:

| Setting | Where |
| --- | --- |
| Motion (full / reduced) | Command bar toggle |
| Semantic edges, cluster colours | Graph controls |
| Semantic search | Search panel checkbox |
| Sync relay URL | Collaboration panel |

Further settings live in a JSON settings file in the OS config directory and
are currently **edited by hand**, not in the UI: appearance theme
(`cyberpunk-dark` / `cyberpunk-dim` / `high-contrast`), `graph_quality`
(`high` / `balanced` / `low-gpu`), `particles_enabled`, `bloom_enabled`,
`battery_saver`, AI defaults (provider, model, base URLs, Claude CLI path,
token limits), and `telemetry_enabled` (off by default).

Per-layer AI policy (what a model may read, summarise, or edit per layer) is
enforced by the backend with safe defaults — new layers allow **local-only**
AI access until the policy is changed in the settings file. A policy UI is
planned.

---

## 18. Keyboard shortcuts

Strata's shortcuts are scoped to the panel you are in.

| Context | Keys | Action |
| --- | --- | --- |
| Anywhere | `Ctrl/Cmd+N` | New note (in the first unlocked layer) |
| Editor | `Ctrl/Cmd+S` | Save now |
| Editor | `Ctrl/Cmd+click` on `[[link]]` | Open the linked note |
| Editor | `Ctrl/Cmd+Z` / `Ctrl/Cmd+Y` | Undo / redo |
| Editor | `Ctrl/Cmd+F` | Find in note |
| File tree (note focused) | `Enter` | Open |
| File tree (note focused) | `F2` | Rename |
| File tree (note focused) | `Delete` | Move to trash |
| Rename fields | `Enter` / `Esc` | Commit / cancel |
| Graph list | `↑` `↓` | Move focus |
| Graph list | `Enter` | Open note |
| Graph list | `Space` | Toggle selection |
| Graph list | `Ctrl/Cmd+A` | Select all |
| Graph & lists | `Ctrl/Cmd+click` | Add/remove from selection |
| Graph | `Shift+click` | Shortest-path selection |
| 2D graph | `Shift+drag` | Lasso selection |
| Dialogs / menus | `Esc` | Close |

---

## 19. Troubleshooting

**"The Strata host is not reachable."** The frontend was loaded without the
desktop shell. Start Strata with `python -m app.main`.

**The 3D view doesn't appear.** Strata falls back to the 2D graph when WebGL
is unavailable or graph quality is `low-gpu`. Everything except the galaxy
rendering works identically in 2D.

**"Ask" is greyed out.** Read the tooltip: either no provider is configured
(add one in the AI tab, with an API key if required), or the policy gate
blocked the request — most commonly because the selection includes a layer
whose policy is local-only while a remote provider is chosen.

**A private layer won't unlock.** Strata deliberately reports only that the
layer *did not unlock* — check the password, or use the recovery key. If both
are lost, the layer cannot be recovered by anyone, including Strata's
developers. That is the design.

**A note changed outside Strata.** With no unsaved edits, Strata reloads it
silently. With unsaved edits, choose **Reload from disk** or **Keep my
edits** in the conflict bar.

**Something got deleted.** Check **Trash** at the bottom of the file tree and
restore it. If an AI plan did it, **Undo** in the Changes tab restores the
pre-apply snapshot.

---

*For architecture, storage formats, encryption details, and the threat model,
see the [documentation index in the README](../README.md#documentation).*
