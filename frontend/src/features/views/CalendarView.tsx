/**
 * The calendar view: a month grid with notes on their date-field day.
 *
 * Shows the month that contains the most notes, so opening the view lands somewhere
 * populated rather than on an empty current month.
 */

import { useMemo, useState } from "react";
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

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function CalendarView({ result, onOpen }: Props): JSX.Element {
  const field = result.config.date_field || "updated";

  const byDay = useMemo(() => {
    const map = new Map<string, ViewRow[]>();
    for (const row of result.rows) {
      const day = dateFor(row, field).slice(0, 10);
      if (!day) continue;
      const list = map.get(day) ?? [];
      list.push(row);
      map.set(day, list);
    }
    return map;
  }, [result.rows, field]);

  const busiestMonth = useMemo(() => {
    const counts = new Map<string, number>();
    for (const day of byDay.keys()) {
      const month = day.slice(0, 7);
      counts.set(
        month,
        (counts.get(month) ?? 0) + (byDay.get(day)?.length ?? 0),
      );
    }
    let best = new Date().toISOString().slice(0, 7);
    let bestCount = -1;
    for (const [month, count] of counts) {
      if (count > bestCount) {
        best = month;
        bestCount = count;
      }
    }
    return best;
  }, [byDay]);

  const [month, setMonth] = useState(busiestMonth);

  const days = useMemo(() => {
    const [year, mon] = month.split("-").map(Number);
    const first = new Date(year!, (mon ?? 1) - 1, 1);
    const startOffset = (first.getDay() + 6) % 7; // Monday-first
    const daysInMonth = new Date(year!, mon!, 0).getDate();
    const cells: (string | null)[] = Array.from(
      { length: startOffset },
      () => null,
    );
    for (let d = 1; d <= daysInMonth; d += 1) {
      cells.push(`${month}-${String(d).padStart(2, "0")}`);
    }
    return cells;
  }, [month]);

  const shiftMonth = (delta: number): void => {
    const [year, mon] = month.split("-").map(Number);
    const next = new Date(year!, (mon ?? 1) - 1 + delta, 1);
    setMonth(next.toISOString().slice(0, 7));
  };

  return (
    <div className="calendar">
      <div className="calendar__header">
        <button
          type="button"
          className="button"
          onClick={() => shiftMonth(-1)}
          aria-label="Previous month"
        >
          ‹
        </button>
        <span className="calendar__month mono">{month}</span>
        <button
          type="button"
          className="button"
          onClick={() => shiftMonth(1)}
          aria-label="Next month"
        >
          ›
        </button>
      </div>

      <div className="calendar__grid" role="grid">
        {WEEKDAYS.map((day) => (
          <div key={day} className="calendar__weekday">
            {day}
          </div>
        ))}
        {days.map((day, index) => (
          <div key={day ?? `empty-${index}`} className="calendar__day">
            {day && (
              <>
                <span className="calendar__day-number mono">
                  {Number(day.slice(-2))}
                </span>
                {(byDay.get(day) ?? []).map((row) => (
                  <button
                    key={row.object_id}
                    type="button"
                    className="calendar__event"
                    title={row.title}
                    onClick={() => onOpen(row.object_id)}
                  >
                    {row.title}
                  </button>
                ))}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
