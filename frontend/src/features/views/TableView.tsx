/** The table view: rows are notes, columns are properties. */

import { useMemo } from "react";
import type { ViewResult } from "../../bridge/types";

interface Props {
  result: ViewResult;
  onOpen: (noteId: string) => void;
}

export function TableView({ result, onOpen }: Props): JSX.Element {
  const columns = useMemo(() => {
    // Show the properties actually present, capped so the table stays readable.
    const keys = new Set<string>();
    for (const row of result.rows) {
      for (const key of Object.keys(row.properties)) keys.add(key);
    }
    return [...keys].sort().slice(0, 6);
  }, [result.rows]);

  const renderRows = (rows: ViewResult["rows"]): JSX.Element[] =>
    rows.map((row) => (
      <tr key={row.object_id} onDoubleClick={() => onOpen(row.object_id)}>
        <td>
          <button
            type="button"
            className="views__link"
            onClick={() => onOpen(row.object_id)}
          >
            {row.title}
          </button>
          {row.is_private && (
            <span className="tag tag--private">{row.layer_name}</span>
          )}
        </td>
        {columns.map((column) => (
          <td key={column} className="mono">
            {row.properties[column] ?? ""}
          </td>
        ))}
        <td className="views__tags">
          {row.tags.map((tag) => (
            <span key={tag} className="tag">
              #{tag}
            </span>
          ))}
        </td>
      </tr>
    ));

  return (
    <div className="views__table-wrap">
      <table className="views__table">
        <thead>
          <tr>
            <th>Title</th>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
            <th>Tags</th>
          </tr>
        </thead>
        <tbody>
          {result.groups.length > 0
            ? result.groups.flatMap((group) => [
                <tr key={`g-${group.key}`} className="views__group-row">
                  <td colSpan={columns.length + 2}>
                    {group.label}{" "}
                    <span className="mono">({group.rows.length})</span>
                  </td>
                </tr>,
                ...renderRows(group.rows),
              ])
            : renderRows(result.rows)}
        </tbody>
      </table>
    </div>
  );
}
