/**
 * The one place that decides what a node looks like.
 *
 * Colours come from CSS custom properties so that the high-contrast theme and the
 * low-GPU mode reach the WebGL canvas too — a 3D renderer that hardcodes hex
 * strings is a theme that silently stops at the canvas boundary.
 */

import type { GraphNode, NodeType } from "../../bridge/types";

const TOKEN_BY_TYPE: Record<NodeType, string> = {
  note: "--graph-node-note",
  folder: "--graph-node-folder",
  tag: "--graph-node-tag",
  concept: "--graph-node-concept",
  decision: "--graph-node-decision",
  person: "--graph-node-default",
  project: "--graph-node-default",
  task: "--graph-node-default",
  attachment: "--graph-node-default",
  source: "--graph-node-default",
  cluster: "--graph-node-concept",
  view: "--graph-node-default",
};

let cache: Record<string, string> = {};

export function resetTokenCache(): void {
  cache = {};
}

export function cssToken(name: string, fallback = "#6f7fa8"): string {
  if (cache[name]) return cache[name];
  if (typeof window === "undefined" || !document.documentElement)
    return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  const resolved = value || fallback;
  cache[name] = resolved;
  return resolved;
}

// A fixed palette for cluster colouring — distinct hues that read on the dark
// background. Cluster index modulo the palette length, so any cluster count works.
const CLUSTER_PALETTE = [
  "#22e0f5",
  "#a06bff",
  "#3ddc97",
  "#ffb547",
  "#ff5cc8",
  "#7fe7f5",
  "#ff8a5c",
  "#5c8dff",
];

/** One-hop neighbours of the selection (excluding the selection itself). */
export function neighborIds(
  edges: ReadonlyArray<{ source: string; target: string }>,
  selected: ReadonlySet<string>,
): Set<string> {
  const out = new Set<string>();
  if (selected.size === 0) return out;
  for (const edge of edges) {
    const sourceSelected = selected.has(edge.source);
    const targetSelected = selected.has(edge.target);
    if (sourceSelected && !targetSelected) out.add(edge.target);
    if (targetSelected && !sourceSelected) out.add(edge.source);
  }
  return out;
}

/** True when the edge touches at least one selected node. */
export function edgeIsLit(
  edge: { source: string; target: string },
  selected: ReadonlySet<string>,
): boolean {
  return selected.has(edge.source) || selected.has(edge.target);
}

export function nodeColor(
  node: GraphNode,
  selected: boolean,
  connected = false,
): string {
  // Selection is always pure white — not theme-overridable — so the pick
  // reads the same under every template and colour override.
  if (selected) return "#ffffff";
  // Neighbours of the selection share the bright connected-edge red so the
  // local constellation reads as one highlight, not a mixed palette.
  if (connected) return cssToken("--graph-edge-selected-solid", "#ff2d55");
  if (node.locked) return cssToken("--graph-node-locked", "#47506a");
  if (node.cluster >= 0)
    return CLUSTER_PALETTE[node.cluster % CLUSTER_PALETTE.length]!;
  return cssToken(TOKEN_BY_TYPE[node.type] ?? "--graph-node-default");
}

/**
 * The halo around a node. Unselected nodes glow in their own hue (the galaxy);
 * a selected node's glow shifts to bright ignition-gold — a colour deliberately
 * absent from the node palette, so selection reads instantly at any zoom.
 * Connected neighbours glow in the same red as their lit edges.
 */
export function glowColor(
  node: GraphNode,
  selected: boolean,
  connected = false,
): string {
  if (selected) return cssToken("--graph-glow-selected", "#ffe566");
  if (connected) return cssToken("--graph-edge-selected-solid", "#ff2d55");
  if (node.locked) return cssToken("--graph-node-locked", "#47506a");
  if (node.cluster >= 0)
    return CLUSTER_PALETTE[node.cluster % CLUSTER_PALETTE.length]!;
  return cssToken(TOKEN_BY_TYPE[node.type] ?? "--graph-node-default");
}

/** Degree-scaled radius, clamped so one hub cannot dominate the view. */
export function nodeRadius(node: GraphNode): number {
  if (node.type === "tag") return 1.4;
  if (node.type === "folder") return 2.2;
  return Math.min(1.6 + Math.sqrt(node.degree) * 0.55, 5.2);
}

export function edgeColor(selected: boolean, _origin: string): string {
  // Opaque RGB only — THREE.Color ignores alpha and floods the console when
  // given rgba(...), which also hid real GPU warnings during Explore.
  // Incident to selection = bright red; everything else = dark gray.
  if (selected) return cssToken("--graph-edge-selected-solid", "#ff2d55");
  return cssToken("--graph-edge-default-solid", "#3a3f4a");
}
