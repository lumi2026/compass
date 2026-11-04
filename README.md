# Compass — Your Situation-Aware AI Consultant

Know what changed. Understand why. Decide what's next.

Compass is an AI-powered situational intelligence platform where autonomous AI agents explore, understand, and track an evolving world, and humans review what they found.

Unlike traditional AI assistants that mainly answer questions, Compass proactively monitors developments, builds contextual understanding, and surfaces its findings for you to explore. (Human ask-and-challenge loop is tracked future work — see below.)

## What it does

- Tracks real-world situations as they evolve over time.
- Connects events, evidence, and hypotheses into one living picture.
- Surfaces what changed and why it matters.
- Lets you explore the living picture (ask-and-challenge loop is future work).

## How it works

Compass combines two ideas:

- **Living Event Graph:** a dynamic knowledge map linking events, sources, evidence, and hypotheses over time.
- **Agent Squad:** specialized AI agents that discover information, investigate events, identify connections, and test hypotheses.

Humans stay in the loop to guide investigations and sharpen results.
(Note: the live feedback loop is future state — the view is read-only
today, and `WorkspaceTask` feedback types are accepted by the gate but
not yet wired to a UI loop.)

## MVP scope

The MVP demonstrates the approach on prediction-market signals and
recurring news scans: Kalshi events and markets flow into the Living
Event Graph, the news squad interconnects them with evidence, and the
3D view renders the result. The architecture is designed to support
additional sources and personal applications beyond that.

## Architecture

Five layers, one direction. Upstream data flows down; nothing above
reaches past the layer below it except through the two HTTP services.

```
Kalshi REST / socket ─┐
GDELT DOC 2.0 ────────┤
GDELT NGrams snaps ───┘
        │  sources/ — normalize once, typed objects out
        ▼
kalshi feed :8080 ──► graph API :8090 ──► view :5173
services/               (curated store)     (read-only 3D)
        ▲                       ▲
        │ odds/sweeps           │ gated proposals
        │                       │
        └──── squad/ via tools/ ┘
          lanes + brain, no direct writes
          next steps carry over: follow-up digs,
          challenge re-scans, deep round after 2 quiet
```

| Layer | Folder | Role |
|---|---|---|
| Information sources | `sources/` | One adapter per upstream (`kalshi`, `gdelt`, `ngrams`) plus shared `base` contracts. Stateless, keyless, parse-at-boundary. [README](sources/README.md) |
| Services | `services/` | Two local HTTP services. Kalshi feed `:8080` (live socket + REST fallback, SSE/webhooks, odds publishing). Graph API `:8090` (single-writer curated store, atomic snapshots, append-only log). [README](services/README.md) |
| Agent squad | `squad/` | Lanes (scout, news-agent, investigator, connector, auditor) plus a model-driven `brain`. All writes go through the Skeptic gate. [README](squad/README.md) |
| Service tools | `tools/` | `{name: {description, func}}` registries the brain calls — Kalshi, graph, news-cycle launchers. Fail-soft dicts, never exceptions. [README](tools/README.md) |
| View | `view/` | React 3D over `GET /graph` (5s poll, `If-None-Match`). Events-first, markets/subjects hidden, hypotheses as relation-colored edges. [Init guide](view/README.md) |

Shared state lives in two places: the graph store (`data_local/`,
gitignored — snapshot + commit log + findings) and frozen seeds for
offline runs. The commit log is the audit trail and the odds history;
time travel replays it.

### One upkeep pass

`scripts/run_compass.py` repeats `STANDING_GOAL`: snapshot the graph →
discover Kalshi events → sync watchlist → fan out `news_loop` stage
cycles (discover → scan → pair → propose → commit, auditor vetoing bad
pairs) → Connector derives cross-event links → findings note. Between
cycles the loop proposes its own next move (`squad/next_steps.py`,
no model cost): follow-up endpoints first, challenged links re-scanned
deep, one deep history round after two quiet rounds — and each pass
starts by executing the proposed step. Each
agent writes only `Proposal`s; the graph API gate is the single commit
point.

### Conventions

- Contracts first: every module states its contract in the docstring;
  tests assert the contract.
- Fail-soft upstream: per-event/per-article failures skip, never abort.
- Local-only: both services bind `127.0.0.1`, no auth yet (pre-public
  token gate is tracked work) — do not expose them as-is.

Start here: [view/README.md](view/README.md) (full init, backends
included), [docs/live_test.md](docs/live_test.md) (verification
commands), [docs/progress.md](docs/progress.md) (build history).
