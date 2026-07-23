/**
 * First-run essentials tour — short tips aligned with GUIDE §§2–3½, 9, 12.
 */

import type { AppMode } from "../../state/store";
import type { InspectorTab, TourPrepareOptions } from "./shellChrome";

export type TourPhase = "welcome" | "spotlight" | "done";

export type SpotlightStep = {
  id: string;
  title: string;
  body: string;
  /** `data-tour` attribute value on the highlighted host. */
  target: string;
  mode?: AppMode;
  prepare?: TourPrepareOptions;
  /** When set, open a note whose title matches (e.g. demo "Start Here"). */
  openNoteTitle?: string;
};

export const SPOTLIGHT_STEPS: SpotlightStep[] = [
  {
    id: "modes",
    title: "Four ways to work",
    body: "Focus to write, Explore for the graph, Views for tables and boards, Command for AI change plans. The centre stage follows the mode you pick.",
    target: "modes",
    prepare: { openNav: true },
  },
  {
    id: "capture",
    title: "Capture first, organise later",
    body: "Paste text or import a page into Inbox. Raw captures stay yours until you process them into knowledge.",
    target: "capture",
  },
  {
    id: "layers",
    title: "Layers are boundaries",
    body: "Public layers are plain Markdown on disk. Private layers are encrypted — locked ones contribute nothing to search, the graph, or AI.",
    target: "layers",
    prepare: { openNav: true, openSections: ["layers"] },
  },
  {
    id: "writing",
    title: "Your notes, your files",
    body: "Open anything from Files. Notes are ordinary Markdown — edit here or in any other editor. Try the seeded Start Here note if it is still around.",
    target: "files",
    mode: "focus",
    openNoteTitle: "Start Here",
    prepare: { openNav: true, openSections: ["files"] },
  },
  {
    id: "graph",
    title: "See how ideas connect",
    body: "Explore shows the knowledge graph. Select nodes (ctrl-click for more); pan and zoom in 2D, or switch to 3D when WebGL is available.",
    target: "graph",
    mode: "explore",
    prepare: { openNav: true },
  },
  {
    id: "ai",
    title: "AI sees only what you select",
    body: "The Context Composer lists exactly what a model would receive. Private content never leaves without an explicit confirmation.",
    target: "inspector-ai",
    mode: "explore",
    prepare: {
      openInspector: true,
      inspectorTab: "ai" satisfies InspectorTab,
    },
  },
];
