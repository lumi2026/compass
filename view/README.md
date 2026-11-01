# Compass view

React 3D view over the live graph API. Same color language as the
standalone viewer. Read-only for now; feedback loop stays a later step.

## Run

Terminal 1 — graph API:

```
uv run python -m services.graph_server --port 8090
```

Terminal 2 — view:

```
cd view && npm install && npm run dev
```

Open http://localhost:5173 in a browser.

`VITE_GRAPH_URL` overrides the backend (default is the dev proxy to
`:8090`). `VITE_GRAPH_TOKEN` pre-wires the future Bearer gate.

The as-of box filters by world time (`valid_at`). Odds stay live until
the replay step lands.
