/**
 * Workspace display settings: appearance theme and related chrome prefs.
 *
 * Everything here is already validated by the Python settings service; this
 * dialog only exposes the values. Appearance changes re-point CSS tokens on
 * <html>, so the shell and the graph canvas stay in sync.
 */

import { useEffect } from "react";
import type { AppSettings } from "../../bridge/types";
import { useStore } from "../../state/store";

type Appearance = AppSettings["appearance"];
type Motion = AppSettings["motion"];
type GraphQuality = AppSettings["graph_quality"];

const APPEARANCES: { value: Appearance; label: string; hint: string }[] = [
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

export function SettingsDialog(props: { onClose: () => void }): JSX.Element {
  const { settings, applySettings } = useStore();

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
  const hidden = settings?.hide_for_sharing ?? false;

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
          <h2 className="dialog__title">Settings</h2>
          <button
            type="button"
            className="button button--ghost"
            onClick={props.onClose}
          >
            Close
          </button>
        </header>

        <div className="settings-dialog__body scroll-y">
          <section className="settings-dialog__section" aria-labelledby="settings-appearance">
            <h3 id="settings-appearance" className="settings-dialog__heading">
              Appearance
            </h3>
            <p className="settings-dialog__hint">
              Theme for the shell. Graph edges stay bright red when connected,
              dark gray otherwise.
            </p>
            <div
              className="segmented settings-dialog__segmented"
              role="group"
              aria-label="Appearance theme"
            >
              {APPEARANCES.map((entry) => (
                <button
                  key={entry.value}
                  type="button"
                  className={`segmented__option ${appearance === entry.value ? "segmented__option--active" : ""}`}
                  aria-pressed={appearance === entry.value}
                  title={entry.hint}
                  onClick={() => void applySettings({ appearance: entry.value })}
                >
                  {entry.label}
                </button>
              ))}
            </div>
          </section>

          <section className="settings-dialog__section" aria-labelledby="settings-motion">
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

          <section className="settings-dialog__section" aria-labelledby="settings-graph">
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
            </div>
          </section>

          <section className="settings-dialog__section" aria-labelledby="settings-privacy">
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
            </label>
            <p className="settings-dialog__hint">
              Exclude the whole Strata window from screenshots and screen shares.
              You still see it; capture tools do not.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
