import { describe, expect, it } from "vitest";
import {
  appendEdgeCurveSegments,
  EDGE_CURVE_SEGMENTS,
  edgeControlPoint,
  edgeSalt,
  quadraticBezier,
} from "../features/graph-3d/edgeCurves";

describe("edgeCurves", () => {
  it("hashes an undirected edge pair stably", () => {
    expect(edgeSalt("a", "b")).toBe(edgeSalt("b", "a"));
    expect(edgeSalt("a", "b")).not.toBe(edgeSalt("a", "c"));
  });

  it("keeps quadratic endpoints fixed", () => {
    const [cx, cy, cz] = edgeControlPoint(0, 0, 0, 10, 0, 0, 0.5);
    expect(quadraticBezier(0, 0, 0, cx, cy, cz, 10, 0, 0, 0)).toEqual([
      0, 0, 0,
    ]);
    expect(quadraticBezier(0, 0, 0, cx, cy, cz, 10, 0, 0, 1)).toEqual([
      10, 0, 0,
    ]);
  });

  it("tessellates a curve into the expected segment count", () => {
    const out: number[] = [];
    appendEdgeCurveSegments(out, 0, 0, 0, 10, 0, 0, 0.4);
    expect(out.length).toBe(EDGE_CURVE_SEGMENTS * 6);
    expect(out[0]).toBeCloseTo(0);
    expect(out[out.length - 3]).toBeCloseTo(10);
  });

  it("bulges the control point away from a straight chord", () => {
    const [cx, cy, cz] = edgeControlPoint(0, 0, 10, 0, 0, 20, 0.5);
    // Midpoint is (0,0,15); radial bulge along +Z should move control beyond 15.
    expect(cz).toBeGreaterThan(15);
    expect(Math.hypot(cx, cy)).toBeLessThan(5);
  });
});
