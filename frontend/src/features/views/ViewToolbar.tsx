/** Filters, sort, and group-by controls for a structured view. */

import type { FilterOperator, ViewConfig } from "../../bridge/types";

interface Props {
  config: ViewConfig;
  available: string[];
  onChange: (config: ViewConfig) => void;
}

const OPERATORS: { value: FilterOperator; label: string }[] = [
  { value: "equals", label: "is" },
  { value: "not_equals", label: "is not" },
  { value: "contains", label: "contains" },
  { value: "is_not_empty", label: "is set" },
  { value: "is_empty", label: "is empty" },
  { value: "greater_than", label: ">" },
  { value: "less_than", label: "<" },
  { value: "before", label: "before" },
  { value: "after", label: "after" },
];

const NO_VALUE = new Set<FilterOperator>(["is_empty", "is_not_empty"]);

export function ViewToolbar({
  config,
  available,
  onChange,
}: Props): JSX.Element {
  const addFilter = (): void =>
    onChange({
      ...config,
      filters: [
        ...config.filters,
        { field: available[0] ?? "title", operator: "contains", value: "" },
      ],
    });

  const updateFilter = (
    index: number,
    patch: Partial<ViewConfig["filters"][number]>,
  ): void => {
    const filters = config.filters.map((filter, i) =>
      i === index ? { ...filter, ...patch } : filter,
    );
    onChange({ ...config, filters });
  };

  const removeFilter = (index: number): void =>
    onChange({
      ...config,
      filters: config.filters.filter((_, i) => i !== index),
    });

  return (
    <div className="view-toolbar">
      <div className="view-toolbar__row">
        <label className="view-toolbar__control">
          <span className="label">Sort</span>
          <select
            className="select"
            value={config.sort[0]?.field ?? ""}
            onChange={(event) =>
              onChange({
                ...config,
                sort: event.target.value
                  ? [
                      {
                        field: event.target.value,
                        direction: config.sort[0]?.direction ?? "asc",
                      },
                    ]
                  : [],
              })
            }
          >
            <option value="">—</option>
            {available.map((field) => (
              <option key={field} value={field}>
                {field}
              </option>
            ))}
          </select>
        </label>

        {config.sort[0] && (
          <button
            type="button"
            className="button"
            onClick={() =>
              onChange({
                ...config,
                sort: [
                  {
                    field: config.sort[0]!.field,
                    direction:
                      config.sort[0]!.direction === "asc" ? "desc" : "asc",
                  },
                ],
              })
            }
          >
            {config.sort[0].direction === "asc" ? "↑" : "↓"}
          </button>
        )}

        {(config.type === "kanban" || config.type === "table") && (
          <label className="view-toolbar__control">
            <span className="label">Group</span>
            <select
              className="select"
              value={config.group_by}
              onChange={(event) =>
                onChange({ ...config, group_by: event.target.value })
              }
            >
              <option value="">—</option>
              {available.map((field) => (
                <option key={field} value={field}>
                  {field}
                </option>
              ))}
            </select>
          </label>
        )}

        {(config.type === "calendar" || config.type === "timeline") && (
          <label className="view-toolbar__control">
            <span className="label">Date</span>
            <select
              className="select"
              value={config.date_field}
              onChange={(event) =>
                onChange({ ...config, date_field: event.target.value })
              }
            >
              <option value="updated">updated</option>
              <option value="created">created</option>
              {available
                .filter(
                  (f) =>
                    f.includes("date") ||
                    f.includes("due") ||
                    f.includes("published"),
                )
                .map((field) => (
                  <option key={field} value={field}>
                    {field}
                  </option>
                ))}
            </select>
          </label>
        )}

        <button
          type="button"
          className="button button--ghost"
          onClick={addFilter}
        >
          + Filter
        </button>
      </div>

      {config.filters.map((filter, index) => (
        <div key={index} className="view-toolbar__filter">
          <select
            className="select"
            aria-label="Filter field"
            value={filter.field}
            onChange={(event) =>
              updateFilter(index, { field: event.target.value })
            }
          >
            {available.map((field) => (
              <option key={field} value={field}>
                {field}
              </option>
            ))}
          </select>

          <select
            className="select"
            aria-label="Filter operator"
            value={filter.operator}
            onChange={(event) =>
              updateFilter(index, {
                operator: event.target.value as FilterOperator,
              })
            }
          >
            {OPERATORS.map((op) => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>

          {!NO_VALUE.has(filter.operator) && (
            <input
              className="input"
              aria-label="Filter value"
              value={filter.value}
              onChange={(event) =>
                updateFilter(index, { value: event.target.value })
              }
            />
          )}

          <button
            type="button"
            className="tree__action tree__action--danger"
            aria-label="Remove filter"
            onClick={() => removeFilter(index)}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
