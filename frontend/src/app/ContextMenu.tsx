/**
 * The application context menu.
 *
 * Right-click anywhere gets Strata's own menu, not the browser's — the native
 * WebEngine menu (with Reload, View source, …) makes no sense inside a desktop
 * shell and is suppressed here via preventDefault (production builds already
 * suppress it at the Qt level; this covers development too).
 *
 * Two deliberate exceptions and one rule:
 * - Editable surfaces (inputs, the CodeMirror editor) keep the native menu:
 *   cut/copy/paste there is OS-integrated and has no Reload anyway.
 * - Every item is wired to a real store action. A menu item that does nothing
 *   is a lie; if a capability does not exist, the item does not exist.
 * - Fully keyboard-operable: Escape closes, arrows move, Enter activates.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useStore } from "../state/store";

interface MenuPosition {
  x: number;
  y: number;
}

interface Item {
  id: string;
  label: string;
  hint?: string;
  danger?: boolean;
  run: () => void;
}

const EDITABLE = 'input, textarea, [contenteditable="true"], .cm-editor';

export function AppContextMenu(): JSX.Element | null {
  const state = useStore();
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setPosition(null), []);

  useEffect(() => {
    const onContextMenu = (event: MouseEvent): void => {
      const target = event.target as HTMLElement | null;
      // Editable surfaces keep the OS cut/copy/paste menu.
      if (target?.closest(EDITABLE)) return;
      event.preventDefault();
      setPosition({ x: event.clientX, y: event.clientY });
    };
    window.addEventListener("contextmenu", onContextMenu);
    return () => window.removeEventListener("contextmenu", onContextMenu);
  }, []);

  useEffect(() => {
    if (!position) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") close();
    };
    const onDown = (event: MouseEvent): void => {
      if (!menuRef.current?.contains(event.target as Node)) close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("blur", close);
    };
  }, [position, close]);

  // Keep the menu on screen and focus its first item.
  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu || !position) return;
    const rect = menu.getBoundingClientRect();
    const x = Math.min(position.x, window.innerWidth - rect.width - 8);
    const y = Math.min(position.y, window.innerHeight - rect.height - 8);
    menu.style.left = `${Math.max(8, x)}px`;
    menu.style.top = `${Math.max(8, y)}px`;
    menu.querySelector<HTMLButtonElement>("[role='menuitem']")?.focus();
  }, [position]);

  if (!position) return null;

  const hasSelection = state.selectedIds.length > 0;
  const unlockedPrivate = state.layers.some(
    (layer) => layer.visibility === "private" && layer.state === "unlocked",
  );
  const firstPublic = state.layers.find(
    (layer) => layer.state === "mounted" || layer.state === "unlocked",
  );
  const exploring = state.mode === "explore";

  const sections: { title: string; items: Item[] }[] = [
    {
      title: "Workspace",
      items: [
        ...(firstPublic
          ? [
              {
                id: "new-note",
                label: "New note",
                hint: "Ctrl+N",
                run: () => void state.createNote(firstPublic.id, ""),
              },
            ]
          : []),
        {
          id: "mode-focus",
          label: "Open editor",
          run: () => state.setMode("focus"),
        },
        {
          id: "mode-explore",
          label: "Explore the graph",
          run: () => state.setMode("explore"),
        },
        {
          id: "mode-views",
          label: "Database views",
          run: () => state.setMode("views"),
        },
      ],
    },
    ...(exploring
      ? [
          {
            title: "Graph",
            items: [
              {
                id: "dimension",
                label:
                  state.dimension === "3d"
                    ? "Switch to 2D graph"
                    : "Switch to 3D galaxy",
                run: () =>
                  state.setDimension(state.dimension === "3d" ? "2d" : "3d"),
              },
              ...(hasSelection
                ? [
                    {
                      id: "clear-selection",
                      label: `Clear selection (${state.selectedIds.length})`,
                      run: () => state.clearSelection(),
                    },
                  ]
                : []),
            ],
          },
        ]
      : []),
    ...(unlockedPrivate
      ? [
          {
            title: "Security",
            items: [
              {
                id: "lock-all",
                label: "Lock all private layers",
                danger: true,
                run: () => void state.lockAllLayers(),
              },
            ],
          },
        ]
      : []),
  ];

  return (
    <div
      ref={menuRef}
      className="context-menu"
      role="menu"
      aria-label="Strata menu"
      // Position is set imperatively (and clamped to the viewport) in the layout
      // effect. It is deliberately NOT an inline style prop: an unrelated store
      // update re-renders this component, and re-applying the raw coords here
      // would undo the clamp and snap the menu off-screen.
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
      {sections
        .filter((section) => section.items.length > 0)
        .map((section) => (
          <div key={section.title} className="context-menu__section">
            <span className="context-menu__heading mono" aria-hidden="true">
              {section.title}
            </span>
            {section.items.map((item) => (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                className={`context-menu__item ${item.danger ? "context-menu__item--danger" : ""}`}
                onClick={() => {
                  item.run();
                  close();
                }}
              >
                <span>{item.label}</span>
                {item.hint && (
                  <kbd className="context-menu__hint">{item.hint}</kbd>
                )}
              </button>
            ))}
          </div>
        ))}
    </div>
  );
}
