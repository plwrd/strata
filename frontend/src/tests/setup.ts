import "@testing-library/jest-dom/vitest";

/** jsdom exposes `navigator.clipboard` as a getter, so tests must redefine it. */
export function stubClipboard(
  writeText: (text: string) => Promise<void>,
): void {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
}

// jsdom has no matchMedia; the reduced-motion hook asks for it on mount.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
