/**
 * Focus mode.
 *
 * Milestone 1 renders the note read-only: CodeMirror 6, live preview and autosave
 * are Milestone 2. The note *content* is real — read from the Markdown file on
 * disk through the bridge — so this is a working reader, not a mock-up of one.
 * It says so, rather than presenting a disabled editor that looks broken.
 */

import { useStore } from "../../state/store";

export function NoteView(): JSX.Element {
  const { openNote, selectedIds, graph, openNoteById } = useStore();

  if (!openNote) {
    const candidates = (graph?.nodes ?? []).filter(
      (node) =>
        selectedIds.includes(node.id) && !node.locked && node.type === "note",
    );
    return (
      <div className="note-view note-view--empty">
        <p className="empty-state">
          Open a note from the graph (double-click), the tree, or the search
          results.
        </p>
        {candidates.length > 0 && (
          <ul className="note-view__candidates">
            {candidates.slice(0, 8).map((node) => (
              <li key={node.id}>
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => void openNoteById(node.id)}
                >
                  {node.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const { metadata, content } = openNote;

  return (
    <article className="note-view">
      <header className="note-view__header">
        <h1 className="note-view__title">{metadata.title}</h1>
        <p className="note-view__path mono">{metadata.folder_path || "/"}</p>
        <ul className="note-view__tags">
          {metadata.tags.map((tag) => (
            <li key={tag} className="tag">
              #{tag}
            </li>
          ))}
        </ul>
      </header>

      <div className="note-view__banner">
        <span className="tag tag--warning">Read-only</span> The Markdown editor
        (CodeMirror 6, live preview, autosave) arrives in Milestone 2.
      </div>

      {/* Rendered as text, never as HTML: note content is untrusted input. */}
      <pre className="note-view__content">{content}</pre>

      {metadata.links.length > 0 && (
        <footer className="note-view__links">
          <h2 className="sidebar__heading">Outgoing links</h2>
          <ul>
            {metadata.links.map((link) => (
              <li key={`${link.relationship}:${link.target_title}`}>
                <span className="mono">{link.relationship}</span> →{" "}
                {link.target_title}
              </li>
            ))}
          </ul>
        </footer>
      )}
    </article>
  );
}
