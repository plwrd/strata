import type { Locator, Page } from "playwright";

export type VisualOptions = {
  enabled: boolean;
  headed: boolean;
  pace?: "demo" | "balanced" | "brisk";
  slowMoMs: number;
  highlightMs: number;
  pauseAfterActionMs: number;
  pauseAfterExpectMs: number;
  typeDelayMs: number;
  narrate: boolean;
  cursor: boolean;
};

const OVERLAY_ID = "humazie-visual-overlay";
const HIGHLIGHT_ID = "humazie-visual-highlight";
const CURSOR_ID = "humazie-visual-cursor";

const VISUAL_CSS = `
#${OVERLAY_ID} {
  position: fixed;
  left: 16px;
  right: 16px;
  bottom: 16px;
  z-index: 2147483646;
  pointer-events: none;
  font: 600 14px/1.35 "Segoe UI", system-ui, sans-serif;
  color: #061018;
  background: linear-gradient(90deg, #7dd3fc, #a7f3d0);
  border: 1px solid rgba(255,255,255,0.35);
  border-radius: 12px;
  padding: 12px 16px;
  box-shadow: 0 12px 40px rgba(0,0,0,0.35);
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 160ms ease, transform 160ms ease;
}
#${OVERLAY_ID}[data-visible="true"] {
  opacity: 1;
  transform: translateY(0);
}
#${OVERLAY_ID} strong {
  display: block;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.7;
  margin-bottom: 4px;
}
#${HIGHLIGHT_ID} {
  position: fixed;
  z-index: 2147483645;
  pointer-events: none;
  border: 3px solid #38bdf8;
  border-radius: 10px;
  box-shadow:
    0 0 0 9999px rgba(3, 12, 22, 0.28),
    0 0 0 4px rgba(56, 189, 248, 0.35),
    0 10px 30px rgba(56, 189, 248, 0.45);
  transition: top 120ms ease, left 120ms ease, width 120ms ease, height 120ms ease, opacity 120ms ease;
  opacity: 0;
}
#${HIGHLIGHT_ID}[data-visible="true"] { opacity: 1; }
#${CURSOR_ID} {
  position: fixed;
  z-index: 2147483647;
  width: 18px;
  height: 18px;
  margin-left: -3px;
  margin-top: -3px;
  border-radius: 50% 50% 50% 0;
  transform: rotate(-15deg);
  background: #f8fafc;
  border: 2px solid #0ea5e9;
  box-shadow: 0 4px 14px rgba(0,0,0,0.35);
  pointer-events: none;
  opacity: 0;
  transition: top 180ms ease, left 180ms ease, opacity 120ms ease;
}
#${CURSOR_ID}[data-visible="true"] { opacity: 1; }
`;

export async function ensureVisualChrome(page: Page, options: VisualOptions): Promise<void> {
  if (!options.enabled) return;
  await page.addStyleTag({ content: VISUAL_CSS });
  // Keep this evaluate free of nested functions — tsx can inject __name helpers
  // that break when Playwright serializes the callback into the browser.
  await page.evaluate(
    ([overlayId, highlightId, cursorId]) => {
      const ids = [overlayId, highlightId, cursorId];
      for (let i = 0; i < ids.length; i += 1) {
        const id = ids[i]!;
        let el = document.getElementById(id);
        if (!el) {
          el = document.createElement("div");
          el.id = id;
          document.documentElement.appendChild(el);
        }
      }
    },
    [OVERLAY_ID, HIGHLIGHT_ID, CURSOR_ID] as [string, string, string],
  );
}

export async function narrate(
  page: Page,
  options: VisualOptions,
  title: string,
  detail: string,
): Promise<void> {
  if (!options.enabled || !options.narrate) return;
  await ensureVisualChrome(page, options);
  await page.evaluate(
    ([overlayId, titleText, detailText]) => {
      const el = document.getElementById(overlayId);
      if (!el) return;
      el.innerHTML =
        "<strong>" +
        titleText +
        "</strong><div>" +
        detailText +
        "</div>";
      el.setAttribute("data-visible", "true");
    },
    [OVERLAY_ID, title, detail] as [string, string, string],
  );
}

export async function clearNarration(page: Page, options: VisualOptions): Promise<void> {
  if (!options.enabled || !options.narrate) return;
  await page.evaluate((overlayId) => {
    const el = document.getElementById(overlayId);
    if (el) el.setAttribute("data-visible", "false");
  }, OVERLAY_ID);
}

export async function highlightLocator(
  page: Page,
  locator: Locator,
  options: VisualOptions,
): Promise<void> {
  if (!options.enabled) return;
  await ensureVisualChrome(page, options);
  const box = await locator.boundingBox();
  if (!box) return;
  await page.evaluate(
    ([highlightId, cursorId, y, x, width, height, showCursor]) => {
      const ring = document.getElementById(highlightId);
      if (ring) {
        ring.style.top = Math.max(0, (y as number) - 6) + "px";
        ring.style.left = Math.max(0, (x as number) - 6) + "px";
        ring.style.width = (width as number) + 12 + "px";
        ring.style.height = (height as number) + 12 + "px";
        ring.setAttribute("data-visible", "true");
      }
      const cursor = document.getElementById(cursorId);
      if (cursor && showCursor) {
        cursor.style.top = (y as number) + (height as number) / 2 + "px";
        cursor.style.left = (x as number) + (width as number) / 2 + "px";
        cursor.setAttribute("data-visible", "true");
      }
    },
    [
      HIGHLIGHT_ID,
      CURSOR_ID,
      box.y,
      box.x,
      box.width,
      box.height,
      options.cursor,
    ] as [string, string, number, number, number, number, boolean],
  );
  await page.waitForTimeout(options.highlightMs);
}

export async function clearHighlight(page: Page, options: VisualOptions): Promise<void> {
  if (!options.enabled) return;
  await page.evaluate(
    ([highlightId, cursorId]) => {
      const ring = document.getElementById(highlightId);
      if (ring) ring.setAttribute("data-visible", "false");
      const cursor = document.getElementById(cursorId);
      if (cursor) cursor.setAttribute("data-visible", "false");
    },
    [HIGHLIGHT_ID, CURSOR_ID] as [string, string],
  );
}

export async function humanPause(page: Page, options: VisualOptions, ms?: number): Promise<void> {
  if (!options.enabled) return;
  await page.waitForTimeout(ms ?? options.pauseAfterActionMs);
}

export async function humanClick(
  page: Page,
  locator: Locator,
  options: VisualOptions,
  label: string,
  timeout: number,
): Promise<void> {
  const target = locator.first();
  await target.waitFor({ state: "visible", timeout });
  await target.scrollIntoViewIfNeeded();
  await narrate(page, options, "Click", label);
  await highlightLocator(page, target, options);
  await target.click({ timeout });
  await clearHighlight(page, options);
  await humanPause(page, options);
}

export async function humanType(
  page: Page,
  locator: Locator,
  value: string,
  options: VisualOptions,
  label: string,
  timeout: number,
): Promise<void> {
  const target = locator.first();
  await target.waitFor({ state: "visible", timeout });
  await target.scrollIntoViewIfNeeded();
  await narrate(page, options, "Type", label);
  await highlightLocator(page, target, options);
  await target.click({ timeout });
  await target.fill("");
  if (options.enabled) {
    await target.pressSequentially(value, { delay: options.typeDelayMs });
  } else {
    await target.fill(value, { timeout });
  }
  await clearHighlight(page, options);
  await humanPause(page, options);
}
