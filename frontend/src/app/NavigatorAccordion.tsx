/** Collapsible navigator sections so the stage keeps more room. */

import { useId, useState, type ReactNode } from "react";

export type NavigatorSectionId =
  | "layers"
  | "files"
  | "search"
  | "collab"
  | "graph";

type SectionDef = {
  id: NavigatorSectionId;
  label: string;
  defaultOpen?: boolean;
  children: ReactNode;
};

type Props = {
  sections: SectionDef[];
};

export function NavigatorAccordion({ sections }: Props): JSX.Element {
  const baseId = useId();
  const [openIds, setOpenIds] = useState<Set<NavigatorSectionId>>(
    () =>
      new Set(
        sections.filter((section) => section.defaultOpen).map((section) => section.id),
      ),
  );

  const toggle = (id: NavigatorSectionId): void => {
    setOpenIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <>
      {sections.map((section) => {
        const open = openIds.has(section.id);
        const panelId = `${baseId}-${section.id}-panel`;
        const buttonId = `${baseId}-${section.id}-button`;
        return (
          <div
            key={section.id}
            className="nav-section"
          >
            <button
              id={buttonId}
              type="button"
              className="nav-section__toggle"
              aria-expanded={open}
              aria-controls={panelId}
              onClick={() => toggle(section.id)}
            >
              <span>{section.label}</span>
              <span className="nav-section__chevron" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
            </button>
            {open && (
              <div id={panelId} className="nav-section__body">
                {section.children}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
