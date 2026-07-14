/** Runs the layout worker and hands back positions. */

import { useEffect, useRef, useState } from "react";
import type { GraphSnapshot } from "../../bridge/types";
import type {
  LayoutRequest,
  LayoutResult,
} from "../../workers/graphLayout.worker";

export type Positions = Record<string, [number, number, number]>;

export function useGraphLayout(
  graph: GraphSnapshot | null,
  dimension: "2d" | "3d",
  quality: "high" | "balanced" | "low-gpu",
): { positions: Positions; computing: boolean } {
  const [positions, setPositions] = useState<Positions>({});
  const [computing, setComputing] = useState(false);
  const workerRef = useRef<Worker | null>(null);

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
      return;
    }

    setComputing(true);
    const handle = (event: MessageEvent<LayoutResult>) => {
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
