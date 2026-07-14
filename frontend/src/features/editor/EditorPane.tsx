/**
 * Focus mode: tabs, the editor, and the three view modes.
 *
 * Source / Live / Reading are the three ways to look at the same file. Live is a
 * split (editor left, rendered right) rather than an inline transform: an inline
 * live-preview that rewrites the document under the cursor is a well-known source
 * of lost keystrokes, and typing latency is the one thing this product cannot
 * trade away.
 */

import { useMemo } from "react";
import { useStore, type ViewMode } from "../../state/store";
import { MarkdownEditor, type EditorSuggestions } from "./MarkdownEditor";
import { MarkdownPreview } from "./MarkdownPreview";

const MODES: { value: ViewMode; label: string }[] = [
  { value: "source", label: "Source" },
  { value: "live", label: "Live" },
  { value: "reading", label: "Reading" },
];

export function EditorPane(): JSX.Element {
  const state = useStore();
  const { openNote, tabs, activeNoteId, viewMode, tree, dirty, saving } = state;

  const suggestions: EditorSuggestions = useMemo(() => {
    const notes = tree?.notes ?? [];
    const tags = new Set<string>();
    const propertyKeys = new Set<string>([
      "supports",
      "contradicts",
      "depends_on",
      "expands",
      "supersedes",
      "blocks",
      "evidence_for",
      "derived_from",
      "relates_to",
    ]);
    for (const note of notes) {
      note.tags.forEach((tag) => tags.add(tag));
      Object.keys(note.properties).forEach((key) => propertyKeys.add(key));
    }
    return {
      noteTitles: notes.map((note) => note.title),
      tags: [...tags].sort(),
      propertyKeys: [...propertyKeys].sort(),
    };
  }, [tree]);

  const titleIndex = useMemo(() => {
    const index: Record<string, string> = {};
    for (const note of tree?.notes ?? []) {
      index[note.title.toLowerCase()] = note.id;
      for (const alias of note.aliases) index[alias.toLowerCase()] = note.id;
    }
    return index;
  }, [tree]);

  if (!openNote || !activeNoteId) {
    return (
      <div className="editor-pane editor-pane--empty">
        <p className="empty-state">
          Select a note in the tree, or press <kbd>Ctrl</kbd>+<kbd>N</kbd> to
          create one.
        </p>
      </div>
    );
  }

  const content = state.draft ?? openNote.content;

  return (
    <div className="editor-pane">
      <div className="tabs" role="tablist" aria-label="Open notes">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={`tab ${tab.id === activeNoteId ? "tab--active" : ""}`}
          >
            <button
              type="button"
              role="tab"
              aria-selected={tab.id === activeNoteId}
              className="tab__label"
              onClick={() => void state.openNoteById(tab.id)}
            >
              {tab.title}
              {dirty[tab.id] ? (
                <span className="tab__dirty" aria-label="Unsaved changes">
                  •
                </span>
              ) : null}
            </button>
            <button
              type="button"
              className="tab__close"
              aria-label={`Close ${tab.title}`}
              onClick={() => state.closeTab(tab.id)}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="editor-pane__bar">
        <h1 className="editor-pane__title">{openNote.metadata.title}</h1>
        <span className="editor-pane__path mono">
          {openNote.metadata.folder_path
            ? `${openNote.metadata.folder_path}/${openNote.metadata.title}.md`
            : `${openNote.metadata.title}.md`}
        </span>

        <div className="segmented" role="group" aria-label="View mode">
          {MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              className={`segmented__option ${viewMode === mode.value ? "segmented__option--active" : ""}`}
              aria-pressed={viewMode === mode.value}
              onClick={() => state.setViewMode(mode.value)}
            >
              {mode.label}
            </button>
          ))}
        </div>

        <span className="editor-pane__status mono" role="status">
          {saving ? "saving…" : dirty[activeNoteId] ? "unsaved" : "saved"}
        </span>
      </div>

      {state.externalChange ? (
        <p className="editor-pane__conflict" role="alert">
          This file changed on disk outside Strata.{" "}
          <button
            type="button"
            className="button"
            onClick={() => void state.reloadOpenNote()}
          >
            Reload from disk
          </button>{" "}
          <button
            type="button"
            className="button"
            onClick={() => state.keepLocalEdits()}
          >
            Keep my edits
          </button>
        </p>
      ) : null}

      <div className={`editor-pane__body editor-pane__body--${viewMode}`}>
        {viewMode !== "reading" && (
          <MarkdownEditor
            noteId={activeNoteId}
            initialContent={openNote.content}
            suggestions={suggestions}
            onChange={(value) => state.setDraft(activeNoteId, value)}
            onSave={(value) => void state.saveNote(activeNoteId, value)}
          />
        )}

        {viewMode !== "source" && (
          <MarkdownPreview
            content={content}
            titleIndex={titleIndex}
            onOpenNote={(id) => void state.openNoteById(id)}
          />
        )}
      </div>
    </div>
  );
}
