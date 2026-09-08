/** Runs the layout worker and hands back positions. */

import { useEffect, useRef, useState } from "react";
import type { GraphSnapshot } from "../../bridge/types";
import type {
  LayoutRequest,
  LayoutResult,
} from "../../workers/graphLayout.worker";
import { FOLDER_LAYOUT_Z } from "./layoutConstants";

export type Positions = Record<string, [number, number, number]>;

function nearCentroid(
  previous: Positions,
  index: number,
  total: number,
  elevate = false,
): [number, number, number] {
  const values = Object.values(previous);
  let cx = 0;
  let cy = 0;
  let cz = 0;
  if (values.length > 0) {
    for (const point of values) {
      cx += point[0];
      cy += point[1];
      cz += point[2];
    }
    cx /= values.length;
    cy /= values.length;
    cz /= values.length;
  }
  const angle = (index / Math.max(total, 1)) * Math.PI * 2;
  const radius = 12 + (index % 7);
  return [
    cx + Math.cos(angle) * radius,
    cy + Math.sin(angle) * radius,
    elevate ? FOLDER_LAYOUT_Z : cz + ((index % 3) - 1) * 2,
  ];
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
  const positionsRef = useRef<Positions>({});

  useEffect(() => {
    positionsRef.current = positions;
  }, [positions]);

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

    const previous = positionsRef.current;
    // Keep existing nodes where they are; park newcomers near the centre so
    // unlock does not scatter the whole galaxy before the worker returns.
    setPositions(() => {
      const next: Positions = {};
      graph.nodes.forEach((node, index) => {
        const prior = previous[node.id];
        if (prior) {
          next[node.id] =
            node.type === "folder"
              ? [prior[0], prior[1], FOLDER_LAYOUT_Z]
              : prior;
        } else {
          next[node.id] = nearCentroid(
            previous,
            index,
            graph.nodes.length,
            node.type === "folder",
          );
        }
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
      nodes: graph.nodes.map((node) => ({
        id: node.id,
        degree: node.degree,
        type: node.type,
      })),
      edges: graph.edges.map((edge) => ({
        source: edge.source,
        target: edge.target,
        weight: edge.weight,
      })),
      dimension,
      quality,
      seed: previous,
    };
    worker.postMessage(request);

    return () => worker.removeEventListener("message", handle);
  }, [graph, dimension, quality]);

  return { positions, computing };
}
