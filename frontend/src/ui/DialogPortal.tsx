/**
 * Mount dialogs on document.body.
 *
 * The navigator and inspector use `container-type` / `transform` for layout and
 * compositing. Those create containing blocks, so a `position: fixed` backdrop
 * rendered inside them is clipped to the rail instead of the viewport — which
 * is how Unlock ended up as a broken strip in the sidebar with no usable input.
 */

import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export function DialogPortal({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  return createPortal(children, document.body);
}
