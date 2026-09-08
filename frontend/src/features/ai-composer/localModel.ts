/** Local-model ids shared with Python's ``app.domain.local_model``. */

export const DEFAULT_LOCAL_MODEL = "qwythos";

const LEGACY_LOCAL_DEFAULTS = new Set(["", "default", "deepseek-r1:7b"]);

export function isQwythos(model: string): boolean {
  const name = model.trim().toLowerCase();
  if (!name) return false;
  return (
    name.includes("qwythos") ||
    name.includes("claude-mythos") ||
    name.includes("mythos-5")
  );
}

export function pickInstalledModel(
  available: string[],
  preferred = "",
): string {
  const ids = available.map((id) => id.trim()).filter(Boolean);
  const pref = preferred.trim();
  const legacy = LEGACY_LOCAL_DEFAULTS.has(pref.toLowerCase());

  const match = (target: string): string | undefined =>
    ids.find((id) => id.toLowerCase() === target.toLowerCase());
  const qwythos = ids.find(isQwythos);

  if (ids.length === 0) {
    return pref || DEFAULT_LOCAL_MODEL;
  }
  if (pref && !legacy) {
    const found = match(pref);
    if (found) return found;
  }
  if (qwythos) return qwythos;
  return match(pref) ?? ids[0];
}
