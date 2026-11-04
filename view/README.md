# Compass view — full init guide

React 3D view over the live Living Event Graph. Read-only: it renders
`GET /graph`, never writes. This guide starts the whole backend stack
first, then the view. All commands run from the repo root unless noted.

## Prerequisites

- Python side: `uv` with the project venv (backend services).
- View side: node 22 + npm 10 (`node_modules` is already installed;
  re-run `npm install` inside `view/` only after pulling new deps).
- Ports free: `8090` (graph API), `8080` (kalshi service), `5173`
  (view dev server).

## 0. Environment (`.env`, repo root)

Only needed for live/LLM paths; the view itself needs no keys.

| Key | Needed for |
|---|---|
| `KALSHI_KEY_ID` + `KALSHI_PRIVATE_KEY` (or `..._PATH`) | Live Kalshi socket (without them the feed falls back to REST polling) |
| `MODEL_API_KEY` | LLM summaries, relevance, audits (without it lanes use extractive fallbacks) |
| `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` | Tracing (opt-in; key alone never enables it) |

`.env` fills gaps only — real environment variables win.

## 1. Graph API `:8090` (start first)

Owns the curated graph; everything else reads/writes through it.

```sh
uv run python -m services.graph_api --port 8090
```

State lives in `data_local/` (`COMPASS_DATA_DIR` overrides):
`graph_snapshot.json`, `graph_log.jsonl`, `findings.jsonl`. Boot loads
the snapshot and replays the log tail — your existing rev-235 state
loads as-is. Check `curl 127.0.0.1:8090/health` → `{ok, nodes, edges,
revision}`.

## 2. Kalshi service `:8080` (start second)

Live market feed + odds publishing into the graph.

```sh
uv run python scripts/kalshi_service.py --port 8080 \
  --graph-url http://127.0.0.1:8090
```

Without keys it runs in poll mode (socket needs auth). Add `--sweep`
once the watchlist is non-empty to prime the graph, or let the news
loop sync it. Check `curl 127.0.0.1:8080/health` → watchlist size,
publish wiring.

## 3. Builders (optional — the graph already has data)

Main agent (standalone — talks to the local `:8090` / `:8080`
defaults, no URLs needed):

```sh
uv run python scripts/run_compass.py --loop
```

Add `--interval-min 15` to slow the cycle; `--max-cycles N` to stop
after N cycles.

News loop (one slice, exits after N cycles):

```sh
uv run python scripts/news_loop.py --graph-url http://127.0.0.1:8090 --kalshi-url http://127.0.0.1:8080 --once
```

Add `--loop --interval-min 15` to keep building.

Audit loop (flags bad links, never deletes):

```sh
uv run python scripts/audit_loop.py --loop --interval-min 60
```

Skip these for a first look — the frozen `data_local/` state already
renders.

## 4. View `:5173`

```sh
cd view && npm run dev
```

Open http://localhost:5173. The dev server proxies `/graph` +
`/health` to `:8090`, so no CORS setup and no auth header needed
locally.

| Env (prefix `VITE_`, set before `npm run dev`) | Effect |
|---|---|
| `VITE_GRAPH_URL` | Talk to a non-default backend (default: same-origin proxy → `:8090`) |
| `VITE_GRAPH_TOKEN` | Bearer [REDACTED] for the future authed API (no-op until the server gate lands) |

Production bundle: `npm run build` (outputs `view/dist/`), preview with
`npm run preview`. Tests: `npm test` (transform + zoom math, stdlib
only).

## What you will see

- Events-first: blue Event nodes linked through articles and purple
  hypothesis edges. Markets (grey) and Subjects are hidden by default —
  the bar reports the hidden counts; tick `markets` / `subjects` to
  reveal each layer. Events always stay, even unlinked controls.
- Click an Event for category · series · lead market + live price,
  article titles, hypothesis titles. Click a purple link for the claim,
  its evidence, and any audit challenge flag.
- `+`/`-` zoom, Reset refits. Polls every 5s (`If-None-Match`, cheap
  `304` when unchanged); killing the API shows a stale banner and
  recovers on restart.
- The as-of input filters by world time (`valid_at`). Odds stay live
  on older frames until the replay step lands — faithfully labeled in
  the UI.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Graph API unreachable` banner | `:8090` not up — start Terminal 1 first, then reload |
| Bar shows `0 nodes` | Empty store (fresh `data_local/`) — run a sweep or one news cycle |
| `address already in use` | Old service still bound — `lsof -i :8090` / `:8080` / `:5173`, stop it, retry |
| Kalshi 429s in the service log | Upstream throttle; per-event failures skip, nothing commits half-done |
| Newest snapshot 404s in loop logs | Normal — freshest 15-min mark hasn't landed; resolver starts 10 min back |
| Empty `LLM` output, `engine=extractive` | No `MODEL_API_KEY` — intended offline fallback, not an error |
| No LangSmith runs | Setup call missing or `LANGSMITH_TRACING` unset — both required |
