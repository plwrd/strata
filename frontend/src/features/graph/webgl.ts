/**
 * WebGL capability detection.
 *
 * Requirement: Strata must remain usable without 3D. That is not satisfied by
 * *intending* to fall back — Three.js throws when it cannot create a context, and
 * an uncaught throw inside the React tree blanks the whole application. So the
 * capability is probed before the canvas is ever mounted, and `GraphErrorBoundary`
 * catches the case where a context is created and then lost.
 *
 * Cases this covers in the wild: remote desktop sessions, VMs without a GPU,
 * headless CI, blocklisted drivers, and a user who turned WebGL off.
 */

let cached: boolean | null = null;

export function isWebGLAvailable(): boolean {
  if (cached !== null) return cached;
  if (typeof document === "undefined") {
    cached = false;
    return cached;
  }

  try {
    const canvas = document.createElement("canvas");
    const context =
      canvas.getContext("webgl2") ??
      canvas.getContext("webgl") ??
      canvas.getContext("experimental-webgl");
    cached = context !== null;
  } catch {
    cached = false;
  }
  return cached;
}

/** Test seam: the probe is memoised, so tests must be able to clear it. */
export function resetWebGLProbe(): void {
  cached = null;
}
