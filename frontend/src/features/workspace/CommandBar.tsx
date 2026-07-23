/** The top bar: modes, graph dimension, lens, capture, and accessibility. */

import { useState } from "react";
import { CaptureDialog } from "../capture/CaptureDialog";
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
    settings,
    applySettings,
    workspace,
    activeLensId,
  } = useStore();

  const reduced = settings?.motion === "reduced";
  const [capturing, setCapturing] = useState(false);

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

      <nav className="commandbar__modes" aria-label="Application mode">
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
        <button
          type="button"
          className="button button--primary commandbar__capture"
          title="Capture text or a page into the Inbox"
          onClick={() => setCapturing(true)}
        >
          ⇣ Capture
        </button>

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

        <span className="commandbar__lens mono" title="Active Knowledge Lens">
          lens: {activeLensId.replace("lens_", "")}
        </span>

        <button
          type="button"
          className="button button--ghost"
          aria-pressed={reduced}
          title="Suppress decorative animation"
          onClick={() =>
            void applySettings({ motion: reduced ? "full" : "reduced" })
          }
        >
          {reduced ? "Motion: reduced" : "Motion: full"}
        </button>
      </div>

      {capturing && <CaptureDialog onClose={() => setCapturing(false)} />}
    </header>
  );
}
