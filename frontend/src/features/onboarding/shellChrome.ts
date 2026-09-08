/**
 * Tiny registration so the tour can open drawers / switch inspector tabs
 * without lifting all shell chrome into the Zustand store.
 */

import type { AppMode } from "../../state/store";

export type InspectorTab = "ai" | "operations" | "properties" | "links";

export type ShellChromeApi = {
  setNavOpen: (open: boolean) => void;
  setInspectorOpen: (open: boolean) => void;
  setInspectorTab: (tab: InspectorTab) => void;
};

let api: ShellChromeApi | null = null;

export function registerShellChrome(next: ShellChromeApi | null): void {
  api = next;
}

export type TourPrepareOptions = {
  mode?: AppMode;
  openNav?: boolean;
  openInspector?: boolean;
  inspectorTab?: InspectorTab;
  /** Expand these navigator accordion sections if collapsed. */
  openSections?: Array<"layers" | "files" | "search" | "collab" | "graph">;
};

export function prepareShellForTour(options: TourPrepareOptions): void {
  if (options.openNav) api?.setNavOpen(true);
  if (options.openInspector) api?.setInspectorOpen(true);
  if (options.inspectorTab) api?.setInspectorTab(options.inspectorTab);

  for (const id of options.openSections ?? []) {
    const toggle = document.querySelector<HTMLButtonElement>(
      `[data-tour-section="${id}"]`,
    );
    if (toggle && toggle.getAttribute("aria-expanded") === "false") {
      toggle.click();
    }
  }
}
