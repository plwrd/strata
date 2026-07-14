/** Layer list: visibility, state, and AI policy at a glance. */

import { useStore } from "../../state/store";

export function LayerPanel(): JSX.Element {
  const layers = useStore((state) => state.layers);

  return (
    <section className="layers" aria-label="Layers">
      <h2 className="sidebar__heading">Layers</h2>
      <ul className="layers__list">
        {layers.map((layer) => {
          const locked =
            layer.visibility === "private" && layer.state !== "unlocked";
          return (
            <li key={layer.id} className="layers__item">
              <span
                className={`layers__dot layers__dot--${locked ? "locked" : layer.visibility}`}
                aria-hidden="true"
              />
              <span className="layers__name">{layer.display_name}</span>
              <span
                className={`tag ${locked ? "tag--locked" : layer.visibility === "private" ? "tag--private" : "tag--public"}`}
              >
                {locked ? "locked" : layer.visibility}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="layers__hint">
        Private encrypted layers arrive in Milestone 3. A layer is a permission
        and encryption boundary, not a folder.
      </p>
    </section>
  );
}
