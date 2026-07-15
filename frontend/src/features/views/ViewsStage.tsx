/**
 * Structured views — the database-style surface over the Markdown notes.
 *
 * The rule this whole feature respects: **the notes are the source of truth.** Every
 * view is a query run by Python over the live notes; nothing is a stored table, and
 * editing a note in the editor changes what the view shows on the next run. Filters,
 * sorting and grouping are computed in Python (tested there), so a view can never
 * quietly drop or misorder a row the user would then not know was missing.
 */

import { useEffect, useMemo, useState } from "react";
import { bridge } from "../../bridge/client";
import type { ViewConfig, ViewResult, ViewType } from "../../bridge/types";
import { useStore } from "../../state/store";
import { CalendarView } from "./CalendarView";
import { CardsView } from "./CardsView";
import { KanbanView } from "./KanbanView";
import { TableView } from "./TableView";
import { TimelineView } from "./TimelineView";
import { ViewToolbar } from "./ViewToolbar";

const VIEW_TYPES: { value: ViewType; label: string; icon: string }[] = [
  { value: "table", label: "Table", icon: "▤" },
  { value: "cards", label: "Cards", icon: "▦" },
  { value: "kanban", label: "Kanban", icon: "▥" },
  { value: "calendar", label: "Calendar", icon: "▦" },
  { value: "timeline", label: "Timeline", icon: "▬" },
];

function blankConfig(): ViewConfig {
  return {
    id: `view_${Date.now().toString(36)}`,
    name: "New view",
    type: "table",
    layer_ids: [],
    folder_scope: "",
    filters: [],
    sort: [],
    group_by: "",
    visible_properties: [],
    date_field: "updated",
  };
}

export function ViewsStage(): JSX.Element {
  const openNote = useStore((state) => state.openNoteById);
  // The view renderers take a `void` open handler; openNoteById returns a promise,
  // so wrap it rather than leaking a floating promise through a JSX attribute.
  const openNoteById = (noteId: string): void => void openNote(noteId);
  const [config, setConfig] = useState<ViewConfig>(blankConfig);
  const [result, setResult] = useState<ViewResult | null>(null);
  const [loading, setLoading] = useState(false);

  // The view re-runs whenever its config changes. Debounced lightly so typing a
  // filter value does not fire a query per keystroke.
  useEffect(() => {
    let cancelled = false;
    const handle = window.setTimeout(() => {
      setLoading(true);
      void bridge.views
        .run(config)
        .then(({ result: next }) => {
          if (!cancelled) setResult(next);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [config]);

  const body = useMemo(() => {
    if (!result) return null;
    switch (config.type) {
      case "kanban":
        return <KanbanView result={result} onOpen={openNoteById} />;
      case "cards":
      case "gallery":
        return <CardsView result={result} onOpen={openNoteById} />;
      case "calendar":
        return <CalendarView result={result} onOpen={openNoteById} />;
      case "timeline":
        return <TimelineView result={result} onOpen={openNoteById} />;
      default:
        return <TableView result={result} onOpen={openNoteById} />;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, config.type]);

  return (
    <section className="views" aria-label="Structured views">
      <div className="views__tabs" role="tablist" aria-label="View type">
        {VIEW_TYPES.map((view) => (
          <button
            key={view.value}
            type="button"
            role="tab"
            aria-selected={config.type === view.value}
            className={`views__tab ${config.type === view.value ? "views__tab--active" : ""}`}
            onClick={() => setConfig({ ...config, type: view.value })}
          >
            <span aria-hidden="true">{view.icon}</span> {view.label}
          </button>
        ))}
      </div>

      <ViewToolbar
        config={config}
        available={result?.available_properties ?? []}
        onChange={setConfig}
      />

      <div className="views__body scroll-y">
        {loading && !result && <p className="empty-state">Running the view…</p>}
        {result && result.total === 0 && (
          <p className="empty-state">No notes match this view.</p>
        )}
        {body}
        {result && result.locked_layers_excluded > 0 && (
          <p className="views__notice">
            <span className="tag tag--locked">
              {result.locked_layers_excluded} locked layer(s) are not shown
            </span>
          </p>
        )}
      </div>
    </section>
  );
}
