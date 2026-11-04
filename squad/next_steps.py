"""Autonomous next steps: the loop decides what to do next cycle.

Deterministic and model-free: `compute_next_steps` reads recent cycle
summaries and returns next-cycle params (mode pin, event focus, history
depth) plus human-readable reasons. Runners apply them; explicit
operator pins (`--mode`, `--event`) always win via `apply_next_steps`.

Policy (revives the retired rule-based supervisor, minus the process):
- Fresh links first: follow-up endpoints from the last cycle become
  the next cycle's event focus, same discovery mode. This closes the
  topic -> news -> topics loop with no operator in the middle.
- Challenged links get more evidence: a cycle with auditor vetoes
  re-runs the same mode with a deep history dig.
- Two quiet rounds (0 pairs) trigger one deep history round, then
  rotation resumes.
- Failed cycles are neutral: they neither advance nor reset streaks.
"""

DEEP_SNAPS = 8
DEEP_HOURS = 48.0
DEEP_STRIDE_HOURS = 6.0
QUIET_STREAK = 2
KEEP_RECENT = 5


def _defaults(reason: str) -> dict:
    return {"mode_pin": None, "event_filter": [], "deep": False,
            "history_snaps": 1, "history_hours": DEEP_HOURS,
            "history_stride_hours": DEEP_STRIDE_HOURS,
            "reasons": [reason]}


def compute_next_steps(recent: list[dict]) -> dict:
    """Next-cycle params from recent cycle summaries (oldest first).

    Summaries are `run_cycle` results: {ok, mode, pairs, vetoed,
    followups, followup_events?}. Only `ok` summaries participate;
    anything else yields rotation defaults. Pure: no I/O, no model.
    """
    ok = [s for s in (recent or [])
          if isinstance(s, dict) and s.get("ok")]
    if not ok:
        return _defaults("no-completed-cycle-yet")
    last = ok[-1]
    fups = [f for f in (last.get("followup_events") or [])
            if isinstance(f, dict) and f.get("event_ticker")]
    if fups:
        tickers: list[str] = []
        for f in fups:
            t = str(f["event_ticker"])
            if t not in tickers:
                tickers.append(t)
        via = str(fups[0].get("via") or "") or None
        out = _defaults(f"follow-up dig into {len(tickers)} "
                        f"fresh link endpoint(s)")
        out["event_filter"] = tickers
        out["mode_pin"] = via
        return out
    if last.get("vetoed", 0) > 0:
        out = _defaults(f"challenged pair(s) in {last.get('mode')}: "
                        f"same mode, deep dig for more evidence")
        out["mode_pin"] = last.get("mode") or None
        out["deep"] = True
        out["history_snaps"] = DEEP_SNAPS
        return out
    streak = [s for s in ok[-QUIET_STREAK:]
              if s.get("pairs", 0) == 0]
    if len(streak) >= QUIET_STREAK:
        out = _defaults("two quiet rounds: one deep history round")
        out["deep"] = True
        out["history_snaps"] = DEEP_SNAPS
        return out
    return _defaults("rotate")


def apply_next_steps(cli_mode: str | None, cli_events: tuple,
                     cli_depth: dict, engine: dict) -> dict:
    """Merge operator pins with engine output. Pins always win.

    `cli_depth` is {history_snaps, history_hours,
    history_stride_hours} from flags; the engine's depth applies only
    when it asks for a deep round.
    """
    depth = dict(cli_depth)
    if engine.get("deep"):
        depth = {"history_snaps": engine.get("history_snaps", DEEP_SNAPS),
                 "history_hours": engine.get("history_hours", DEEP_HOURS),
                 "history_stride_hours": engine.get(
                     "history_stride_hours", DEEP_STRIDE_HOURS)}
    return {"mode_pin": cli_mode or engine.get("mode_pin"),
            "event_slice": tuple(cli_events) or tuple(
                engine.get("event_filter") or ()),
            "reasons": list(engine.get("reasons") or []), **depth}
