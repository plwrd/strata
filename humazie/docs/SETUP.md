# Humazie Bot

Autonomous product-review loop for the Strata React frontend.

## What it does

1. Discovers Strata modes, dialogs, forms, and panels
2. Generates product-specific Playwright UI flows
3. Executes them against the Vite **Humazie harness** (`humazie.html` + fake bridge)
4. Records actions, screenshots, traces, console/network errors
5. Classifies issues and optionally applies narrow safe fixes on a Git branch
6. Verifies with lint/typecheck/unit tests + flow rerun
7. Writes a Markdown report under `.humazie/runs/<run-id>/`
8. Exposes a local Next.js dashboard on port 3310

## Why a harness (not Qt)?

Strata is a PySide6 desktop app. Humazie reviews the React shell in Chromium via
Playwright. The harness installs the existing Vitest `fakeBridge` so no real
workspace, encryption keys, or remote AI calls are required.

## Quick start

```powershell
# From repo root
npm --prefix humazie install
npm --prefix humazie/dashboard install
npx --prefix humazie playwright install chromium

# Optional SQLite history
copy humazie\.env.example humazie\.env
# Prefer an absolute file: URL in .env, then:
npm --prefix humazie run db:push

# Serve the frontend harness (or let review start it)
npm --prefix frontend run humazie:serve

# Discover / review
npm --prefix humazie run humazie:discover

# Watch the bot like a human (default): Chromium opens, highlights, types slowly
npm --prefix humazie run humazie:review -- --no-fix --pace=balanced
npm run humazie:watch -- --route=capture          # demo pace (slower, clearer)
npm run humazie:watch -- --pace=brisk             # faster full suite

# Pace guide:
#   demo     — slow typing + longer highlights (easiest to follow)
#   balanced — default: readable typing, shorter waits (not endless)
#   brisk    — quicker for long suites / CI watch

# CI / background (no browser window)
npm --prefix humazie run humazie:review -- --headless --no-fix --pace=brisk
npm --prefix humazie run humazie:report

# Dashboard
npm --prefix humazie run humazie:dashboard
# open http://127.0.0.1:3310
```

Root convenience scripts (if present in package.json / docs):

- `npm run humazie:discover`
- `npm run humazie:watch` — visible human-like review
- `npm run humazie:review`
- `npm run humazie:report`

Visual mode knobs in `humazie.config.ts` → `visual`:

- `headed` — open a real Chromium window
- `pace` — `demo` | `balanced` | `brisk`
- `typeDelayMs` — per-keystroke delay (slower = easier to read)
- `pauseAfterActionMs` — beat after clicks/typing
- `pauseAfterExpectMs` — shorter beat after “look for” checks
- `highlightMs` / `slowMoMs` — spotlight + Playwright slowMo
- `narrate` — on-screen caption of what the bot is doing

## Configuration

Edit [`humazie.config.ts`](../humazie.config.ts) at the repository root.

Important knobs:

- `baseUrl` — harness URL
- `safeActions` / `unsafeActions`
- `autoRepair.enabled`, file/line limits
- lint / typecheck / test / build commands
- accessibility fail thresholds
- `visual.*` — human watch mode (see above)

## Auth / secrets

Strata has no user accounts. Do not commit passwords.

See [`humazie/.env.example`](./.env.example) for optional database URL and future
credential placeholders.

## Safety

Automatic repair never pushes to `main`, never wipes keys, never calls remote AI,
and reverts when verification fails. Broad or auth/crypto changes require manual
review.

## Architecture

See [architecture.md](./architecture.md).
