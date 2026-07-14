/**
 * The accessible equivalent of the graph.
 *
 * Not a consolation prize: it is a real tree with real selection, real keyboard
 * navigation and the same actions as the canvas, and it is what a screen reader
 * actually reads (both canvases are `aria-hidden`). Anything you can do to a node
 * in 3D, you can do to it here.
 *
 * Keyboard: ↑/↓ move, Enter opens, Space toggles selection, Ctrl+A selects all.
 */

import { useMemo, useRef } from "react";
import type { GraphNode, GraphSnapshot } from "../../bridge/types";

interface GraphListProps {
  graph: GraphSnapshot;
  selectedIds: string[];
  onSelect: (id: string, modifiers: { ctrl: boolean; shift: boolean }) => void;
  onOpen: (id: string) => void;
  onSelectAll: (ids: string[]) => void;
}

const TYPE_GLYPH: Record<string, string> = {
  note: "▪",
  folder: "▸",
  tag: "#",
  concept: "◆",
  decision: "◇",
  source: "❝",
  person: "☺",
  project: "▣",
  task: "☐",
  attachment: "⎘",
  cluster: "◈",
  view: "▤",
};

export function GraphList({
  graph,
  selectedIds,
  onSelect,
  onOpen,
  onSelectAll,
}: GraphListProps): JSX.Element {
  const listRef = useRef<HTMLUListElement>(null);
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  const grouped = useMemo(() => {
    const byLayer = new Map<string, GraphNode[]>();
    for (const node of graph.nodes) {
      const bucket = byLayer.get(node.layer_id) ?? [];
      bucket.push(node);
      byLayer.set(node.layer_id, bucket);
    }
    for (const bucket of byLayer.values()) {
      bucket.sort(
        (a, b) => b.degree - a.degree || a.label.localeCompare(b.label),
      );
    }
    return byLayer;
  }, [graph.nodes]);

  const move = (from: HTMLElement, delta: number): void => {
    const items = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [],
    );
    const index = items.indexOf(from);
    const next = items[Math.max(0, Math.min(items.length - 1, index + delta))];
    next?.focus();
  };

  return (
    <div className="graph-list">
      <p className="graph-list__summary" role="status">
        {graph.total_nodes} nodes, {graph.total_edges} connections.{" "}
        {selectedIds.length > 0
          ? `${selectedIds.length} selected.`
          : "Nothing selected."}
        {graph.locked_layer_ids.length > 0
          ? ` ${graph.locked_layer_ids.length} locked layer(s) are hidden.`
          : ""}
      </p>
      <ul
        ref={listRef}
        className="graph-list__tree"
        role="tree"
        aria-multiselectable="true"
        aria-label="Knowledge graph"
        onKeyDown={(event) => {
          if (event.key === "a" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            onSelectAll(graph.nodes.map((node) => node.id));
          }
        }}
      >
        {Array.from(grouped.entries()).map(([layerId, nodes]) => (
          <li key={layerId} role="none">
            <ul
              role="group"
              aria-label={`Layer ${layerId}`}
              className="graph-list__group"
            >
              {nodes.map((node) => {
                const isSelected = selected.has(node.id);
                return (
                  <li
                    key={node.id}
                    role="treeitem"
                    tabIndex={0}
                    aria-selected={isSelected}
                    aria-label={
                      node.locked
                        ? "Locked knowledge object. Unlock its layer to see it."
                        : `${node.type}: ${node.label}. ${node.degree} connections.`
                    }
                    className={[
                      "graph-list__item",
                      isSelected ? "graph-list__item--selected" : "",
                      node.locked ? "graph-list__item--locked" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={(event) =>
                      onSelect(node.id, {
                        ctrl: event.ctrlKey || event.metaKey,
                        shift: event.shiftKey,
                      })
                    }
                    onKeyDown={(event) => {
                      if (event.key === "ArrowDown") {
                        event.preventDefault();
                        move(event.currentTarget, 1);
                      } else if (event.key === "ArrowUp") {
                        event.preventDefault();
                        move(event.currentTarget, -1);
                      } else if (event.key === "Enter") {
                        event.preventDefault();
                        if (!node.locked) onOpen(node.id);
                      } else if (event.key === " ") {
                        event.preventDefault();
                        onSelect(node.id, { ctrl: true, shift: false });
                      }
                    }}
                  >
                    <span aria-hidden="true" className="graph-list__glyph">
                      {node.locked ? "🔒" : (TYPE_GLYPH[node.type] ?? "▪")}
                    </span>
                    <span className="graph-list__label">
                      {node.locked ? "Locked knowledge object" : node.label}
                    </span>
                    <span
                      className="graph-list__degree mono"
                      aria-hidden="true"
                    >
                      {node.degree}
                    </span>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  );
}
