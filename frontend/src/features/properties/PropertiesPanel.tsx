/**
 * The visual property editor.
 *
 * Properties live in the note's YAML frontmatter, so editing one here rewrites
 * the file — and only the frontmatter block, never the body.
 *
 * A note that violates its schema is *reported*, not corrected: the file is the
 * source of truth, and silently rewriting someone's data to satisfy a schema is
 * how a tool loses trust.
 */

import { useEffect, useState } from "react";
import type { PropertyDefinition } from "../../bridge/types";
import { useStore } from "../../state/store";

export function PropertiesPanel(): JSX.Element {
  const { openNote, schemas, schemaId, issues, saveProperties } = useStore();
  const [values, setValues] = useState<Record<string, unknown>>({});

  // Reset the fields when a *different* note is opened, or when this one is saved
  // (its `updated_at` moves). Depending on `properties` itself would re-seed the
  // fields on every render — including mid-edit, which would fight the user's
  // typing.
  useEffect(() => {
    setValues(openNote?.metadata.properties ?? {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openNote?.metadata.id, openNote?.metadata.updated_at]);

  if (!openNote) {
    return <p className="empty-state">Open a note to edit its properties.</p>;
  }

  const schema = schemas.find((candidate) => candidate.id === schemaId);
  const definitions: PropertyDefinition[] = schema?.properties ?? [];
  const extraKeys = Object.keys(values).filter(
    (key) => !definitions.some((definition) => definition.key === key),
  );

  const commit = (next: Record<string, unknown>): void => {
    setValues(next);
    void saveProperties(openNote.metadata.id, next);
  };

  const issueFor = (key: string): string | undefined =>
    issues.find((issue) => issue.key === key)?.problem;

  return (
    <section className="properties" aria-label="Properties">
      <div className="properties__header">
        <h2 className="sidebar__heading">Properties</h2>
        <select
          className="select properties__schema"
          aria-label="Schema"
          value={schemaId ?? ""}
          onChange={(event) => {
            const next = event.target.value;
            commit(next ? { ...values, type: next } : omit(values, "type"));
          }}
        >
          <option value="">No schema</option>
          {schemas.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.icon} {candidate.name}
            </option>
          ))}
        </select>
      </div>

      {definitions.map((definition) => (
        <PropertyField
          key={definition.key}
          definition={definition}
          value={values[definition.key]}
          issue={issueFor(definition.key)}
          onChange={(value) => commit({ ...values, [definition.key]: value })}
        />
      ))}

      {extraKeys.length > 0 && (
        <>
          <h3 className="sidebar__heading">Other</h3>
          {extraKeys.map((key) => (
            <label key={key} className="properties__field">
              <span className="label">{key}</span>
              <input
                className="input"
                value={asText(values[key])}
                onChange={(event) =>
                  setValues({ ...values, [key]: event.target.value })
                }
                onBlur={() => commit(values)}
              />
            </label>
          ))}
        </>
      )}

      {issues.length > 0 && (
        <p className="properties__issues" role="status">
          <span className="tag tag--warning">
            {issues.length} value(s) do not match the {schema?.name ?? "schema"}{" "}
            schema
          </span>
          <span className="properties__note">
            Reported, not corrected — your file is the source of truth.
          </span>
        </p>
      )}
    </section>
  );
}

/**
 * Render any frontmatter value as text.
 *
 * Frontmatter is arbitrary YAML written by a human or an importer, so a value can
 * be a nested map. `String({})` would put "[object Object]" in the field — and
 * then write that back on the next save, destroying the user's data.
 */
function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(asText).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return "";
}

function omit(
  values: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const { [key]: _dropped, ...rest } = values;
  return rest;
}

interface FieldProps {
  definition: PropertyDefinition;
  value: unknown;
  issue: string | undefined;
  onChange: (value: unknown) => void;
}

function PropertyField({
  definition,
  value,
  issue,
  onChange,
}: FieldProps): JSX.Element {
  const label = definition.label || definition.key.replace(/_/g, " ");

  const control = (): JSX.Element => {
    switch (definition.type) {
      case "boolean":
        return (
          <input
            type="checkbox"
            checked={Boolean(value)}
            aria-label={label}
            onChange={(event) => onChange(event.target.checked)}
          />
        );

      case "number":
      case "rating":
      case "progress":
      case "duration":
        return (
          <input
            className="input"
            type="number"
            value={value === undefined || value === null ? "" : Number(value)}
            min={definition.minimum ?? undefined}
            max={definition.maximum ?? undefined}
            aria-label={label}
            onChange={(event) =>
              onChange(
                event.target.value === "" ? null : Number(event.target.value),
              )
            }
          />
        );

      case "date":
        return (
          <input
            className="input"
            type="date"
            value={asText(value).slice(0, 10)}
            aria-label={label}
            onChange={(event) => onChange(event.target.value)}
          />
        );

      case "datetime":
        return (
          <input
            className="input"
            type="datetime-local"
            value={asText(value).slice(0, 16)}
            aria-label={label}
            onChange={(event) => onChange(event.target.value)}
          />
        );

      case "select":
      case "status":
        return (
          <select
            className="select"
            value={asText(value)}
            aria-label={label}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="">—</option>
            {definition.options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );

      case "tags":
      case "multi-select":
        return (
          <input
            className="input"
            value={
              Array.isArray(value)
                ? (value as string[]).join(", ")
                : asText(value)
            }
            placeholder="comma, separated"
            aria-label={label}
            onChange={(event) =>
              onChange(
                event.target.value
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean),
              )
            }
          />
        );

      default:
        return (
          <input
            className="input"
            type={
              definition.type === "email"
                ? "email"
                : definition.type === "url"
                  ? "url"
                  : "text"
            }
            value={asText(value)}
            aria-label={label}
            onChange={(event) => onChange(event.target.value)}
          />
        );
    }
  };

  return (
    <label
      className={`properties__field ${issue ? "properties__field--invalid" : ""}`}
    >
      <span className="label">
        {label}
        {definition.required ? <span aria-hidden="true"> *</span> : null}
      </span>
      {control()}
      {issue ? (
        <span className="properties__issue" role="alert">
          {issue}
        </span>
      ) : null}
    </label>
  );
}
