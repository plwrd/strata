import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import type { HumazieConfig } from "../config.js";
import type { ProductEdge, ProductMap, ProductNode } from "../types.js";
import { makeId, nowIso, resolveFromRoot } from "../util/paths.js";

const MODE_LABELS = ["Focus", "Explore", "Views", "Command"] as const;
const MODE_IDS = ["focus", "explore", "views", "command"] as const;

function walkTsx(dir: string, out: string[] = []): string[] {
  let entries: string[] = [];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (entry === "node_modules" || entry === "dist") continue;
      walkTsx(full, out);
    } else if (/\.(tsx|ts)$/.test(entry) && !entry.endsWith(".d.ts")) {
      out.push(full);
    }
  }
  return out;
}

function extractDialogs(content: string, file: string, root: string): ProductNode[] {
  const nodes: ProductNode[] = [];
  const aria = [...content.matchAll(/aria-label=["']([^"']+)["']/g)].map((m) => m[1]);
  const isDialog =
    /role=["']dialog["']/.test(content) ||
    /Dialog/.test(file) ||
    /aria-modal=["']true["']/.test(content);
  if (!isDialog) return nodes;
  const label = aria[0] ?? file.split(/[\\/]/).pop()?.replace(/\.tsx?$/, "") ?? "dialog";
  nodes.push({
    id: makeId("dialog", file + label),
    kind: "dialog",
    label,
    sourceFiles: [relative(root, file).replace(/\\/g, "/")],
    accessibleName: label,
  });
  return nodes;
}

function extractForms(content: string, file: string, root: string): ProductNode[] {
  if (!/<form\b|type=["']submit["']|getByRole\(["']textbox/.test(content) && !/Dialog/.test(file)) {
    return [];
  }
  if (!/onSubmit|submit\s*=|type=["']submit["']/.test(content)) return [];
  const name = file.split(/[\\/]/).pop()?.replace(/\.tsx?$/, "") ?? "form";
  return [
    {
      id: makeId("form", file),
      kind: "form",
      label: name,
      sourceFiles: [relative(root, file).replace(/\\/g, "/")],
    },
  ];
}

/**
 * Build a product map from Strata's React sources.
 * Modes are primary pages; dialogs/forms/panels are nodes; mode switches are edges.
 */
export function discoverProduct(config: HumazieConfig): ProductMap {
  const root = resolveFromRoot();
  const nodes: ProductNode[] = [];
  const edges: ProductEdge[] = [];
  const sourceSummary: Record<string, number> = {};

  for (const mode of MODE_IDS) {
    const label = MODE_LABELS[MODE_IDS.indexOf(mode)];
    nodes.push({
      id: `mode_${mode}`,
      kind: "page",
      label,
      route: `#${mode}`,
      sourceFiles: ["frontend/src/features/workspace/CommandBar.tsx", "frontend/src/app/App.tsx"],
      accessibleName: label,
    });
  }

  nodes.push({
    id: "shell",
    kind: "section",
    label: "Application shell",
    route: "/humazie.html",
    sourceFiles: ["frontend/src/app/App.tsx"],
  });

  const files: string[] = [];
  for (const featureRoot of config.discovery.featureRoots) {
    files.push(...walkTsx(resolveFromRoot(featureRoot)));
  }

  const dialogs: string[] = [];
  const forms: string[] = [];

  for (const file of files) {
    const content = readFileSync(file, "utf8");
    const rel = relative(root, file).replace(/\\/g, "/");
    sourceSummary[rel] = content.split("\n").length;

    for (const dialog of extractDialogs(content, file, root)) {
      nodes.push(dialog);
      dialogs.push(dialog.label);
      edges.push({
        id: makeId("edge", `shell->${dialog.id}`),
        from: "shell",
        to: dialog.id,
        action: "open-dialog",
        label: `Open ${dialog.label}`,
      });
    }
    for (const form of extractForms(content, file, root)) {
      nodes.push(form);
      forms.push(form.label);
      edges.push({
        id: makeId("edge", `shell->${form.id}`),
        from: "shell",
        to: form.id,
        action: "submit-form",
        label: `Submit ${form.label}`,
      });
    }
  }

  for (const mode of MODE_IDS) {
    edges.push({
      id: makeId("edge", `shell->mode_${mode}`),
      from: "shell",
      to: `mode_${mode}`,
      action: "navigate-mode",
      label: `Switch to ${mode}`,
    });
  }

  // Runtime-oriented sections known from the shell
  const sections = [
    { id: "navigator", label: "Navigator", file: "frontend/src/app/App.tsx" },
    { id: "inspector", label: "Inspector", file: "frontend/src/app/App.tsx" },
    { id: "capture", label: "Capture", file: "frontend/src/features/capture/CaptureDialog.tsx" },
    { id: "health", label: "Health", file: "frontend/src/features/health/HealthDialog.tsx" },
    { id: "layers", label: "Layers", file: "frontend/src/features/layers/LayerPanel.tsx" },
    { id: "search", label: "Search", file: "frontend/src/features/search/SearchPanel.tsx" },
    { id: "ai", label: "AI Composer", file: "frontend/src/features/ai-composer/AIComposerPanel.tsx" },
  ];
  for (const section of sections) {
    nodes.push({
      id: `section_${section.id}`,
      kind: "section",
      label: section.label,
      sourceFiles: [section.file],
      accessibleName: section.label,
    });
  }

  return {
    generatedAt: nowIso(),
    nodes,
    edges,
    modes: [...MODE_IDS],
    dialogs: [...new Set(dialogs)],
    forms: [...new Set(forms)],
    sourceSummary,
  };
}

/** Lightweight runtime enrichment from a live page DOM. */
export function enrichMapFromDom(
  map: ProductMap,
  observed: { headings: string[]; buttons: string[]; links: string[] },
): ProductMap {
  const extra: ProductNode[] = observed.headings.slice(0, 20).map((heading) => ({
    id: makeId("heading", heading),
    kind: "section" as const,
    label: heading,
    sourceFiles: [],
  }));
  return {
    ...map,
    nodes: [...map.nodes, ...extra],
  };
}
