/**
 * Offline font for drei/troika `<Text>` labels.
 *
 * Troika defaults to Noto via unicode-font-resolver on jsDelivr. That is blocked
 * by Strata's `connect-src 'self'` CSP, so we ship Inter Latin and pass it to
 * every label. Without this, font workers fail and the 3D graph degrades.
 *
 * Labels should stay within the Latin subset (ASCII ellipsis `...`, not `…`) so
 * the unicode CDN fallback is never queried.
 */

import interWoff from "@fontsource/inter/files/inter-latin-400-normal.woff?url";

export const GRAPH_LABEL_FONT = interWoff;
