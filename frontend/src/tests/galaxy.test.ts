/**
 * The galaxy geometry builders — pure functions behind the 3D effects.
 *
 * The shaders can't run in jsdom, but everything they consume is built here:
 * deterministic starfields, glow attributes (selection ignites gold), edge
 * particle buffers, and the label budget.
 */

import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode } from "../bridge/types";
import {
  buildEdgeParticles,
  buildNodeGlow,
  buildStarfield,
  mulberry32,
  pickLabelled,
} from "../features/graph-3d/galaxy";
import type { Positions } from "../features/graph/useGraphLayout";

function node(id: string, degree = 1, locked = false): GraphNode {
  return {
    id,
    layer_id: "layer_a",
    type: "note",
    label: `Note ${id}`,
    locked,
    folder_path: "",
    tags: [],
    degree,
    updated_at: "",
    word_count: 0,
    cluster: -1,
  };
}

function edge(id: string, source: string, target: string): GraphEdge {
  return {
    id,
    source,
    target,
    type: "relationship",
    relationship: "references",
    origin: "explicit",
    confidence: null,
    weight: 1,
  };
}

const POSITIONS: Positions = {
  a: [0, 0, 0],
  b: [10, 0, 0],
  c: [0, 10, 0],
};

describe("mulberry32", () => {
  it("is deterministic and uniform-ish", () => {
    const a = mulberry32(1);
    const b = mulberry32(1);
    const first = [a(), a(), a()];
    expect([b(), b(), b()]).toEqual(first);
    expect(first.every((v) => v >= 0 && v < 1)).toBe(true);
  });
});

describe("buildStarfield", () => {
  it("places every star inside the shell, deterministically", () => {
    const one = buildStarfield(200, 100, 300, 7);
    const two = buildStarfield(200, 100, 300, 7);
    expect(one.positions).toEqual(two.positions);
    expect(one.count).toBe(200);

    for (let i = 0; i < one.count; i += 1) {
      const r = Math.hypot(
        one.positions[i * 3]!,
        one.positions[i * 3 + 1]!,
        one.positions[i * 3 + 2]!,
      );
      expect(r).toBeGreaterThanOrEqual(100 - 1e-6);
      expect(r).toBeLessThanOrEqual(300 + 1e-6);
    }
  });
});

describe("buildNodeGlow", () => {
  it("marks selected nodes and skips unplaced ones", () => {
    const nodes = [node("a"), node("b"), node("ghost")];
    const glow = buildNodeGlow(nodes, POSITIONS, new Set(["b"]), 0.1);

    expect(glow.count).toBe(2); // "ghost" has no position
    expect(glow.selected[0]).toBe(0);
    expect(glow.selected[1]).toBe(1);
    // Selected halo is larger than the unselected one for the same node kind.
    expect(glow.sizes[1]!).toBeGreaterThan(glow.sizes[0]!);
  });
});

describe("buildEdgeParticles", () => {
  it("uses only fully-placed edges and respects the cap", () => {
    const edges = [edge("e1", "a", "b"), edge("e2", "a", "ghost")];
    const data = buildEdgeParticles(edges, POSITIONS, new Set(), 0.1, 4, 3);

    // one valid edge * 4 per edge = 4, capped at 3
    expect(data.count).toBe(3);
    expect(data.offsets.length).toBe(3);
    for (const offset of data.offsets) {
      expect(offset).toBeGreaterThanOrEqual(0);
      expect(offset).toBeLessThan(1);
    }
    // start/end come from the layout, scaled
    expect(data.starts[0]).toBeCloseTo(0);
    expect(data.ends[0]).toBeCloseTo(1); // 10 * 0.1
  });

  it("is deterministic for a given seed", () => {
    const edges = [edge("e1", "a", "b")];
    const one = buildEdgeParticles(edges, POSITIONS, new Set(), 0.1, 3, 10);
    const two = buildEdgeParticles(edges, POSITIONS, new Set(), 0.1, 3, 10);
    expect(one.offsets).toEqual(two.offsets);
    expect(one.speeds).toEqual(two.speeds);
  });
});

describe("pickLabelled", () => {
  it("always includes selected and hovered, then the biggest hubs", () => {
    const nodes = [node("a", 9), node("b", 5), node("c", 0)];
    const picked = pickLabelled(nodes, POSITIONS, new Set(["c"]), "b", 2);
    const ids = picked.map((n) => n.id);

    // c (selected) and b (hovered) always win, even over the cap.
    expect(ids).toContain("c");
    expect(ids).toContain("b");
  });

  it("never labels a locked node as a landmark", () => {
    const nodes = [node("a", 9, true), node("b", 5)];
    const picked = pickLabelled(nodes, POSITIONS, new Set(), null, 5);
    expect(picked.map((n) => n.id)).toEqual(["b"]);
  });

  it("does not label isolated dots", () => {
    const nodes = [node("a", 0), node("b", 1)];
    expect(pickLabelled(nodes, POSITIONS, new Set(), null, 5)).toEqual([]);
  });
});
