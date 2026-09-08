/** The top bar: modes and a compact overflow for secondary controls. */

import { useEffect, useId, useRef, useState } from "react";
import { HealthDialog } from "../health/HealthDialog";
import { SettingsDialog } from "../settings/SettingsDialog";
import { requestTourReplay } from "../onboarding/useOnboardingTour";
import { useStore, type AppMode } from "../../state/store";

const MODES: { value: AppMode; label: string; hint: string }[] = [
  { value: "focus", label: "Focus", hint: "Read and write" },
  { value: "explore", label: "Explore", hint: "Navigate the graph" },
  { value: "views", label: "Views", hint: "Table, kanban, calendar, timeline" },
  { value: "command", label: "Command", hint: "AI and bulk operations" },
];

export function CommandBar(): JSX.Element {
  const {
    mode,
    setMode,
    dimension,
    setDimension,
    workspace,
    activeLensId,
    settingsOpen,
    setSettingsOpen,
  } = useStore();

  const [healthOpen, setHealthOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const moreMenuId = useId();

  useEffect(() => {
    if (!moreOpen) return;
    const onPointerDown = (event: MouseEvent): void => {
      if (!moreRef.current?.contains(event.target as Node)) {
        setMoreOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [moreOpen]);

  const showDimension = mode === "explore";

  return (
    <header className="commandbar">
      <div className="commandbar__brand">
        <span className="commandbar__logo" aria-hidden="true">
          ▚
        </span>
        <span className="commandbar__name">STRATA</span>
        <span className="commandbar__workspace mono">
          {workspace?.workspace?.name ?? "no workspace"}
        </span>
      </div>

      <nav
        className="commandbar__modes"
        aria-label="Application mode"
        data-tour="modes"
      >
        {MODES.map((entry) => (
          <button
            key={entry.value}
            type="button"
            className={`mode ${mode === entry.value ? "mode--active" : ""}`}
            aria-pressed={mode === entry.value}
            title={entry.hint}
            onClick={() => setMode(entry.value)}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      <div className="commandbar__controls">
        {showDimension && (
          <div className="segmented" role="group" aria-label="Graph dimension">
            <button
              type="button"
              className={`segmented__option ${dimension === "2d" ? "segmented__option--active" : ""}`}
              aria-pressed={dimension === "2d"}
              onClick={() => setDimension("2d")}
            >
              2D
            </button>
            <button
              type="button"
              className={`segmented__option ${dimension === "3d" ? "segmented__option--active" : ""}`}
              aria-pressed={dimension === "3d"}
              onClick={() => setDimension("3d")}
            >
              3D
            </button>
          </div>
        )}

        <div className="commandbar__more" ref={moreRef}>
          <button
            type="button"
            className="button button--ghost"
            aria-expanded={moreOpen}
            aria-controls={moreMenuId}
            aria-haspopup="true"
            title="More workspace controls"
            onClick={() => setMoreOpen((open) => !open)}
          >
            More
          </button>
          {moreOpen && (
            <div
              id={moreMenuId}
              className="commandbar__more-menu"
              role="group"
              aria-label="More workspace controls"
            >
              <button
                type="button"
                className="button button--ghost"
                title="Appearance, motion, and display preferences"
                onClick={() => {
                  setMoreOpen(false);
                  setSettingsOpen(true);
                }}
              >
                Settings
              </button>
              <button
                type="button"
                className="button button--ghost"
                title="Replay the first-run tour of the shell"
                onClick={() => {
                  setMoreOpen(false);
                  requestTourReplay();
                }}
              >
                Tutorial
              </button>
              <button
                type="button"
                className="button button--ghost"
                title="What needs attention, and how to fix it"
                onClick={() => {
                  setMoreOpen(false);
                  setHealthOpen(true);
                }}
              >
                ◉ Health
              </button>
              <span
                className="commandbar__lens mono"
                title="Active Knowledge Lens"
              >
                lens: {activeLensId.replace("lens_", "")}
              </span>
            </div>
          )}
        </div>
      </div>

      {healthOpen && <HealthDialog onClose={() => setHealthOpen(false)} />}
      {settingsOpen && (
        <SettingsDialog onClose={() => setSettingsOpen(false)} />
      )}
    </header>
  );
}
