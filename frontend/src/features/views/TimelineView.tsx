/**
 * The timeline view: notes on a vertical time axis, by their chosen date field.
 *
 * The date comes from `config.date_field` (created, updated, or any date property);
 * a note with no value for that field is grouped under "Undated" rather than being
 * silently dropped.
 */

import { useMemo } from "react";
import type { ViewResult, ViewRow } from "../../bridge/types";

interface Props {
  result: ViewResult;
  onOpen: (noteId: string) => void;
}

function dateFor(row: ViewRow, field: string): string {
  if (field === "created") return row.created_at;
  if (field === "updated") return row.updated_at;
  return row.properties[field] ?? "";
}

export function TimelineView({ result, onOpen }: Props): JSX.Element {
  const field = result.config.date_field || "updated";

  const buckets = useMemo(() => {
    const byMonth = new Map<string, ViewRow[]>();
    for (const row of result.rows) {
      const raw = dateFor(row, field);
      const month = raw ? raw.slice(0, 7) : "Undated";
      const list = byMonth.get(month) ?? [];
      list.push(row);
      byMonth.set(month, list);
    }
    return [...byMonth.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [result.rows, field]);

  return (
    <div className="timeline">
      {buckets.map(([month, rows]) => (
        <div key={month} className="timeline__bucket">
          <div className="timeline__marker">
            <span className="timeline__dot" aria-hidden="true" />
            <span className="timeline__month mono">{month}</span>
          </div>
          <ul className="timeline__items">
            {rows.map((row) => (
              <li key={row.object_id}>
                <button
                  type="button"
                  className="timeline__item"
                  onClick={() => onOpen(row.object_id)}
                >
                  <span className="timeline__item-title">{row.title}</span>
                  <span className="timeline__item-date mono">
                    {dateFor(row, field).slice(0, 10)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
