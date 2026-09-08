/**
 * Apply appearance template + typography/colour overrides to <html>.
 *
 * Templates live in tokens.css via data-appearance. Overrides are inline
 * CSS variables so the canvas (cssToken) and the shell stay in sync after
 * resetTokenCache().
 */

import type {
  AppSettings,
  FontBody,
  FontDisplay,
  FontMono,
  ThemeColorKey,
} from "../../bridge/types";
import { resetTokenCache } from "../graph/nodeStyle";

export const THEME_COLOR_KEYS: readonly ThemeColorKey[] = [
  "surface_void",
  "surface_base",
  "surface_raised",
  "surface_overlay",
  "text_primary",
  "text_secondary",
  "text_tertiary",
  "accent_primary",
  "accent_ai",
  "accent_collaboration",
  "status_success",
  "status_warning",
  "status_danger",
  "graph_background",
  "graph_node_default",
  "graph_node_selected",
  "graph_glow_selected",
  "graph_edge_default",
  "graph_edge_selected",
  "border_accent",
] as const;

const FONT_BODY: Record<FontBody, string> = {
  inter: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
  system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  chakra: '"Chakra Petch", "Rajdhani", "Segoe UI", system-ui, sans-serif',
};

const FONT_DISPLAY: Record<FontDisplay, string> = {
  chakra: '"Chakra Petch", "Rajdhani", "Segoe UI", system-ui, sans-serif',
  inter: '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
  system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
};

const FONT_MONO: Record<FontMono, string> = {
  jetbrains:
    '"JetBrains Mono", "Cascadia Mono", "Consolas", ui-monospace, monospace',
  consolas: '"Consolas", "Cascadia Mono", ui-monospace, monospace',
  system: 'ui-monospace, "Cascadia Mono", "Consolas", monospace',
};

/** CSS custom properties we may have set inline and must clear on re-apply. */
const CLEARABLE_VARS = [
  "--font-body",
  "--font-display",
  "--font-mono",
  "--ui-scale",
  ...THEME_COLOR_KEYS.map((key) => `--${key.replaceAll("_", "-")}`),
  "--graph-edge-default-solid",
  "--graph-edge-selected-solid",
  "--graph-edge-ai-solid",
];

function toKebab(key: string): string {
  return key.replaceAll("_", "-");
}

export function applyTheme(settings: AppSettings): void {
  const root = document.documentElement;
  root.dataset["appearance"] = settings.appearance;
  root.dataset["motion"] =
    settings.motion === "system" ? "system" : settings.motion;
  root.dataset["graphQuality"] = settings.graph_quality;

  for (const name of CLEARABLE_VARS) {
    root.style.removeProperty(name);
  }

  const scale = settings.ui_scale ?? 1;
  root.style.setProperty("--ui-scale", String(scale));

  const body = settings.font_body ?? "inter";
  const display = settings.font_display ?? "chakra";
  const mono = settings.font_mono ?? "jetbrains";
  root.style.setProperty("--font-body", FONT_BODY[body]);
  root.style.setProperty("--font-display", FONT_DISPLAY[display]);
  root.style.setProperty("--font-mono", FONT_MONO[mono]);

  const colors = settings.theme_colors ?? {};
  for (const key of THEME_COLOR_KEYS) {
    const hex = colors[key];
    if (!hex) continue;
    const cssName = `--${toKebab(key)}`;
    root.style.setProperty(cssName, hex);
    if (key === "graph_edge_default") {
      root.style.setProperty("--graph-edge-default-solid", hex);
      root.style.setProperty("--graph-edge-ai-solid", hex);
    }
    if (key === "graph_edge_selected") {
      root.style.setProperty("--graph-edge-selected-solid", hex);
    }
  }

  resetTokenCache();
}

/** Read the live computed colour for a token (template + overrides). */
export function readThemeColor(key: ThemeColorKey, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${toKebab(key)}`)
    .trim();
  if (!raw) return fallback;
  // Native <input type="color"> needs #rrggbb; expand rgb()/named if needed.
  if (/^#[0-9A-Fa-f]{6}$/i.test(raw)) return raw.toLowerCase();
  if (/^#[0-9A-Fa-f]{3}$/i.test(raw)) {
    const a = raw.charAt(1);
    const b = raw.charAt(2);
    const c = raw.charAt(3);
    return `#${a}${a}${b}${b}${c}${c}`.toLowerCase();
  }
  return fallback;
}
