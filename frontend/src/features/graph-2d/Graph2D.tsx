/**
 * The 2D graph — and the fallback when WebGL is unavailable or refused.
 *
 * Same data, same layout worker, same selection model as the 3D scene: only the
 * renderer differs. That is deliberate. "Continue basic editing without 3D
 * support" is a requirement, so 2D cannot be a lesser, separately-maintained view
 * that quietly drifts out of sync.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphSnapshot } from "../../bridge/types";
import {
  edgeColor,
  glowColor,
  nodeColor,
  nodeRadius,
} from "../graph/nodeStyle";
import type { Positions } from "../graph/useGraphLayout";

interface Graph2DProps {
  graph: GraphSnapshot;
  positions: Positions;
  selectedIds: string[];
  onSelect: (id: string, modifiers: { ctrl: boolean; shift: boolean }) => void;
  onOpen: (id: string) => void;
  onLasso?: (ids: string[], add: boolean) => void;
}

const PADDING = 40;

export function Graph2D({
  graph,
  positions,
  selectedIds,
  onSelect,
  onOpen,
  onLasso,
}: Graph2DProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  // Bumped whenever the canvas resizes, to force a repaint at the new size —
  // otherwise the backing store keeps the old dimensions while the hit test uses
  // the new ones, and clicks land on the wrong node.
  const [resizeTick, setResizeTick] = useState(0);

  // Lasso: a drag with Shift held draws a rectangle; every node inside it is
  // selected on release. The rectangle is an overlay div (state), so it does not
  // fight the canvas's own repaint.
  const [lasso, setLasso] = useState<{
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  } | null>(null);
  const draggingRef = useRef(false);

  // Map layout space to canvas space once per render; reused by both the painter
  // and the hit test so a click always lands on what the user sees.
  const transform = useMemo(() => {
    const entries = Object.values(positions);
    if (entries.length === 0) return null;
    const xs = entries.map((p) => p[0]);
    const ys = entries.map((p) => p[1]);
    return {
      minX: Math.min(...xs),
      maxX: Math.max(...xs),
      minY: Math.min(...ys),
      maxY: Math.max(...ys),
    };
  }, [positions]);

  const project = useMemo(() => {
    return (
      point: [number, number, number],
      width: number,
      height: number,
    ): [number, number] => {
      if (!transform) return [width / 2, height / 2];
      const spanX = Math.max(transform.maxX - transform.minX, 1);
      const spanY = Math.max(transform.maxY - transform.minY, 1);
      const scale = Math.min(
        (width - PADDING * 2) / spanX,
        (height - PADDING * 2) / spanY,
      );
      const x = (point[0] - transform.minX) * scale + PADDING;
      const y = (point[1] - transform.minY) * scale + PADDING;
      return [x, y];
    };
  }, [transform]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const { clientWidth, clientHeight } = canvas;
    canvas.width = clientWidth * ratio;
    canvas.height = clientHeight * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, clientWidth, clientHeight);

    for (const edge of graph.edges) {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) continue;
      const lit = selected.has(edge.source) && selected.has(edge.target);
      const [x1, y1] = project(from, clientWidth, clientHeight);
      const [x2, y2] = project(to, clientWidth, clientHeight);
      context.strokeStyle = edgeColor(lit, edge.origin);
      context.lineWidth = lit ? 1.8 : 0.7;
      context.beginPath();
      context.moveTo(x1, y1);
      context.lineTo(x2, y2);
      context.stroke();
    }

    // Canvas shadows are the single most expensive 2D operation, and the 2D
    // path is exactly where low-GPU machines land. Past this budget, only the
    // selection keeps its glow — the state signal survives, the decoration pays.
    const glowBudget = graph.nodes.length <= 1500;

    for (const node of graph.nodes) {
      const point = positions[node.id];
      if (!point) continue;
      const isSelected = selected.has(node.id);
      const [x, y] = project(point, clientWidth, clientHeight);
      const radius = nodeRadius(node) * (isSelected ? 1.4 : 1) * 1.8;

      // The same glow language as the 3D galaxy: a halo in the node's own hue,
      // shifting to ignition-gold when selected.
      if (glowBudget || isSelected) {
        context.shadowColor = glowColor(node, isSelected);
        context.shadowBlur = isSelected ? 22 : 10;
      }
      context.fillStyle = nodeColor(node, isSelected);
      context.beginPath();
      context.arc(x, y, radius, 0, Math.PI * 2);
      context.fill();
      context.shadowBlur = 0;

      if (isSelected) {
        context.strokeStyle = "#ffffff";
        context.lineWidth = 1.5;
        context.stroke();
      }

      if (node.degree > 2 || isSelected) {
        context.fillStyle = isSelected ? "#e8edf7" : "#93a1bd";
        context.font = '10px "JetBrains Mono", monospace';
        context.fillText(node.label.slice(0, 28), x + radius + 4, y + 3);
      }
    }
  }, [graph, positions, selected, project, resizeTick]);

  // Repaint on resize so the backing store and the hit test agree on size.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setResizeTick((t) => t + 1));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  const hitTest = (
    event: React.MouseEvent<HTMLCanvasElement>,
  ): string | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;

    let closest: { id: string; distance: number } | null = null;
    for (const node of graph.nodes) {
      const point = positions[node.id];
      if (!point) continue;
      const [x, y] = project(point, canvas.clientWidth, canvas.clientHeight);
      const distance = Math.hypot(px - x, py - y);
      const radius = nodeRadius(node) * 1.8 + 4;
      if (distance <= radius && (!closest || distance < closest.distance)) {
        closest = { id: node.id, distance };
      }
    }
    return closest?.id ?? null;
  };

  const localPoint = (event: React.MouseEvent): [number, number] => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  };

  const finishLasso = (add: boolean): void => {
    const canvas = canvasRef.current;
    const box = lasso;
    draggingRef.current = false;
    setLasso(null);
    if (!canvas || !box || !onLasso) return;

    const x = Math.min(box.x0, box.x1);
    const y = Math.min(box.y0, box.y1);
    const w = Math.abs(box.x1 - box.x0);
    const h = Math.abs(box.y1 - box.y0);
    if (w < 4 && h < 4) return; // a click, not a lasso

    const inside: string[] = [];
    for (const node of graph.nodes) {
      const point = positions[node.id];
      if (!point || node.locked) continue;
      const [px, py] = project(point, canvas.clientWidth, canvas.clientHeight);
      if (px >= x && px <= x + w && py >= y && py <= y + h)
        inside.push(node.id);
    }
    onLasso(inside, add);
  };

  return (
    <div className="graph-2d-host">
      <canvas
        ref={canvasRef}
        className="graph-2d"
        aria-hidden="true"
        onMouseDown={(event) => {
          if (event.shiftKey && onLasso) {
            const [x, y] = localPoint(event);
            setLasso({ x0: x, y0: y, x1: x, y1: y });
            draggingRef.current = true;
          }
        }}
        onMouseMove={(event) => {
          if (draggingRef.current) {
            const [x, y] = localPoint(event);
            setLasso((box) => (box ? { ...box, x1: x, y1: y } : box));
          }
        }}
        onMouseUp={(event) => {
          if (draggingRef.current) finishLasso(event.ctrlKey || event.metaKey);
        }}
        onMouseLeave={() => {
          if (draggingRef.current) finishLasso(false);
        }}
        onClick={(event) => {
          if (event.shiftKey) return; // shift is the lasso modifier here
          const id = hitTest(event);
          if (id)
            onSelect(id, {
              ctrl: event.ctrlKey || event.metaKey,
              shift: false,
            });
        }}
        onDoubleClick={(event) => {
          const id = hitTest(event);
          if (id) onOpen(id);
        }}
      />
      {lasso && (
        <div
          className="graph-lasso"
          style={{
            left: Math.min(lasso.x0, lasso.x1),
            top: Math.min(lasso.y0, lasso.y1),
            width: Math.abs(lasso.x1 - lasso.x0),
            height: Math.abs(lasso.y1 - lasso.y0),
          }}
        />
      )}
    </div>
  );
}
