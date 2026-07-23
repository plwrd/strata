/**
 * The file tree.
 *
 * A real tree over the real folders on disk, with create / rename / move / delete.
 * Every destructive action goes to the trash, never to oblivion — `delete` here
 * means "put it in .strata/trash", and the UI says so.
 *
 * Drag-and-drop does two things. A note dragged onto a folder (or onto a layer's
 * name, for the root) is moved there; the move is performed by Python (which
 * re-checks the path), so a dragged item cannot be dropped outside the layer.
 * Files dragged in from the operating system are imported: Markdown and plain
 * text become notes, everything else becomes an attachment wrapped in a note —
 * and in a private layer the bytes are encrypted before they touch the disk.
 */

import { useMemo, useState } from "react";
import type { NoteMetadata, TreeFolder } from "../../bridge/types";
import { useStore } from "../../state/store";
import { readDroppedFiles } from "./importDrop";

interface Node {
  folder: TreeFolder | null;
  path: string;
  name: string;
  layerId: string;
  children: Node[];
  notes: NoteMetadata[];
}

function buildTree(
  folders: TreeFolder[],
  notes: NoteMetadata[],
  layerId: string,
): Node {
  const root: Node = {
    folder: null,
    path: "",
    name: "/",
    layerId,
    children: [],
    notes: [],
  };
  const byPath = new Map<string, Node>([["", root]]);

  for (const folder of [...folders].sort((a, b) =>
    a.path.localeCompare(b.path),
  )) {
    if (folder.layer_id !== layerId) continue;
    const node: Node = {
      folder,
      path: folder.path,
      name: folder.name,
      layerId,
      children: [],
      notes: [],
    };
    byPath.set(folder.path, node);
    const parentPath = folder.path.includes("/")
      ? folder.path.slice(0, folder.path.lastIndexOf("/"))
      : "";
    (byPath.get(parentPath) ?? root).children.push(node);
  }

  for (const note of notes) {
    if (note.layer_id !== layerId) continue;
    (byPath.get(note.folder_path) ?? root).notes.push(note);
  }

  return root;
}

export function FileTree(): JSX.Element {
  const state = useStore();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const layers = state.layers.filter((layer) => layer.state !== "locked");

  const trees = useMemo(
    () =>
      layers.map((layer) => ({
        layer,
        root: buildTree(
          state.tree?.folders ?? [],
          state.tree?.notes ?? [],
          layer.id,
        ),
      })),
    [layers, state.tree],
  );

  const startRename = (id: string, current: string): void => {
    setRenaming(id);
    setDraft(current);
  };

  const commitRename = async (
    id: string,
    kind: "note" | "folder",
  ): Promise<void> => {
    const name = draft.trim();
    setRenaming(null);
    if (!name) return;
    if (kind === "note") await state.renameNote(id, name);
    else await state.renameFolder(id, name);
  };

  // One drop handler for folders and layer roots: a dragged note is a move, a
  // drag from the operating system is an import.
  const handleDrop = async (
    event: React.DragEvent,
    layerId: string,
    path: string,
  ): Promise<void> => {
    event.preventDefault();
    setDropTarget(null);
    const noteId = event.dataTransfer.getData("text/strata-note");
    if (noteId) {
      await state.moveNote(noteId, path);
      return;
    }
    if (event.dataTransfer.files.length > 0) {
      const files = await readDroppedFiles([...event.dataTransfer.files]);
      await state.importFiles(layerId, path, files);
    }
  };

  const dropProps = (
    key: string,
    layerId: string,
    path: string,
  ): Pick<
    React.HTMLAttributes<HTMLDivElement>,
    "onDragOver" | "onDragLeave" | "onDrop"
  > => ({
    onDragOver: (event) => {
      event.preventDefault();
      setDropTarget(key);
    },
    onDragLeave: () =>
      setDropTarget((current) => (current === key ? null : current)),
    onDrop: (event) => void handleDrop(event, layerId, path),
  });

  const renderNode = (node: Node, depth: number): JSX.Element => {
    const isCollapsed = collapsed[node.path] ?? false;
    const key = node.folder?.id ?? `root:${node.layerId}`;

    return (
      <li key={key} role="none">
        {node.folder && (
          <div
            className={`tree__row tree__row--folder ${dropTarget === key ? "tree__row--drop" : ""}`}
            style={{ paddingLeft: `${depth * 12}px` }}
            role="treeitem"
            aria-expanded={!isCollapsed}
            tabIndex={0}
            {...dropProps(key, node.layerId, node.path)}
          >
            <button
              type="button"
              className="tree__twisty"
              aria-label={isCollapsed ? "Expand" : "Collapse"}
              onClick={() =>
                setCollapsed((c) => ({ ...c, [node.path]: !isCollapsed }))
              }
            >
              {isCollapsed ? "▸" : "▾"}
            </button>

            {renaming === node.folder.id ? (
              <input
                className="input tree__input"
                value={draft}
                autoFocus
                aria-label="Folder name"
                onChange={(event) => setDraft(event.target.value)}
                onBlur={() => void commitRename(node.folder!.id, "folder")}
                onKeyDown={(event) => {
                  if (event.key === "Enter")
                    void commitRename(node.folder!.id, "folder");
                  if (event.key === "Escape") setRenaming(null);
                }}
              />
            ) : (
              <>
                <span className="tree__name">{node.name}</span>
                <span className="tree__actions">
                  <button
                    type="button"
                    className="tree__action"
                    title="New note here"
                    onClick={() =>
                      void state.createNote(node.layerId, node.path)
                    }
                  >
                    ＋
                  </button>
                  <button
                    type="button"
                    className="tree__action"
                    title="New subfolder"
                    onClick={() =>
                      void state.createFolder(node.layerId, node.path)
                    }
                  >
                    🗀
                  </button>
                  <button
                    type="button"
                    className="tree__action"
                    title="Rename folder"
                    onClick={() => startRename(node.folder!.id, node.name)}
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    className="tree__action tree__action--danger"
                    title="Move folder and its notes to the trash"
                    onClick={() => void state.deleteFolder(node.folder!.id)}
                  >
                    🗑
                  </button>
                </span>
              </>
            )}
          </div>
        )}

        {!isCollapsed && (
          <ul role="group">
            {node.children.map((child) => renderNode(child, depth + 1))}

            {node.notes.map((note) => (
              <li key={note.id} role="none">
                <div
                  className={`tree__row ${state.activeNoteId === note.id ? "tree__row--active" : ""}`}
                  style={{ paddingLeft: `${(depth + 1) * 12 + 14}px` }}
                  role="treeitem"
                  aria-selected={state.activeNoteId === note.id}
                  tabIndex={0}
                  draggable
                  onDragStart={(event) =>
                    event.dataTransfer.setData("text/strata-note", note.id)
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void state.openNoteById(note.id);
                    if (event.key === "F2") startRename(note.id, note.title);
                    if (event.key === "Delete") void state.deleteNote(note.id);
                  }}
                >
                  {renaming === note.id ? (
                    <input
                      className="input tree__input"
                      value={draft}
                      autoFocus
                      aria-label="Note title"
                      onChange={(event) => setDraft(event.target.value)}
                      onBlur={() => void commitRename(note.id, "note")}
                      onKeyDown={(event) => {
                        if (event.key === "Enter")
                          void commitRename(note.id, "note");
                        if (event.key === "Escape") setRenaming(null);
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className="tree__name tree__name--note"
                        onClick={() => void state.openNoteById(note.id)}
                      >
                        {note.title}
                      </button>
                      <span className="tree__actions">
                        <button
                          type="button"
                          className="tree__action"
                          title="Rename (F2)"
                          onClick={() => startRename(note.id, note.title)}
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          className="tree__action"
                          title="Duplicate"
                          onClick={() => void state.duplicateNote(note.id)}
                        >
                          ⧉
                        </button>
                        <button
                          type="button"
                          className="tree__action tree__action--danger"
                          title="Move to trash (Delete)"
                          onClick={() => void state.deleteNote(note.id)}
                        >
                          🗑
                        </button>
                      </span>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </li>
    );
  };

  return (
    <section className="tree" aria-label="Files" data-tour="files">
      <div className="tree__header">
        <h2 className="sidebar__heading">Files</h2>
      </div>

      {trees.map(({ layer, root }) => (
        <div key={layer.id} className="tree__layer">
          <div
            className={`tree__layer-row ${dropTarget === `layer:${layer.id}` ? "tree__row--drop" : ""}`}
            {...dropProps(`layer:${layer.id}`, layer.id, "")}
          >
            <span className="tree__layer-name mono">{layer.display_name}</span>
            <span className="tree__actions">
              <button
                type="button"
                className="tree__action"
                title={`New note in ${layer.display_name}`}
                onClick={() => void state.createNote(layer.id, "")}
              >
                ＋
              </button>
              <button
                type="button"
                className="tree__action"
                title={`New folder in ${layer.display_name}`}
                onClick={() => void state.createFolder(layer.id, "")}
              >
                🗀
              </button>
            </span>
          </div>
          <ul
            role="tree"
            aria-label={`Files in ${layer.display_name}`}
            className="tree__list"
          >
            {renderNode(root, 0)}
          </ul>
        </div>
      ))}

      {state.trash.length > 0 && (
        <details className="tree__trash">
          <summary>Trash ({state.trash.length})</summary>
          <ul>
            {state.trash.map((entry) => (
              <li key={entry.entry} className="tree__trash-item">
                <span>{entry.title}</span>
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => void state.restoreNote(entry.entry)}
                >
                  Restore
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
