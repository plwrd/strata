/** Runs the layout worker and hands back positions. */

import { useEffect, useRef, useState } from "react";
import type { GraphSnapshot } from "../../bridge/types";
import type {
  LayoutRequest,
  LayoutResult,
} from "../../workers/graphLayout.worker";

export type Positions = Record<string, [number, number, number]>;

function provisionalPosition(
  index: number,
  total: number,
): [number, number, number] {
  // Scatter newcomers near the origin so edges can draw immediately while the
  // worker settles a full layout (important after unlocking a private layer).
  const angle = (index / Math.max(total, 1)) * Math.PI * 2;
  const radius = 8 + (index % 5);
  return [Math.cos(angle) * radius, Math.sin(angle) * radius, (index % 3) - 1];
}

export function useGraphLayout(
  graph: GraphSnapshot | null,
  dimension: "2d" | "3d",
  quality: "high" | "balanced" | "low-gpu",
): { positions: Positions; computing: boolean } {
  const [positions, setPositions] = useState<Positions>({});
  const [computing, setComputing] = useState(false);
  const workerRef = useRef<Worker | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    const worker = new Worker(
      new URL("../../workers/graphLayout.worker.ts", import.meta.url),
      {
        type: "module",
      },
    );
    workerRef.current = worker;
    return () => {
      worker.terminate();
      workerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const worker = workerRef.current;
    if (!worker || !graph || graph.nodes.length === 0) {
      setPositions({});
      setComputing(false);
      return;
    }

    // Keep known positions; give brand-new nodes (e.g. just-unlocked layer)
    // provisional coordinates so 2D/3D can connect them before the worker returns.
    setPositions((previous) => {
      const next: Positions = {};
      graph.nodes.forEach((node, index) => {
        next[node.id] =
          previous[node.id] ?? provisionalPosition(index, graph.nodes.length);
      });
      return next;
    });

    const requestId = ++requestIdRef.current;
    setComputing(true);
    const handle = (event: MessageEvent<LayoutResult>): void => {
      if (requestIdRef.current !== requestId) return;
      setPositions(event.data.positions);
      setComputing(false);
    };
    worker.addEventListener("message", handle);

    const request: LayoutRequest = {
      nodes: graph.nodes.map((node) => ({ id: node.id, degree: node.degree })),
      edges: graph.edges.map((edge) => ({
        source: edge.source,
        target: edge.target,
        weight: edge.weight,
      })),
      dimension,
      quality,
    };
    worker.postMessage(request);

    return () => worker.removeEventListener("message", handle);
  }, [graph, dimension, quality]);

  return { positions, computing };
}
