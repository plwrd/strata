/**
 * The kanban view: one column per group value.
 *
 * If no group-by is set, everything lands in one column with a hint — a kanban is
 * only meaningful when grouped by a status-like property.
 */

import type { ViewResult } from "../../bridge/types";

interface Props {
  result: ViewResult;
  onOpen: (noteId: string) => void;
}

export function KanbanView({ result, onOpen }: Props): JSX.Element {
  const groups = result.groups.length
    ? result.groups
    : [{ key: "", label: "All", rows: result.rows }];

  return (
    <div className="kanban">
      {!result.config.group_by && (
        <p className="views__notice">
          <span className="tag tag--warning">
            Pick a “Group” property to form columns
          </span>
        </p>
      )}
      <div className="kanban__board">
        {groups.map((group) => (
          <div key={group.key || "none"} className="kanban__column">
            <div className="kanban__column-header">
              <span>{group.label}</span>
              <span className="mono">{group.rows.length}</span>
            </div>
            <div className="kanban__cards scroll-y">
              {group.rows.map((row) => (
                <button
                  key={row.object_id}
                  type="button"
                  className="kanban__card"
                  onClick={() => onOpen(row.object_id)}
                >
                  <span className="kanban__card-title">{row.title}</span>
                  {row.snippet && (
                    <span className="kanban__card-snippet">{row.snippet}</span>
                  )}
                  <span className="kanban__card-tags">
                    {row.is_private && (
                      <span className="tag tag--private">{row.layer_name}</span>
                    )}
                    {row.tags.slice(0, 3).map((tag) => (
                      <span key={tag} className="tag">
                        #{tag}
                      </span>
                    ))}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
