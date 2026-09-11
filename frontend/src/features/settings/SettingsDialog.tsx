/**
 * Workspace display settings: templates, typography, colours, and chrome prefs.
 *
 * Templates set data-appearance; fonts / ui_scale / theme_colors layer CSS
 * variable overrides via applyTheme. Everything is validated by the Python
 * settings service.
 */

import { useEffect, useState } from "react";
import type {
  AppearanceTemplate,
  AppSettings,
  CaptureProtection,
  FontBody,
  FontDisplay,
  FontMono,
  ThemeColorKey,
} from "../../bridge/types";
import { useStore } from "../../state/store";
import { readThemeColor } from "./applyTheme";

type Motion = AppSettings["motion"];
type GraphQuality = AppSettings["graph_quality"];

/**
 * What to tell the user about screen protection.
 *
 * The toggle is a request to the OS; this describes the *answer*. Saying
 * "screenshots and screen shares do not see Strata" on a platform that granted
 * nothing is the kind of claim someone plans a call around, so each state gets
 * its own sentence and a failure is an alert, not a hint.
 */
export function describeCaptureProtection(
  state: CaptureProtection,
  requested: boolean,
): { tone: "ok" | "warning" | "muted"; message: string } {
  if (!requested) {
    return {
      tone: "muted",
      message: "Off — Strata appears in screenshots and screen shares.",
    };
  }
  switch (state) {
    case "excluded":
      return {
        tone: "ok",
        message:
          "Active — you still see Strata; screenshots and screen shares do not.",
      };
    case "blacked-out":
      return {
        tone: "ok",
        message:
          "Active, older method — Strata appears as a black rectangle in recordings rather than being omitted.",
      };
    case "unsupported":
      return {
        tone: "warning",
        message:
          "Not available on this platform — Strata is visible to screenshots and screen shares. " +
          "Windows is the only platform with a per-window capture control; on Linux there is none to ask for. " +
          "Lock your private layers before you share a screen.",
      };
    case "failed":
      return {
        tone: "warning",
        message:
          "The system refused to hide this window. Assume Strata is visible in screenshots and screen shares.",
      };
    default:
      return {
        tone: "muted",
        message: "Checking with the system…",
      };
  }
}

const TEMPLATES: {
  value: AppearanceTemplate;
  label: string;
  hint: string;
}[] = [
  {
    value: "cyberpunk-dark",
    label: "Cyberpunk Dark",
    hint: "Default neon dark shell",
  },
  {
    value: "cyberpunk-dim",
    label: "Cyberpunk Dim",
    hint: "Lower luminance for long sessions",
  },
  {
    value: "high-contrast",
    label: "High contrast",
    hint: "WCAG-friendlier surfaces, no glow",
  },
  { value: "ember", label: "Ember", hint: "Warm dark, amber accent" },
  { value: "forest", label: "Forest", hint: "Deep green-black, mint accent" },
  { value: "slate", label: "Slate", hint: "Neutral graphite, cool blue-gray" },
];

const MOTIONS: { value: Motion; label: string }[] = [
  { value: "full", label: "Full" },
  { value: "reduced", label: "Reduced" },
  { value: "system", label: "System" },
];

const QUALITIES: { value: GraphQuality; label: string }[] = [
  { value: "high", label: "High" },
  { value: "balanced", label: "Balanced" },
  { value: "low-gpu", label: "Low GPU" },
];

const FONT_BODY: { value: FontBody; label: string }[] = [
  { value: "inter", label: "Inter" },
  { value: "chakra", label: "Chakra Petch" },
  { value: "system", label: "System" },
];

const FONT_DISPLAY: { value: FontDisplay; label: string }[] = [
  { value: "chakra", label: "Chakra Petch" },
  { value: "inter", label: "Inter" },
  { value: "system", label: "System" },
];

const FONT_MONO: { value: FontMono; label: string }[] = [
  { value: "jetbrains", label: "JetBrains Mono" },
  { value: "consolas", label: "Consolas" },
  { value: "system", label: "System mono" },
];

// Kept in step with app.domain.browser.SEARCH_URLS — Python refuses an engine it
// does not know, so a stale entry here fails loudly rather than silently.
const SEARCH_ENGINES: { value: string; label: string }[] = [
  { value: "duckduckgo", label: "DuckDuckGo" },
  { value: "google", label: "Google" },
  { value: "bing", label: "Bing" },
  { value: "brave", label: "Brave" },
  { value: "kagi", label: "Kagi" },
  { value: "startpage", label: "Startpage" },
];

const UI_SCALES: { value: number; label: string }[] = [
  { value: 0.9, label: "Small" },
  { value: 1, label: "Default" },
  { value: 1.1, label: "Large" },
  { value: 1.25, label: "XL" },
];

const COLOR_GROUPS: {
  title: string;
  keys: { key: ThemeColorKey; label: string }[];
}[] = [
  {
    title: "Surfaces",
    keys: [
      { key: "surface_void", label: "Void" },
      { key: "surface_base", label: "Base" },
      { key: "surface_raised", label: "Raised" },
      { key: "surface_overlay", label: "Overlay" },
    ],
  },
  {
    title: "Text",
    keys: [
      { key: "text_primary", label: "Primary" },
      { key: "text_secondary", label: "Secondary" },
      { key: "text_tertiary", label: "Tertiary" },
    ],
  },
  {
    title: "Accents",
    keys: [
      { key: "accent_primary", label: "Primary" },
      { key: "accent_ai", label: "AI" },
      { key: "accent_collaboration", label: "Collaboration" },
      { key: "border_accent", label: "Border accent" },
    ],
  },
  {
    title: "Status",
    keys: [
      { key: "status_success", label: "Success" },
      { key: "status_warning", label: "Warning" },
      { key: "status_danger", label: "Danger" },
    ],
  },
  {
    title: "Graph",
    keys: [
      { key: "graph_background", label: "Background" },
      { key: "graph_node_default", label: "Node default" },
      { key: "graph_glow_selected", label: "Selection glow" },
      { key: "graph_edge_selected", label: "Connected edge" },
      { key: "graph_edge_default", label: "Idle edge" },
    ],
  },
];

const FALLBACK_HEX: Record<ThemeColorKey, string> = {
  surface_void: "#04060d",
  surface_base: "#080b16",
  surface_raised: "#0e1322",
  surface_overlay: "#141a2c",
  text_primary: "#e8edf7",
  text_secondary: "#93a1bd",
  text_tertiary: "#5d6a86",
  accent_primary: "#22e0f5",
  accent_ai: "#a06bff",
  accent_collaboration: "#ff5cc8",
  status_success: "#3ddc97",
  status_warning: "#ffb547",
  status_danger: "#ff4d6a",
  graph_background: "#04060d",
  graph_node_default: "#6f7fa8",
  graph_node_selected: "#ffffff",
  graph_glow_selected: "#ffe566",
  graph_edge_default: "#3a3f4a",
  graph_edge_selected: "#ff2d55",
  border_accent: "#22e0f5",
};

function nearestScale(value: number): number {
  let best = UI_SCALES[0]!.value;
  let distance = Math.abs(value - best);
  for (const entry of UI_SCALES) {
    const next = Math.abs(value - entry.value);
    if (next < distance) {
      best = entry.value;
      distance = next;
    }
  }
  return best;
}

function ColorRow(props: {
  colorKey: ThemeColorKey;
  label: string;
  override: string | undefined;
  onChange: (hex: string) => void;
  onReset: () => void;
}): JSX.Element {
  const live = readThemeColor(props.colorKey, FALLBACK_HEX[props.colorKey]);
  const value = (props.override ?? live).toLowerCase();
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const commit = (raw: string): void => {
    const hex = raw.trim().toLowerCase();
    if (!/^#[0-9a-f]{6}$/.test(hex)) {
      setDraft(value);
      return;
    }
    setDraft(hex);
    props.onChange(hex);
  };

  return (
    <div className="settings-color-row">
      <label
        className="settings-color-row__label"
        htmlFor={`theme-${props.colorKey}`}
      >
        {props.label}
      </label>
      <input
        id={`theme-${props.colorKey}`}
        type="color"
        className="settings-color-row__swatch"
        value={/^#[0-9a-f]{6}$/.test(draft) ? draft : live}
        aria-label={`${props.label} colour`}
        onChange={(event) => {
          const hex = event.target.value.toLowerCase();
          setDraft(hex);
          props.onChange(hex);
        }}
      />
      <input
        type="text"
        className="settings-color-row__hex mono"
        value={draft}
        spellCheck={false}
        aria-label={`${props.label} hex`}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={(event) => commit(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            commit((event.target as HTMLInputElement).value);
          }
        }}
      />
      <button
        type="button"
        className="button button--ghost settings-color-row__reset"
        disabled={!props.override}
        title="Clear override for this colour"
        onClick={props.onReset}
      >
        Reset
      </button>
    </div>
  );
}

export function SettingsDialog(props: { onClose: () => void }): JSX.Element {
  const {
    settings,
    captureProtection,
    applySettings,
    chooseBrowserExtension,
    chooseUserScript,
  } = useStore();

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") props.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);

  const appearance = settings?.appearance ?? "cyberpunk-dark";
  const motion = settings?.motion ?? "system";
  const quality = settings?.graph_quality ?? "balanced";
  const particles = settings?.particles_enabled ?? true;
  const bloom = settings?.bloom_enabled ?? true;
  const hidden = settings?.hide_for_sharing ?? true;
  const capture = describeCaptureProtection(captureProtection, hidden);
  const fontBody = settings?.font_body ?? "inter";
  const fontDisplay = settings?.font_display ?? "chakra";
  const fontMono = settings?.font_mono ?? "jetbrains";
  const uiScale = nearestScale(settings?.ui_scale ?? 1);
  const themeColors = settings?.theme_colors ?? {};
  const customized = Object.keys(themeColors).length > 0;

  const setColor = (key: ThemeColorKey, hex: string): void => {
    void applySettings({
      theme_colors: { ...themeColors, [key]: hex },
    });
  };

  const clearColor = (key: ThemeColorKey): void => {
    const next = { ...themeColors };
    delete next[key];
    void applySettings({ theme_colors: next });
  };

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) props.onClose();
      }}
    >
      <div
        className="dialog settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        <header className="dialog__header">
          <h2 className="dialog__title">
            Settings
            {customized && (
              <span className="settings-dialog__badge mono">Customized</span>
            )}
          </h2>
          <button
            type="button"
            className="button button--ghost"
            onClick={props.onClose}
          >
            Close
          </button>
        </header>

        <div className="settings-dialog__body scroll-y">
          <section
            className="settings-dialog__section"
            aria-labelledby="settings-templates"
          >
            <h3 id="settings-templates" className="settings-dialog__heading">
              Templates
            </h3>
            <p className="settings-dialog__hint">
              Choosing a template resets custom colours to that pack. Fonts and
              UI scale are kept.
            </p>
            <div
              className="settings-dialog__templates"
              role="group"
              aria-label="Appearance template"
            >
              {TEMPLATES.map((entry) => (
                <button
                  key={entry.value}
                  type="button"
                  className={`settings-dialog__template ${appearance === entry.value ? "settings-dialog__template--active" : ""}`}
                  aria-pressed={appearance === entry.value}
                  title={entry.hint}
                  onClick={() =>
                    void applySettings({
                      appearance: entry.value,
                      theme_colors: {},
                    })
                  }
                >
                  <span className="settings-dialog__template-name">
                    {entry.label}
                  </span>
                  <span className="settings-dialog__template-hint">
                    {entry.hint}
                  </span>
                </button>
              ))}
            </div>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-type"
          >
            <h3 id="settings-type" className="settings-dialog__heading">
              Typography
            </h3>
            <label className="settings-dialog__field">
              <span>Body</span>
              <select
                value={fontBody}
                aria-label="Body font"
                onChange={(event) =>
                  void applySettings({
                    font_body: event.target.value as FontBody,
                  })
                }
              >
                {FONT_BODY.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="settings-dialog__field">
              <span>Display</span>
              <select
                value={fontDisplay}
                aria-label="Display font"
                onChange={(event) =>
                  void applySettings({
                    font_display: event.target.value as FontDisplay,
                  })
                }
              >
                {FONT_DISPLAY.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="settings-dialog__field">
              <span>Mono</span>
              <select
                value={fontMono}
                aria-label="Monospace font"
                onChange={(event) =>
                  void applySettings({
                    font_mono: event.target.value as FontMono,
                  })
                }
              >
                {FONT_MONO.map((entry) => (
                  <option key={entry.value} value={entry.value}>
                    {entry.label}
                  </option>
                ))}
              </select>
            </label>
            <div
              className="segmented settings-dialog__segmented"
              role="group"
              aria-label="UI scale"
            >
              {UI_SCALES.map((entry) => (
                <button
                  key={entry.value}
                  type="button"
                  className={`segmented__option ${uiScale === entry.value ? "segmented__option--active" : ""}`}
                  aria-pressed={uiScale === entry.value}
                  onClick={() => void applySettings({ ui_scale: entry.value })}
                >
                  {entry.label}
                </button>
              ))}
            </div>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-colors"
          >
            <div className="settings-dialog__section-head">
              <h3 id="settings-colors" className="settings-dialog__heading">
                Colours
              </h3>
              <button
                type="button"
                className="button button--ghost"
                disabled={!customized}
                onClick={() => void applySettings({ theme_colors: {} })}
              >
                Reset all colours
              </button>
            </div>
            <p className="settings-dialog__hint">
              Overrides sit on top of the template. Connected and idle graph
              edges are editable here.
            </p>
            {COLOR_GROUPS.map((group) => (
              <div key={group.title} className="settings-dialog__color-group">
                <h4 className="settings-dialog__subheading">{group.title}</h4>
                {group.keys.map((entry) => (
                  <ColorRow
                    key={entry.key}
                    colorKey={entry.key}
                    label={entry.label}
                    override={themeColors[entry.key]}
                    onChange={(hex) => setColor(entry.key, hex)}
                    onReset={() => clearColor(entry.key)}
                  />
                ))}
              </div>
            ))}
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-motion"
          >
            <h3 id="settings-motion" className="settings-dialog__heading">
              Motion
            </h3>
            <div
              className="segmented settings-dialog__segmented"
              role="group"
              aria-label="Motion preference"
            >
              {MOTIONS.map((entry) => (
                <button
                  key={entry.value}
                  type="button"
                  className={`segmented__option ${motion === entry.value ? "segmented__option--active" : ""}`}
                  aria-pressed={motion === entry.value}
                  onClick={() => void applySettings({ motion: entry.value })}
                >
                  {entry.label}
                </button>
              ))}
            </div>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-graph"
          >
            <h3 id="settings-graph" className="settings-dialog__heading">
              Graph quality
            </h3>
            <div
              className="segmented settings-dialog__segmented"
              role="group"
              aria-label="Graph quality"
            >
              {QUALITIES.map((entry) => (
                <button
                  key={entry.value}
                  type="button"
                  className={`segmented__option ${quality === entry.value ? "segmented__option--active" : ""}`}
                  aria-pressed={quality === entry.value}
                  onClick={() =>
                    void applySettings({ graph_quality: entry.value })
                  }
                >
                  {entry.label}
                </button>
              ))}
            </div>
            <div className="settings-dialog__toggles">
              <label className="search__toggle">
                <input
                  type="checkbox"
                  checked={particles}
                  onChange={(event) =>
                    void applySettings({
                      particles_enabled: event.target.checked,
                    })
                  }
                />
                <span>Particles</span>
              </label>
              <label className="search__toggle">
                <input
                  type="checkbox"
                  checked={bloom}
                  onChange={(event) =>
                    void applySettings({ bloom_enabled: event.target.checked })
                  }
                />
                <span>Bloom</span>
              </label>
              <label className="search__toggle">
                <input
                  type="checkbox"
                  checked={settings?.battery_saver ?? false}
                  onChange={(event) =>
                    void applySettings({
                      battery_saver: event.target.checked,
                    })
                  }
                />
                <span>Battery saver</span>
              </label>
            </div>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-privacy"
          >
            <h3 id="settings-privacy" className="settings-dialog__heading">
              Screen security
            </h3>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={hidden}
                onChange={(event) =>
                  void applySettings({
                    hide_for_sharing: event.target.checked,
                  })
                }
              />
              <span>Hidden for sharing</span>
              <kbd className="settings-dialog__key">Ctrl/Cmd+Shift+H</kbd>
            </label>
            <p
              className={`settings-dialog__hint settings-dialog__hint--${capture.tone}`}
              role={capture.tone === "warning" ? "alert" : undefined}
              data-testid="capture-protection"
            >
              {capture.message}
            </p>
            <p className="settings-dialog__hint">
              On by default (Signal-style). Turn off only when you need to demo
              or record the app itself.
            </p>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-tray"
          >
            <h3 id="settings-tray" className="settings-dialog__heading">
              System tray
            </h3>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.minimize_to_tray ?? false}
                onChange={(event) =>
                  void applySettings({
                    minimize_to_tray: event.target.checked,
                  })
                }
              />
              <span>Minimize to tray</span>
            </label>
            <p className="settings-dialog__hint">
              When on, closing or minimizing hides the window to a tray icon
              instead of quitting — it leaves the taskbar, but Strata keeps
              running and the workspace stays open. Quit from the tray menu.
            </p>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.start_in_tray ?? false}
                disabled={!(settings?.minimize_to_tray ?? false)}
                onChange={(event) =>
                  void applySettings({ start_in_tray: event.target.checked })
                }
              />
              <span>Start hidden in the tray</span>
            </label>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.hide_from_taskbar ?? false}
                onChange={(event) =>
                  void applySettings({
                    hide_from_taskbar: event.target.checked,
                  })
                }
              />
              <span>No taskbar button</span>
            </label>
            <p className="settings-dialog__hint">
              Removes Strata's taskbar button entirely (Windows), even while the
              window is open — it lives in the tray instead, so the tray icon
              stays on and minimizing sends it there. Your way back is the tray.
            </p>
            <p className="settings-dialog__hint">
              All of this hides the <em>window</em>, never the process. Strata
              stays listed in Task Manager and every other process tool — that
              is by design, and any app that hid its own process would be
              malware.
            </p>
          </section>

          <section
            className="settings-dialog__section"
            aria-labelledby="settings-research"
          >
            <h3 id="settings-research" className="settings-dialog__heading">
              Browser research
            </h3>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.browser_control_enabled ?? false}
                onChange={(event) =>
                  void applySettings({
                    browser_control_enabled: event.target.checked,
                  })
                }
              />
              <span>Let Strata drive a browser</span>
              <kbd className="settings-dialog__key">Ctrl/Cmd+Shift+B</kbd>
            </label>
            <p className="settings-dialog__hint">
              Off by default. When on, Strata opens a browser beside your
              workspace and reads the page you point it at, so research reaches
              pages a plain fetch cannot. A browser Strata can read is a browser
              Strata can read everything in, so leave this off unless you are
              researching.
            </p>
            <label className="composer__field">
              <span className="label">Browser</span>
              <select
                className="select"
                value={settings?.browser_backend ?? "embedded"}
                aria-label="Research browser"
                onChange={(event) =>
                  void applySettings({
                    browser_backend: event.target
                      .value as AppSettings["browser_backend"],
                  })
                }
              >
                <option value="embedded">Pane in this window</option>
                <option value="webview2">
                  Pane in this window (Edge engine — plays video)
                </option>
                <option value="chrome">Your own Chrome</option>
              </select>
            </label>
            <p className="settings-dialog__hint">
              Both panes keep their own sign-ins and need nothing installed. The
              built-in one cannot play H.264 or AAC, so most video stays blank,
              and it cannot load extensions — Qt ships Chromium without the
              extensions subsystem. The Edge engine does both, and stays inside
              this window, so the screen-capture exclusion below still covers
              it. Choose your own Chrome only when a page refuses to let you
              sign in to an embedded browser — it is the one option Strata
              cannot keep out of a screen recording.
            </p>
            {settings?.browser_backend === "embedded" && (
              <div className="composer__field">
                <span className="label">User scripts</span>
                <ul className="settings-dialog__list">
                  {(settings?.browser_user_scripts ?? []).map((file) => (
                    <li key={file} className="settings-dialog__row">
                      <code title={file}>{file.split(/[\\/]/).pop()}</code>
                      <button
                        type="button"
                        className="button button--ghost"
                        aria-label={`Remove user script ${file}`}
                        onClick={() =>
                          void applySettings({
                            browser_user_scripts: (
                              settings?.browser_user_scripts ?? []
                            ).filter((kept) => kept !== file),
                          })
                        }
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="button"
                  onClick={() => void chooseUserScript()}
                >
                  Add a user script…
                </button>
                <p className="settings-dialog__hint">
                  The built-in pane cannot load extensions at all — Qt ships
                  Chromium without the extensions subsystem. A user script is
                  the closest thing it has: a <code>.js</code> file injected
                  into every page, the same shape Greasemonkey and Tampermonkey
                  scripts are written in. <code>@run-at document-start</code> is
                  honoured; without it a script runs once the page is there. It
                  runs with the page's own privileges and sees everything on it,
                  so add only scripts you have read or trust.
                </p>
              </div>
            )}
            {settings?.browser_backend === "embedded" && (
              <label className="composer__field">
                <span className="label">Blocked hosts</span>
                <textarea
                  className="input"
                  rows={4}
                  aria-label="Blocked hosts"
                  placeholder={"ads.example.com\ntracker.net"}
                  value={(settings?.browser_blocked_hosts ?? []).join("\n")}
                  onChange={(event) =>
                    void applySettings({
                      browser_blocked_hosts: event.target.value
                        .split("\n")
                        .map((line) => line.trim())
                        .filter(Boolean),
                    })
                  }
                />
                <p className="settings-dialog__hint">
                  One host per line. The pane refuses requests to these and
                  their subdomains, so an ad or tracker never loads, never runs
                  and never sets a cookie — the half of an ad blocker Qt can
                  actually do. It is not a filter list: no cosmetic rules, no
                  path patterns. Pages you navigate to yourself are never
                  blocked, only what they load.
                </p>
              </label>
            )}
            {settings?.browser_backend === "webview2" && (
              <div className="composer__field">
                <span className="label">Extensions</span>
                <ul className="settings-dialog__list">
                  {(settings?.browser_extensions ?? []).map((folder) => (
                    <li key={folder} className="settings-dialog__row">
                      <code title={folder}>{folder}</code>
                      <button
                        type="button"
                        className="button button--ghost"
                        aria-label={`Remove extension ${folder}`}
                        onClick={() =>
                          void applySettings({
                            browser_extensions: (
                              settings?.browser_extensions ?? []
                            ).filter((kept) => kept !== folder),
                          })
                        }
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="button"
                  onClick={() => void chooseBrowserExtension()}
                >
                  Add an extension folder…
                </button>
                <p className="settings-dialog__hint">
                  Unpacked extensions only — pick the folder holding
                  <code> manifest.json</code>, not a <code>.crx</code> file.
                  There is no store install here. An extension sees every page
                  the pane visits and can send what it sees anywhere, so add
                  only ones you would trust with your research; it cannot reach
                  your workspace, because the pane has no bridge to it. Changes
                  take effect next time Strata starts — the engine loads
                  extensions when its browser process is created.
                </p>
              </div>
            )}
            <label className="composer__field">
              <span className="label">Search engine</span>
              <select
                className="select"
                value={settings?.browser_search_engine ?? "duckduckgo"}
                aria-label="Research search engine"
                onChange={(event) =>
                  void applySettings({
                    browser_search_engine: event.target.value,
                  })
                }
              >
                {SEARCH_ENGINES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="composer__field">
              <span className="label">Browser path (optional)</span>
              <input
                className="input"
                value={settings?.browser_executable_path ?? ""}
                placeholder="Found automatically if left empty"
                aria-label="Browser executable path"
                onChange={(event) =>
                  void applySettings({
                    browser_executable_path: event.target.value,
                  })
                }
              />
            </label>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.browser_blur_media ?? false}
                onChange={(event) =>
                  void applySettings({
                    browser_blur_media: event.target.checked,
                  })
                }
              />
              <span>Blur media by default</span>
            </label>
            <label className="composer__field">
              <span className="label">
                Blur strength ({settings?.browser_blur_amount ?? 12}px)
              </span>
              <input
                className="input"
                type="range"
                min={1}
                max={40}
                value={settings?.browser_blur_amount ?? 12}
                aria-label="Blur strength"
                onChange={(event) =>
                  void applySettings({
                    browser_blur_amount: Number(event.target.value),
                  })
                }
              />
            </label>
            <p className="settings-dialog__hint">
              Blur hides images, video and canvas in the browser pane so a page
              is safe to have on a shared screen — text stays readable. Toggle
              it live with <kbd>Ctrl/Cmd+Shift+X</kbd>. The pane only; your own
              Chrome is not restyled.
            </p>
            <label className="search__toggle">
              <input
                type="checkbox"
                checked={settings?.browser_mobile_mode ?? false}
                onChange={(event) =>
                  void applySettings({
                    browser_mobile_mode: event.target.checked,
                  })
                }
              />
              <span>Mobile site mode</span>
            </label>
            <p className="settings-dialog__hint">
              The pane serves a mobile user-agent, so sites render their
              touch/mobile layout — you can also toggle it live in the Research
              panel. Synthetic touch events are advertised to pages from the
              next launch (a process-wide setting). The pane only.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
