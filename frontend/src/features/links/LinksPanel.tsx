/**
 * Backlinks, unlinked mentions, and outgoing links for the open note.
 *
 * An unlinked mention is an offer, not a fact: it names a note that talks *about*
 * this one without linking to it, and the user decides whether that is a link.
 */

import { useStore } from "../../state/store";
import { ConnectionSuggestions } from "./ConnectionSuggestions";

export function LinksPanel(): JSX.Element {
  const { links, openNote, openNoteById, linkHealth } = useStore();

  if (!openNote) {
    return <p className="empty-state">Open a note to see what links to it.</p>;
  }

  const broken = linkHealth.broken.filter(
    (entry) => entry.source_id === openNote.metadata.id,
  );

  return (
    <section className="links" aria-label="Links">
      <h2 className="sidebar__heading">Backlinks ({links.backlinks.length})</h2>
      {links.backlinks.length === 0 ? (
        <p className="empty-state">Nothing links here yet.</p>
      ) : (
        <ul className="links__list">
          {links.backlinks.map((backlink) => (
            <li key={`${backlink.source_id}:${backlink.relationship}`}>
              <button
                type="button"
                className="links__item"
                onClick={() => void openNoteById(backlink.source_id)}
              >
                <span className="links__title">{backlink.source_title}</span>
                <span className="tag">
                  {backlink.relationship.replace(/_/g, " ")}
                </span>
                <span className="links__context">{backlink.context}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <h2 className="sidebar__heading">Outgoing ({links.outgoing.length})</h2>
      <ul className="links__list">
        {links.outgoing.map((link) => (
          <li
            key={`${link.relationship}:${link.target}`}
            className="links__outgoing"
          >
            <span className="mono">{link.relationship.replace(/_/g, " ")}</span>
            <span>→ {link.target}</span>
          </li>
        ))}
      </ul>

      {broken.length > 0 && (
        <>
          <h2 className="sidebar__heading">Broken links ({broken.length})</h2>
          <ul className="links__list">
            {broken.map((entry) => (
              <li key={entry.target} className="links__broken">
                <span className="tag tag--warning">{entry.target}</span>
                <span className="links__context">
                  This note does not exist yet.
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {links.unlinked_mentions.length > 0 && (
        <>
          <h2 className="sidebar__heading">
            Unlinked mentions ({links.unlinked_mentions.length})
          </h2>
          <ul className="links__list">
            {links.unlinked_mentions.map((mention) => (
              <li key={mention.source_id}>
                <button
                  type="button"
                  className="links__item"
                  onClick={() => void openNoteById(mention.source_id)}
                >
                  <span className="links__title">{mention.source_title}</span>
                  <span className="links__context">{mention.context}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <ConnectionSuggestions />
    </section>
  );
}
