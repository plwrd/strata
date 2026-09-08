/**
 * Force-directed layout, off the main thread.
 *
 * Layout is the one genuinely expensive thing the graph does, and it must never
 * compete with typing. The worker receives the topology, ticks the simulation to
 * convergence, and posts back positions. The renderer just draws them.
 *
 * 3D is the same simulation with a synthesised z axis: d3-force is 2D, so z comes
 * from a deterministic radial spread seeded by the node id. That keeps layouts
 * reproducible across runs (a graph that reshuffles itself on every open is
 * disorienting) without shipping a second physics engine.
 *
 * When `seed` positions are provided (e.g. after unlocking a layer), existing
 * nodes warm-start so the constellation does not jump; new nodes spawn near
 * the current centre and a shorter tick budget settles them in.
 */

import {
  forceCenter,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceCollide,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { FOLDER_LAYOUT_Z } from "../features/graph/layoutConstants";

export interface LayoutRequest {
  nodes: { id: string; degree: number; type?: string }[];
  edges: { source: string; target: string; weight: number }[];
  dimension: "2d" | "3d";
  /** Fewer ticks on low-GPU / battery-saver machines. */
  quality: "high" | "balanced" | "low-gpu";
  /** Optional warm-start positions keyed by node id. */
  seed?: Record<string, [number, number, number]>;
}

export interface LayoutResult {
  positions: Record<string, [number, number, number]>;
  ticks: number;
}

interface Node extends SimulationNodeDatum {
  id: string;
  degree: number;
  type?: string;
}

type Link = SimulationLinkDatum<Node> & { weight: number };

const TICKS = { high: 400, balanced: 260, "low-gpu": 120 } as const;
const WARM_TICKS = { high: 120, balanced: 80, "low-gpu": 40 } as const;
const SPREAD = 90;

/** Deterministic 0..1 hash so z is stable for a given node id. */
function hash01(value: string): number {
  let h = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

function centroid(
  seed: Record<string, [number, number, number]>,
): [number, number, number] {
  const values = Object.values(seed);
  if (values.length === 0) return [0, 0, 0];
  let x = 0;
  let y = 0;
  let z = 0;
  for (const point of values) {
    x += point[0];
    y += point[1];
    z += point[2];
  }
  const n = values.length;
  return [x / n, y / n, z / n];
}

export function computeLayout(request: LayoutRequest): LayoutResult {
  const seed = request.seed ?? {};
  const seededCount = request.nodes.filter((node) => seed[node.id]).length;
  const warm = seededCount > 0 && seededCount >= request.nodes.length * 0.35;
  const centre = centroid(seed);

  const nodes: Node[] = request.nodes.map((node, index) => {
    const prior = seed[node.id];
    if (prior) {
      return {
        id: node.id,
        degree: node.degree,
        type: node.type,
        x: prior[0],
        y: prior[1],
        vx: 0,
        vy: 0,
      };
    }
    // Newcomers (unlocked layer notes) spawn near the existing centre.
    const angle = (index / Math.max(request.nodes.length, 1)) * Math.PI * 2;
    const radius = 12 + (index % 7);
    return {
      id: node.id,
      degree: node.degree,
      type: node.type,
      x: centre[0] + Math.cos(angle) * radius,
      y: centre[1] + Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
    };
  });
  const index = new Set(nodes.map((node) => node.id));
  const links: Link[] = request.edges
    .filter((edge) => index.has(edge.source) && index.has(edge.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      weight: edge.weight,
    }));

  const ticks = warm ? WARM_TICKS[request.quality] : TICKS[request.quality];

  const simulation = forceSimulation(nodes)
    .force(
      "link",
      forceLink<Node, Link>(links)
        .id((node) => node.id)
        .distance((link) => 30 + 30 * (1 - link.weight))
        .strength((link) => 0.35 * link.weight),
    )
    .force(
      "charge",
      forceManyBody<Node>().strength((node) =>
        warm ? -40 - node.degree * 6 : -90 - node.degree * 14,
      ),
    )
    .force(
      "collide",
      forceCollide<Node>().radius((node) => 6 + Math.sqrt(node.degree) * 2),
    )
    .force(
      "center",
      // Warm unlocks: pin the centre of mass near where the galaxy already is
      // so newcomers settle without dragging existing stars across the stage.
      forceCenter(warm ? centre[0] : 0, warm ? centre[1] : 0),
    )
    .stop();

  simulation.tick(ticks);

  const positions: Record<string, [number, number, number]> = {};
  for (const node of nodes) {
    const prior = seed[node.id];
    let z = 0;
    if (request.dimension === "3d") {
      if (node.type === "folder") {
        z = FOLDER_LAYOUT_Z;
      } else {
        z =
          prior?.[2] ??
          (hash01(node.id) - 0.5) *
            SPREAD *
            (0.4 + Math.min(node.degree, 8) / 10);
      }
    }
    positions[node.id] = [node.x ?? 0, node.y ?? 0, z];
  }
  return { positions, ticks };
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const result = computeLayout(event.data);
  self.postMessage(result);
};
