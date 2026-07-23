/**
 * Pure geometry builders for the galaxy rendering of the 3D graph.
 *
 * Everything here is deterministic, DOM-free, and WebGL-free so it can be unit
 * tested; the shader components in `effects.tsx` consume these buffers verbatim.
 * The design constraint throughout: all per-frame animation happens on the GPU
 * (a single `uTime` uniform), so a 10k-node galaxy costs the CPU nothing.
 */

import * as THREE from "three";
import type { GraphEdge, GraphNode } from "../../bridge/types";
import type { Positions } from "../graph/useGraphLayout";
import { edgeColor, glowColor, nodeRadius } from "../graph/nodeStyle";

/** Deterministic PRNG (mulberry32): same seed, same galaxy, stable frames. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface StarfieldData {
  positions: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  phases: Float32Array;
  count: number;
}

// A cool-to-warm palette of star tints; mostly white so the graph stays the star.
const STAR_TINTS: [number, number, number][] = [
  [0.9, 0.95, 1.0],
  [1.0, 1.0, 1.0],
  [0.75, 0.85, 1.0],
  [0.85, 0.8, 1.0],
  [1.0, 0.92, 0.8],
];

/** A spherical shell of background stars between innerRadius and outerRadius. */
export function buildStarfield(
  count: number,
  innerRadius: number,
  outerRadius: number,
  seed = 42,
): StarfieldData {
  const random = mulberry32(seed);
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const phases = new Float32Array(count);

  for (let i = 0; i < count; i += 1) {
    // Uniform direction on the sphere (normalised gaussian-ish via trig method).
    const theta = random() * Math.PI * 2;
    const z = random() * 2 - 1;
    const planar = Math.sqrt(1 - z * z);
    const radius = innerRadius + (outerRadius - innerRadius) * random();
    positions[i * 3] = Math.cos(theta) * planar * radius;
    positions[i * 3 + 1] = Math.sin(theta) * planar * radius;
    positions[i * 3 + 2] = z * radius;

    const tint = STAR_TINTS[Math.floor(random() * STAR_TINTS.length)]!;
    colors[i * 3] = tint[0];
    colors[i * 3 + 1] = tint[1];
    colors[i * 3 + 2] = tint[2];

    sizes[i] = 0.6 + random() * 1.8;
    phases[i] = random() * Math.PI * 2;
  }
  return { positions, colors, sizes, phases, count };
}

export interface NebulaData {
  positions: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  phases: Float32Array;
  count: number;
}

// Deep-space dust tints: violet, blue, magenta, teal. Faint by construction —
// the fragment shader keeps alpha low so the nebula reads as atmosphere.
const NEBULA_TINTS: [number, number, number][] = [
  [0.45, 0.3, 0.9],
  [0.2, 0.45, 0.95],
  [0.7, 0.3, 0.8],
  [0.15, 0.6, 0.7],
];

/**
 * Soft dust clouds behind the graph. Puffs are scattered around a handful of
 * cluster centres rather than uniformly, so the backdrop has structure — wisps
 * and voids — instead of even fog. Deterministic like everything else here.
 */
export function buildNebula(
  count: number,
  radius: number,
  seed = 11,
): NebulaData {
  const random = mulberry32(seed);
  const centreCount = Math.max(3, Math.round(count / 30));
  const centres: [number, number, number][] = [];
  for (let i = 0; i < centreCount; i += 1) {
    const theta = random() * Math.PI * 2;
    const z = random() * 2 - 1;
    const planar = Math.sqrt(1 - z * z);
    const r = radius * (0.55 + random() * 0.45);
    centres.push([
      Math.cos(theta) * planar * r,
      Math.sin(theta) * planar * r * 0.6, // flattened: a disc, not a ball
      z * r,
    ]);
  }

  const out: NebulaData = {
    positions: new Float32Array(count * 3),
    colors: new Float32Array(count * 3),
    sizes: new Float32Array(count),
    phases: new Float32Array(count),
    count,
  };
  const spread = radius * 0.22;
  for (let i = 0; i < count; i += 1) {
    const centre = centres[i % centres.length]!;
    out.positions[i * 3] = centre[0] + (random() * 2 - 1) * spread;
    out.positions[i * 3 + 1] = centre[1] + (random() * 2 - 1) * spread * 0.7;
    out.positions[i * 3 + 2] = centre[2] + (random() * 2 - 1) * spread;

    const tint = NEBULA_TINTS[i % NEBULA_TINTS.length]!;
    out.colors[i * 3] = tint[0];
    out.colors[i * 3 + 1] = tint[1];
    out.colors[i * 3 + 2] = tint[2];

    out.sizes[i] = radius * (0.18 + random() * 0.3);
    out.phases[i] = random() * Math.PI * 2;
  }
  return out;
}

export interface GlowData {
  positions: Float32Array;
  colors: Float32Array;
  sizes: Float32Array;
  selected: Float32Array;
  count: number;
}

/** One glow halo per node: colour from the node, hot colour when selected. */
export function buildNodeGlow(
  nodes: GraphNode[],
  positions: Positions,
  selectedIds: Set<string>,
  scale: number,
): GlowData {
  const placed = nodes.filter((node) => positions[node.id] !== undefined);
  const out: GlowData = {
    positions: new Float32Array(placed.length * 3),
    colors: new Float32Array(placed.length * 3),
    sizes: new Float32Array(placed.length),
    selected: new Float32Array(placed.length),
    count: placed.length,
  };
  const color = new THREE.Color();
  placed.forEach((node, i) => {
    const p = positions[node.id]!;
    out.positions[i * 3] = p[0] * scale;
    out.positions[i * 3 + 1] = p[1] * scale;
    out.positions[i * 3 + 2] = p[2] * scale;
    const isSelected = selectedIds.has(node.id);
    color.set(glowColor(node, isSelected));
    out.colors[i * 3] = color.r;
    out.colors[i * 3 + 1] = color.g;
    out.colors[i * 3 + 2] = color.b;
    out.sizes[i] = nodeRadius(node) * (isSelected ? 5.2 : 2.2);
    out.selected[i] = isSelected ? 1 : 0;
  });
  return out;
}

export interface EdgeParticleData {
  /** Named `position` for THREE; holds each particle's start point. */
  starts: Float32Array;
  ends: Float32Array;
  colors: Float32Array;
  offsets: Float32Array;
  speeds: Float32Array;
  count: number;
}

/**
 * Particles that flow along edges. Start/end/phase are baked into attributes;
 * the vertex shader moves them, so animating 6,000 particles is one uniform
 * write per frame.
 */
export function buildEdgeParticles(
  edges: GraphEdge[],
  positions: Positions,
  selectedIds: Set<string>,
  scale: number,
  perEdge: number,
  cap: number,
  seed = 7,
): EdgeParticleData {
  const random = mulberry32(seed);
  const valid = edges.filter(
    (edge) => positions[edge.source] && positions[edge.target],
  );
  const total = Math.min(valid.length * perEdge, cap);
  const out: EdgeParticleData = {
    starts: new Float32Array(total * 3),
    ends: new Float32Array(total * 3),
    colors: new Float32Array(total * 3),
    offsets: new Float32Array(total),
    speeds: new Float32Array(total),
    count: total,
  };
  const color = new THREE.Color();
  for (let i = 0; i < total; i += 1) {
    const edge = valid[i % valid.length]!;
    const from = positions[edge.source]!;
    const to = positions[edge.target]!;
    out.starts[i * 3] = from[0] * scale;
    out.starts[i * 3 + 1] = from[1] * scale;
    out.starts[i * 3 + 2] = from[2] * scale;
    out.ends[i * 3] = to[0] * scale;
    out.ends[i * 3 + 1] = to[1] * scale;
    out.ends[i * 3 + 2] = to[2] * scale;
    const lit = selectedIds.has(edge.source) && selectedIds.has(edge.target);
    color.set(edgeColor(lit, edge.origin));
    // Lit constellation edges carry brighter, faster traffic.
    const boost = lit ? 1.6 : 1.0;
    out.colors[i * 3] = Math.min(color.r * boost, 1);
    out.colors[i * 3 + 1] = Math.min(color.g * boost, 1);
    out.colors[i * 3 + 2] = Math.min(color.b * boost, 1);
    out.offsets[i] = random();
    out.speeds[i] = (0.05 + random() * 0.08) * (lit ? 2.0 : 1.0);
  }
  return out;
}

/**
 * Which nodes deserve a floating label. Selected and hovered nodes always win;
 * the rest of the budget goes to the highest-degree unlocked nodes, so the
 * landmarks of the galaxy are named without drowning it in text.
 */
export function pickLabelled(
  nodes: GraphNode[],
  positions: Positions,
  selectedIds: Set<string>,
  hoveredId: string | null,
  limit: number,
): GraphNode[] {
  const placed = nodes.filter((node) => positions[node.id] !== undefined);
  const chosen = new Map<string, GraphNode>();
  for (const node of placed) {
    if (selectedIds.has(node.id) || node.id === hoveredId) {
      chosen.set(node.id, node);
    }
  }
  const landmarks = placed
    .filter((node) => !chosen.has(node.id) && !node.locked)
    .sort((a, b) => b.degree - a.degree);
  for (const node of landmarks) {
    if (chosen.size >= limit) break;
    if (node.degree < 2) break; // a label on an isolated dot is noise
    chosen.set(node.id, node);
  }
  return [...chosen.values()];
}
