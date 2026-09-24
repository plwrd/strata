/**
 * Quadratic Bézier chords for the 3D galaxy.
 *
 * Straight edges look mechanical at galaxy scale; a pronounced outward bulge
 * (plus a sideways twist hashed from the endpoints) keeps overlapping links
 * readable and the arcs feel flexible rather than rigid.
 */

/** Tessellation density — high enough that thick screen-space strokes stay smooth. */
export const EDGE_CURVE_SEGMENTS = 24;

/** Stable 0..1 salt from an undirected edge id pair. */
export function edgeSalt(sourceId: string, targetId: string): number {
  const a = sourceId < targetId ? sourceId : targetId;
  const b = sourceId < targetId ? targetId : sourceId;
  let hash = 2166136261;
  for (let i = 0; i < a.length; i += 1) {
    hash ^= a.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  hash ^= 0x5f;
  for (let i = 0; i < b.length; i += 1) {
    hash ^= b.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

/**
 * Mid control point: bulge away from the galaxy centre through the chord
 * midpoint, with a small lateral twist so parallel edges do not stack.
 */
export function edgeControlPoint(
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  salt: number,
): [number, number, number] {
  const mx = (ax + bx) * 0.5;
  const my = (ay + by) * 0.5;
  const mz = (az + bz) * 0.5;
  const dx = bx - ax;
  const dy = by - ay;
  const dz = bz - az;
  const dist = Math.hypot(dx, dy, dz) || 1;

  let rx = mx;
  let ry = my;
  let rz = mz;
  let rlen = Math.hypot(rx, ry, rz);
  if (rlen < 1e-3) {
    rx = dy;
    ry = -dx;
    rz = 0;
    rlen = Math.hypot(rx, ry, rz) || 1;
  }
  rx /= rlen;
  ry /= rlen;
  rz /= rlen;

  let sx = dy * rz - dz * ry;
  let sy = dz * rx - dx * rz;
  let sz = dx * ry - dy * rx;
  const slen = Math.hypot(sx, sy, sz);
  if (slen > 1e-5) {
    sx /= slen;
    sy /= slen;
    sz /= slen;
  } else {
    sx = 0;
    sy = 1;
    sz = 0;
  }

  const bulge = dist * (0.32 + salt * 0.14);
  const side = dist * (salt - 0.5) * 0.22;
  return [
    mx + rx * bulge + sx * side,
    my + ry * bulge + sy * side,
    mz + rz * bulge + sz * side,
  ];
}

export function quadraticBezier(
  ax: number,
  ay: number,
  az: number,
  cx: number,
  cy: number,
  cz: number,
  bx: number,
  by: number,
  bz: number,
  t: number,
): [number, number, number] {
  const u = 1 - t;
  return [
    u * u * ax + 2 * u * t * cx + t * t * bx,
    u * u * ay + 2 * u * t * cy + t * t * by,
    u * u * az + 2 * u * t * cz + t * t * bz,
  ];
}

/** Append LineSegments2 position pairs that approximate the edge curve. */
export function appendEdgeCurveSegments(
  out: number[],
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  salt: number,
  segments: number = EDGE_CURVE_SEGMENTS,
): void {
  const [cx, cy, cz] = edgeControlPoint(ax, ay, az, bx, by, bz, salt);
  let prev = quadraticBezier(ax, ay, az, cx, cy, cz, bx, by, bz, 0);
  for (let i = 1; i <= segments; i += 1) {
    const next = quadraticBezier(
      ax,
      ay,
      az,
      cx,
      cy,
      cz,
      bx,
      by,
      bz,
      i / segments,
    );
    out.push(prev[0], prev[1], prev[2], next[0], next[1], next[2]);
    prev = next;
  }
}
