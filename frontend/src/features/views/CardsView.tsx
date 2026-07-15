/** The cards / gallery view: a grid of note cards. */

import type { ViewResult } from "../../bridge/types";

interface Props {
  result: ViewResult;
  onOpen: (noteId: string) => void;
}

export function CardsView({ result, onOpen }: Props): JSX.Element {
  return (
    <div className="cards">
      {result.rows.map((row) => (
        <button
          key={row.object_id}
          type="button"
          className="cards__card"
          onClick={() => onOpen(row.object_id)}
        >
          <span className="cards__title">{row.title}</span>
          <span className="cards__snippet">{row.snippet}</span>
          <span className="cards__meta">
            {row.is_private && (
              <span className="tag tag--private">{row.layer_name}</span>
            )}
            {row.folder_path && (
              <span className="mono cards__folder">{row.folder_path}</span>
            )}
          </span>
          {row.tags.length > 0 && (
            <span className="cards__tags">
              {row.tags.map((tag) => (
                <span key={tag} className="tag">
                  #{tag}
                </span>
              ))}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
