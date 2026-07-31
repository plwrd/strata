/**
 * The file tree.
 *
 * A real tree over the real folders on disk, with create / rename / move / delete.
 * Every destructive action goes to the trash, never to oblivion — `delete` here
 * means "put it in .strata/trash", and the UI says so.
 *
 * Drag-and-drop does three things. A note dragged onto a folder (or onto a
 * layer's name, for the root) is moved there; a folder dragged onto another
 * folder (or the layer root) is reparented as a subfolder. The move is
 * performed by Python (which re-checks the path), so a dragged item cannot be
 * dropped outside its layer. Files dragged in from the operating system are
 * imported: Markdown and plain text become notes, everything else becomes an
 * attachment wrapped in a note — and in a private layer the bytes are
 * encrypted before they touch the disk.
 *
 * Density (List / Large), a tree-scoped context menu, and keyboard navigation
 * live here. The global app menu stands down inside this panel.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { NoteMetadata, TreeFolder } from "../../bridge/types";
import { useStore } from "../../state/store";
import { readDroppedFiles } from "./importDrop";

interface TreeNode {
  folder: TreeFolder | null;
  path: string;
  name: string;
  layerId: string;
  children: TreeNode[];
  notes: NoteMetadata[];
}

type MenuTarget =
  | { kind: "note"; note: NoteMetadata }
  | { kind: "folder"; folder: TreeFolder; path: string; layerId: string }
  | { kind: "layer"; layerId: string; displayName: string }
  | { kind: "trash" };

interface MenuItem {
  id: string;
  label: string;
  hint?: string;
  danger?: boolean;
  run: () => void;
}

interface MenuState {
  x: number;
  y: number;
  target: MenuTarget;
}

function buildTree(
  folders: TreeFolder[],
  notes: NoteMetadata[],
  layerId: string,
): TreeNode {
  const root: TreeNode = {
    folder: null,
    path: "",
    name: "/",
    layerId,
    children: [],
    notes: [],
  };
  const byPath = new Map<string, TreeNode>([["", root]]);

  for (const folder of [...folders].sort((a, b) =>
    a.path.localeCompare(b.path),
  )) {
    if (folder.layer_id !== layerId) continue;
    const node: TreeNode = {
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

function FolderIcon(): JSX.Element {
  return (
    <svg
      className="tree__icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M3 6.5A1.5 1.5 0 0 1 4.5 5H10l2 2h7.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11Z"
      />
    </svg>
  );
}

function NoteIcon(): JSX.Element {
  return (
    <svg
      className="tree__icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M6 3.5A1.5 1.5 0 0 1 7.5 2h6.4L19 7.1V20.5A1.5 1.5 0 0 1 17.5 22h-10A1.5 1.5 0 0 1 6 20.5v-17ZM13 3.2V8h4.7L13 3.2Z"
      />
    </svg>
  );
}

function moveTreeFocus(from: HTMLElement, delta: number): void {
  const root = from.closest(".tree");
  const items = Array.from(
    root?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [],
  );
  const index = items.indexOf(from);
  if (index < 0) return;
  const next = items[Math.max(0, Math.min(items.length - 1, index + delta))];
  next?.focus();
}

function focusTreeItem(root: HTMLElement | null, treeId: string): void {
  root
    ?.querySelector<HTMLElement>(`[role="treeitem"][data-tree-id="${treeId}"]`)
    ?.focus();
}

export function FileTree(): JSX.Element {
  const state = useStore();
  const treeRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);

  const layers = state.layers.filter((layer) => layer.state !== "locked");
  const density = state.explorerDensity;
  const frozen = state.explorerFrozen;

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

  const toggleCollapsed = useCallback((path: string): void => {
    setCollapsed((current) => ({ ...current, [path]: !current[path] }));
  }, []);

  const confirmEmptyTrash = useCallback((): void => {
    if (
      !window.confirm(
        "Permanently delete everything in the trash? This cannot be undone.",
      )
    ) {
      return;
    }
    void state.emptyTrash();
  }, [state]);

  const closeMenu = useCallback(() => setMenu(null), []);

  const openMenu = (event: React.MouseEvent, target: MenuTarget): void => {
    event.preventDefault();
    event.stopPropagation();
    setMenu({ x: event.clientX, y: event.clientY, target });
  };

  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") closeMenu();
    };
    const onDown = (event: MouseEvent): void => {
      if (!menuRef.current?.contains(event.target as Node)) closeMenu();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("blur", closeMenu);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("blur", closeMenu);
    };
  }, [menu, closeMenu]);

  useLayoutEffect(() => {
    const el = menuRef.current;
    if (!el || !menu) return;
    const rect = el.getBoundingClientRect();
    const x = Math.min(menu.x, window.innerWidth - rect.width - 8);
    const y = Math.min(menu.y, window.innerHeight - rect.height - 8);
    el.style.left = `${Math.max(8, x)}px`;
    el.style.top = `${Math.max(8, y)}px`;
    el.querySelector<HTMLButtonElement>("[role='menuitem']")?.focus();
  }, [menu]);

  const handleDrop = async (
    event: React.DragEvent,
    layerId: string,
    path: string,
  ): Promise<void> => {
    event.preventDefault();
    setDropTarget(null);
    if (state.explorerFrozen) return;

    const noteId = event.dataTransfer.getData("text/strata-note");
    if (noteId) {
      const note = (state.tree?.notes ?? []).find((entry) => entry.id === noteId);
      if (note && note.layer_id !== layerId) {
        useStore.setState({
          connectionMessage: "Notes stay inside their layer — drop within the same layer.",
        });
        return;
      }
      await state.moveNote(noteId, path);
      return;
    }

    const folderPayload = event.dataTransfer.getData("text/strata-folder");
    if (folderPayload) {
      try {
        const dragged = JSON.parse(folderPayload) as {
          id: string;
          layerId: string;
          path: string;
        };
        if (dragged.layerId !== layerId) {
          useStore.setState({
            connectionMessage:
              "Folders stay inside their layer — drop within the same layer.",
          });
          return;
        }
        if (
          path === dragged.path ||
          path.startsWith(`${dragged.path}/`)
        ) {
          return;
        }
        await state.moveFolder(dragged.id, path);
        if (path) {
          setCollapsed((current) => ({ ...current, [path]: false }));
        }
      } catch {
        // Ignore malformed drag payloads from other apps.
      }
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
  > => {
    if (frozen) return {};
    return {
      onDragOver: (event) => {
        event.preventDefault();
        setDropTarget(key);
      },
      onDragLeave: () =>
        setDropTarget((current) => (current === key ? null : current)),
      onDrop: (event) => void handleDrop(event, layerId, path),
    };
  };

  const menuItems = (target: MenuTarget): MenuItem[] => {
    switch (target.kind) {
      case "note":
        return [
          {
            id: "open",
            label: "Open",
            hint: "Enter",
            run: () => void state.openNoteById(target.note.id),
          },
          {
            id: "rename",
            label: "Rename",
            hint: "F2",
            run: () => startRename(target.note.id, target.note.title),
          },
          {
            id: "duplicate",
            label: "Duplicate",
            hint: "Ctrl+D",
            run: () => void state.duplicateNote(target.note.id),
          },
          {
            id: "trash",
            label: "Move to trash",
            hint: "Delete",
            danger: true,
            run: () => void state.deleteNote(target.note.id),
          },
        ];
      case "folder": {
        const isCollapsed = collapsed[target.path] ?? false;
        return [
          {
            id: "new-note",
            label: "New note",
            run: () => void state.createNote(target.layerId, target.path),
          },
          {
            id: "new-folder",
            label: "New subfolder",
            run: () => void state.createFolder(target.layerId, target.path),
          },
          {
            id: "rename",
            label: "Rename",
            hint: "F2",
            run: () => startRename(target.folder.id, target.folder.name),
          },
          {
            id: "toggle",
            label: isCollapsed ? "Expand" : "Collapse",
            hint: isCollapsed ? "→" : "←",
            run: () => toggleCollapsed(target.path),
          },
          {
            id: "trash",
            label: "Move to trash",
            hint: "Delete",
            danger: true,
            run: () => void state.deleteFolder(target.folder.id),
          },
        ];
      }
      case "layer":
        return [
          {
            id: "new-note",
            label: "New note",
            run: () => void state.createNote(target.layerId, ""),
          },
          {
            id: "new-folder",
            label: "New folder",
            run: () => void state.createFolder(target.layerId, ""),
          },
        ];
      case "trash":
        return [
          {
            id: "empty",
            label: "Empty trash",
            danger: true,
            run: confirmEmptyTrash,
          },
        ];
    }
  };

  const onFolderKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
    folder: TreeFolder,
    path: string,
    layerId: string,
  ): void => {
    if (renaming) return;
    const isCollapsed = collapsed[path] ?? false;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveTreeFocus(event.currentTarget, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveTreeFocus(event.currentTarget, -1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (!isCollapsed) {
        toggleCollapsed(path);
      } else {
        const parentPath = path.includes("/")
          ? path.slice(0, path.lastIndexOf("/"))
          : "";
        if (parentPath) {
          const parent = (state.tree?.folders ?? []).find(
            (entry) => entry.layer_id === layerId && entry.path === parentPath,
          );
          if (parent) {
            focusTreeItem(treeRef.current, `folder:${parent.id}`);
          }
        }
      }
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      if (isCollapsed) {
        toggleCollapsed(path);
      } else {
        moveTreeFocus(event.currentTarget, 1);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      toggleCollapsed(path);
    } else if (event.key === "F2") {
      event.preventDefault();
      startRename(folder.id, folder.name);
    } else if (event.key === "Delete") {
      event.preventDefault();
      void state.deleteFolder(folder.id);
    }
  };

  const onNoteKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
    note: NoteMetadata,
  ): void => {
    if (renaming) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveTreeFocus(event.currentTarget, 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveTreeFocus(event.currentTarget, -1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      const parent = (state.tree?.folders ?? []).find(
        (entry) =>
          entry.layer_id === note.layer_id && entry.path === note.folder_path,
      );
      if (parent) {
        focusTreeItem(treeRef.current, `folder:${parent.id}`);
      }
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      moveTreeFocus(event.currentTarget, 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void state.openNoteById(note.id);
    } else if (event.key === "F2") {
      event.preventDefault();
      startRename(note.id, note.title);
    } else if (event.key === "Delete") {
      event.preventDefault();
      void state.deleteNote(note.id);
    } else if (
      event.key.toLowerCase() === "d" &&
      (event.ctrlKey || event.metaKey)
    ) {
      event.preventDefault();
      void state.duplicateNote(note.id);
    }
  };

  const renderNode = (node: TreeNode, depth: number): JSX.Element => {
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
            data-tree-id={`folder:${node.folder.id}`}
            data-kind="folder"
            data-path={node.path}
            draggable={!frozen}
            onDragStart={
              frozen
                ? undefined
                : (event) => {
                    event.dataTransfer.setData(
                      "text/strata-folder",
                      JSON.stringify({
                        id: node.folder!.id,
                        layerId: node.layerId,
                        path: node.path,
                      }),
                    );
                    event.dataTransfer.effectAllowed = "move";
                  }
            }
            {...dropProps(key, node.layerId, node.path)}
            onContextMenu={(event) =>
              openMenu(event, {
                kind: "folder",
                folder: node.folder!,
                path: node.path,
                layerId: node.layerId,
              })
            }
            onKeyDown={(event) =>
              onFolderKeyDown(event, node.folder!, node.path, node.layerId)
            }
          >
            <button
              type="button"
              className="tree__twisty"
              aria-label={isCollapsed ? "Expand" : "Collapse"}
              tabIndex={-1}
              onClick={() => toggleCollapsed(node.path)}
            >
              {isCollapsed ? "▸" : "▾"}
            </button>
            <FolderIcon />

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
                  data-tree-id={`note:${note.id}`}
                  data-kind="note"
                  draggable={!frozen}
                  onDragStart={
                    frozen
                      ? undefined
                      : (event) =>
                          event.dataTransfer.setData(
                            "text/strata-note",
                            note.id,
                          )
                  }
                  onContextMenu={(event) =>
                    openMenu(event, { kind: "note", note })
                  }
                  onKeyDown={(event) => onNoteKeyDown(event, note)}
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
                      <NoteIcon />
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
    <section
      ref={treeRef}
      className="tree"
      aria-label="Files"
      data-tour="files"
      data-density={density}
      data-frozen={frozen ? "true" : "false"}
    >
      <div className="tree__header">
        <h2 className="sidebar__heading">Files</h2>
        <div className="tree__header-actions" role="group" aria-label="View">
          <button
            type="button"
            className={`tree__density ${frozen ? "tree__density--active" : ""}`}
            aria-pressed={frozen}
            title={
              frozen
                ? "Unfreeze — allow drag and drop"
                : "Freeze — disable drag and drop"
            }
            onClick={() => state.setExplorerFrozen(!frozen)}
          >
            {frozen ? "Frozen" : "Freeze"}
          </button>
          <button
            type="button"
            className={`tree__density ${density === "list" ? "tree__density--active" : ""}`}
            aria-pressed={density === "list"}
            title="List view"
            onClick={() => state.setExplorerDensity("list")}
          >
            List
          </button>
          <button
            type="button"
            className={`tree__density ${density === "large" ? "tree__density--active" : ""}`}
            aria-pressed={density === "large"}
            title="Large icons"
            onClick={() => state.setExplorerDensity("large")}
          >
            Large
          </button>
        </div>
      </div>

      {trees.map(({ layer, root }) => (
        <div key={layer.id} className="tree__layer">
          <div
            className={`tree__layer-row ${dropTarget === `layer:${layer.id}` ? "tree__row--drop" : ""}`}
            {...dropProps(`layer:${layer.id}`, layer.id, "")}
            onContextMenu={(event) =>
              openMenu(event, {
                kind: "layer",
                layerId: layer.id,
                displayName: layer.display_name,
              })
            }
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
        <details
          className="tree__trash"
          onContextMenu={(event) => openMenu(event, { kind: "trash" })}
        >
          <summary>
            Trash ({state.trash.length})
            <button
              type="button"
              className="button button--ghost tree__empty-trash"
              onClick={(event) => {
                event.preventDefault();
                confirmEmptyTrash();
              }}
            >
              Empty trash
            </button>
          </summary>
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

      {menu && (
        <div
          ref={menuRef}
          className="context-menu"
          role="menu"
          aria-label="Files menu"
          onKeyDown={(event) => {
            const items = [
              ...(menuRef.current?.querySelectorAll<HTMLButtonElement>(
                "[role='menuitem']",
              ) ?? []),
            ];
            const index = items.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            if (event.key === "ArrowDown") {
              event.preventDefault();
              items[(index + 1) % items.length]?.focus();
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              items[(index - 1 + items.length) % items.length]?.focus();
            }
          }}
        >
          {menuItems(menu.target).map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className={`context-menu__item ${item.danger ? "context-menu__item--danger" : ""}`}
              onClick={() => {
                item.run();
                closeMenu();
              }}
            >
              <span>{item.label}</span>
              {item.hint && (
                <kbd className="context-menu__hint">{item.hint}</kbd>
              )}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
