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

export interface LayoutRequest {
  nodes: { id: string; degree: number }[];
  edges: { source: string; target: string; weight: number }[];
  dimension: "2d" | "3d";
  /** Fewer ticks on low-GPU / battery-saver machines. */
  quality: "high" | "balanced" | "low-gpu";
}

export interface LayoutResult {
  positions: Record<string, [number, number, number]>;
  ticks: number;
}

interface Node extends SimulationNodeDatum {
  id: string;
  degree: number;
}

type Link = SimulationLinkDatum<Node> & { weight: number };

const TICKS = { high: 400, balanced: 260, "low-gpu": 120 } as const;
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

export function computeLayout(request: LayoutRequest): LayoutResult {
  const nodes: Node[] = request.nodes.map((node) => ({
    id: node.id,
    degree: node.degree,
  }));
  const index = new Set(nodes.map((node) => node.id));
  const links: Link[] = request.edges
    .filter((edge) => index.has(edge.source) && index.has(edge.target))
    .map((edge) => ({
      source: edge.source,
      target: edge.target,
      weight: edge.weight,
    }));

  const ticks = TICKS[request.quality];

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
      forceManyBody<Node>().strength((node) => -90 - node.degree * 14),
    )
    .force(
      "collide",
      forceCollide<Node>().radius((node) => 6 + Math.sqrt(node.degree) * 2),
    )
    .force("center", forceCenter(0, 0))
    .stop();

  simulation.tick(ticks);

  const positions: Record<string, [number, number, number]> = {};
  for (const node of nodes) {
    const z =
      request.dimension === "3d"
        ? (hash01(node.id) - 0.5) *
          SPREAD *
          (0.4 + Math.min(node.degree, 8) / 10)
        : 0;
    positions[node.id] = [node.x ?? 0, node.y ?? 0, z];
  }
  return { positions, ticks };
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const result = computeLayout(event.data);
  self.postMessage(result);
};
