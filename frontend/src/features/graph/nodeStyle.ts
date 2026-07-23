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

export function nodeColor(node: GraphNode, selected: boolean): string {
  if (selected) return cssToken("--graph-node-selected", "#ffffff");
  if (node.locked) return cssToken("--graph-node-locked", "#47506a");
  if (node.cluster >= 0)
    return CLUSTER_PALETTE[node.cluster % CLUSTER_PALETTE.length]!;
  return cssToken(TOKEN_BY_TYPE[node.type] ?? "--graph-node-default");
}

/**
 * The halo around a node. Unselected nodes glow in their own hue (the galaxy);
 * a selected node's glow shifts to bright ignition-gold — a colour deliberately
 * absent from the node palette, so selection reads instantly at any zoom.
 */
export function glowColor(node: GraphNode, selected: boolean): string {
  if (selected) return cssToken("--graph-glow-selected", "#ffe566");
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

export function edgeColor(selected: boolean, origin: string): string {
  if (selected)
    return cssToken("--graph-edge-selected", "rgba(34,224,245,0.95)");
  if (origin === "ai-suggested")
    return cssToken("--graph-edge-ai", "rgba(160,107,255,0.8)");
  return cssToken("--graph-edge-default", "rgba(111,127,168,0.35)");
}
