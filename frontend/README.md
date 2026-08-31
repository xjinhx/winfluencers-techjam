# Shopping Copilot — hosted demo (storefront + chat)

Implements `PRD_demo_frontend.md`. Two services:

- **`api/`** — a thin FastAPI wrapper around the real, unmodified
  `starter.agent.Agent` (the actual competition entry point). It does not
  touch `shopping_copilot/`, `evaluator/`, or `config/tuned.json` — it only
  imports them and adds catalog lookups so the UI has something to render
  besides a bare `parent_asin`.
- **`web/`** — a Vite + React + TypeScript app implementing the two Figma
  screens (Storefront, Chat). No Tailwind, no UI framework — plain CSS driven
  off the Figma file's design tokens (`web/src/styles/tokens.css`).

This is a presentation artifact (Devpost demo video + a shareable link). It
has zero effect on `TechnicalScore` — the competition evaluates `agent.py`
headlessly through `evaluator/local_evaluator.py`, not through this app.

## Run it locally

**Terminal 1 — API** (from the repo root):

```bash
python -m venv frontend/api/.venv
frontend/api/.venv/Scripts/python -m pip install -r frontend/api/requirements.txt   # Windows
# frontend/api/.venv/bin/pip install -r frontend/api/requirements.txt              # macOS/Linux

frontend/api/.venv/Scripts/python -m uvicorn main:app --app-dir frontend/api --host 127.0.0.1 --port 8000
```

Requires `data/catalog.jsonl` and `data/public_set.jsonl` to exist locally
(they're gitignored/frozen — see the root `CLAUDE.md`). Health check:
`curl http://127.0.0.1:8000/health` should return the catalog row count.

**Terminal 2 — web:**

```bash
cd frontend/web
npm install
cp .env.example .env.local   # VITE_API_URL=http://localhost:8000
npm run dev
```

Open the printed localhost URL. The storefront is a static stage set per the
PRD — only the "Ask the Copilot" pill is live. The chat screen calls the real
agent on every send.

## What's real vs. presentation garnish

Per the PRD, the frontend renders exactly what `agent.py`'s `respond()`
contract returns (`message`, `ask_attribute`, a ranked `recommendations`
list of `parent_asin`) plus catalog lookups for display fields. Two things
are explicitly NOT agent output, and are labeled as such in the code:

- **Quick-reply chip values** (`web/src/lib/presentation.ts`) — the agent
  returns a bare attribute name (e.g. `"color"`), not suggested values.
- **The "matched: ..." line** under each recommendation
  (`web/src/components/RecommendationList.tsx`) — `respond()` doesn't return
  per-recommendation reasoning. It's computed client-side by checking which
  words the customer has typed so far appear in that product's title/category.

## Deploying (Railway)

Two services from one Railway project, both pointed at this repo.

**API service** — set the service's root directory to the **repo root**
(not `frontend/api`), because it needs `shopping_copilot/`, `starter/`,
`config/tuned.json`, and `data/` alongside it:

- Build: `pip install -r frontend/api/requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --app-dir frontend/api`
  (already in `frontend/api/Procfile`)
- `data/catalog.jsonl` is gitignored and frozen per the submission rules —
  it has to reach the deployed container some other way (a Railway volume,
  a private release asset, whatever Jinhong already uses to get it onto a
  machine). This app does not download or regenerate it.
- Env var: `FRONTEND_ORIGIN` — comma-separated list of allowed origins
  (the web service's public URL). Defaults to `*` if unset, which is fine
  for a demo but worth tightening once the web URL is known.

**Web service** — root directory `frontend/web`:

- Build: `npm install && npm run build`
- Start: serve `dist/` as static files (Railway's static/Nixpacks preset, or
  `npx serve dist`)
- Env var: `VITE_API_URL` — the API service's public Railway URL (build-time
  env var, since Vite inlines it at build).

Per the PRD's own risk list: confirm hosting a live endpoint mid-competition
doesn't run into any submission/fairness rule before actually going live, and
loop in Dylan since this exposes `agent.py` over a public HTTP API.

## Notes on fidelity to the Figma

Pulled via the Figma MCP server (`get_design_context` on file
`w0QGjScrL7VsKpPKS1cQ6o`, frames `01 — Storefront` / `02 — Chat`, symbol
`ProductCard`). Icons and the ArtSlot placeholder art (`light`/`silhouette`)
were downloaded as real asset files into `web/src/assets/icons/` rather than
redrawn — the Figma export URLs expire in ~7 days, so they're committed as
local files. Four icons (`home`, `inbox`, `profile`, the header `⋯` icon) are
inlined as JSX (`web/src/components/NavIcons.tsx`) instead of `<img src>`, so
`currentColor` can drive active/inactive tinting — an externally-referenced
SVG file can't be recolored from the host page.

The catalog has no product photography (`data/catalog.jsonl` has no image
field at all), which is exactly what the Figma ArtSlot design already assumes
— a solid color field is deterministically picked per `parent_asin`
(`web/src/lib/presentation.ts:artColorFor`) so the same product always gets
the same swatch.
