"""Background news team: one loop, many agent invocations per cycle.

Each cycle explores *across* Kalshi topics (never a fixed task list):
discover topics, pick pairs by the rotating mode, scan one snapshot for
cross-topic terms, extract happenings + hypotheses, and POST everything
through the graph API gate. The loop is the scheduler; Scout/NewsAgent/
Investigator/Connector do the lane work inside the cycle.

Run:  uv run python scripts/news_loop.py --graph-url URL --once
      uv run python scripts/news_loop.py --loop --interval-min 15

Modes rotate per cycle: co-mention, shared-subject, burst,
entity-overlap, odds-correlation. Pin one with --mode for tests.
"""

import concurrent.futures
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import click

sys.path.insert(0, ".")

from graph import article_id  # noqa: E402
from scripts.cross_link import extract_terms  # noqa: E402
from squad.auditor import audit_pair  # noqa: E402
from sources.kalshi import EVENTS_LIMIT_MAX  # noqa: E402
from sources.ngrams import (  # noqa: E402
    articles_from_mentions,
    ngram_mentions,
    resolve_snapshot,
    snapshot_candidates,
)
from squad import Proposal, Skeptic  # noqa: E402
from squad.models import resolve_model  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402

MODES = ("co-mention", "shared-subject", "burst",
         "entity-overlap", "odds-correlation")


def _get_json(url: str, timeout: int = 120):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except Exception as e:
        # Transient 502s from the kalshi service (Kalshi upstream
        # flakes) observed mid-loop. One retry saves the cycle; the
        # caller still decides (fail-soft paths stay fail-soft).
        log(f"GET retry: {url.rsplit('/', 1)[-1]} ({type(e).__name__})")
        time.sleep(5)
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _graph_events(graph_url: str) -> dict:
    """ticker -> title for Event nodes already in the graph.

    Fail-open: an unreachable graph looks like an empty graph, so the
    cycle degrades to pure discovery instead of dying.
    """
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
    except Exception:
        return {}
    out = {}
    for el in data.get("elements", []):
        d = el.get("data", {})
        if d.get("id", "").startswith("kalshi:"):
            t = d["id"].removeprefix("kalshi:")
            out[t] = d.get("label") or t
    return out


def discover_topics(kalshi_url: str, event_slice: tuple = (),
                    limit: int = 30, graph_url: str | None = None) -> list[dict]:
    """Kalshi events -> [{event_ticker, title, terms, new}]. Slice restricts.

    Without a slice the standing graph set comes first, then open
    events not yet graphed, up to `limit` newcomers. Titles always
    come from Kalshi (graph titles can be bare tickers with no
    searchable terms). An explicit slice behaves exactly as before.
    """
    if event_slice:
        data = _get_json(kalshi_url.rstrip("/") + f"/events?limit={limit}")
        open_evts = [e for e in data.get("events", [])
                     if e.get("event_ticker")]
        picked = [e for e in open_evts if e["event_ticker"] in event_slice]
        topics = [{"event_ticker": e["event_ticker"],
                   "title": e.get("title", e["event_ticker"]),
                   "terms": extract_terms(e.get("title", e["event_ticker"])),
                   "new": False} for e in picked]
        return [t for t in topics if t["terms"]]
    known = set(_graph_events(graph_url)) if graph_url else set()
    # Newcomers are capped at `limit`; fetch past the standing set so a
    # full head of known events cannot hide them. Clamped: Kalshi 400s
    # /events above EVENTS_LIMIT_MAX, and this sum grows with the graph.
    fetch_limit = min(limit + len(known), EVENTS_LIMIT_MAX)
    data = _get_json(
        kalshi_url.rstrip("/") + f"/events?limit={fetch_limit}")
    open_evts = [e for e in data.get("events", [])
                 if e.get("event_ticker")]
    standing, fresh = [], []
    for e in open_evts:
        ticker = e["event_ticker"]
        terms = extract_terms(e.get("title", ticker))
        if not terms:
            continue
        topic = {"event_ticker": ticker,
                 "title": e.get("title", ticker),
                 "terms": terms, "new": ticker not in known}
        (standing if ticker in known else fresh).append(topic)
    return standing + fresh[:limit]


def _ensure_event_nodes(graph_url: str, topics: list[dict]) -> int:
    """Post Scout Event nodes for newcomers so discovery lands in the graph.

    Fail-open: any error posts nothing and the cycle continues with the
    in-memory topics. Duplicates merge in the gate, so a race with the
    kalshi publish path is harmless.
    """
    fresh = [t for t in topics if t.get("new")]
    if not fresh:
        return 0
    try:
        resp = _post_json(
            graph_url.rstrip("/") + "/proposals",
            {"proposals": [
                {"agent": "scout", "action": "add-node",
                 "entity_id": f"kalshi:{t['event_ticker']}",
                 "detail": json.dumps({"kind": "Event",
                                       "title": t["title"]}),
                 "confidence": 0.85} for t in fresh]})
    except Exception as e:
        log(f"new events: {len(fresh)} skipped ({type(e).__name__})")
        return 0
    n = sum(1 for v in resp.get("verdicts", []) if v.get("committed"))
    log(f"new events: {len(fresh)} posted {n}")
    return n


def sync_watchlist(kalshi_url: str, topics: list[dict]) -> dict:
    """Push the discovered events to the kalshi service watchlist.

    Fail-open: a down or stub kalshi service must never kill the cycle —
    odds-correlation reads REST directly, the watchlist only feeds
    ticks/sweeps/snapshots.
    """
    tickers = [t["event_ticker"] for t in topics if t.get("event_ticker")]
    if not tickers:
        return {"synced": False, "reason": "no topics"}
    try:
        resp = _post_json(kalshi_url.rstrip("/") + "/topics",
                          {"events": tickers})
    except Exception as e:
        return {"synced": False,
                "reason": f"kalshi unreachable: {e}"}
    watching = resp.get("tickers", tickers)
    log(f"watchlist synced: {len(watching)} tickers")
    return {"synced": True, "tickers": len(watching)}


def next_mode(cycle: int, pinned: str | None = None) -> str:
    if pinned and pinned in MODES:
        return pinned
    return MODES[cycle % len(MODES)]


def log(msg: str) -> None:
    """Stage log, always to stdout (the loop's only log sink)."""
    print(f"[news-loop] {msg}", flush=True)


def _topic_urls(topics: list[dict], mentions: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t in topics:
        urls: set[str] = set()
        for term in t["terms"]:
            urls.update(mentions.get(term, {}).get("urls", []))
        out[t["event_ticker"]] = urls
    return out


def _cap_pairs(pairs: list, max_pairs: int) -> list:
    """Cap pairs per cycle; max_pairs <= 0 means no cap.

    Matches the --max-audits / --max-cycles convention (0 = unbounded).
    """
    return pairs if max_pairs <= 0 else pairs[:max_pairs]


def shared_url_pairs(topics: list[dict], mentions: dict,
                     max_pairs: int = 10) -> list[dict]:
    """Co-mention: topic pairs sharing article URLs in one snapshot."""
    by_topic = _topic_urls(topics, mentions)
    keys = sorted(by_topic)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = sorted(by_topic[a] & by_topic[b])
            if shared:
                pairs.append({"a": a, "b": b, "urls": shared,
                              "mode": "co-mention"})
    pairs.sort(key=lambda p: -len(p["urls"]))
    return _cap_pairs(pairs, max_pairs)


def shared_subject_pairs(topics: list[dict], mentions: dict,
                         max_pairs: int = 10) -> list[dict]:
    """Shared-subject: pairs with a common term, evidence = its articles."""
    term_topics: dict[str, set[str]] = {}
    for t in topics:
        for term in t["terms"]:
            term_topics.setdefault(term, set()).add(t["event_ticker"])
    seen, pairs = set(), []
    for term, tickers in term_topics.items():
        ts = sorted(tickers)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                urls = sorted(set(mentions.get(term, {}).get("urls", [])))
                if urls:
                    pairs.append({"a": a, "b": b, "urls": urls,
                                  "mode": "shared-subject",
                                  "subject": term})
    pairs.sort(key=lambda p: -len(p["urls"]))
    return _cap_pairs(pairs, max_pairs)


def burst_topics(topics: list[dict], new_mentions: dict,
                 old_mentions: dict) -> list[dict]:
    """Topics whose mention counts spiked vs the older snapshot."""
    out = []
    for t in topics:
        new = sum(new_mentions.get(term, {}).get("count", 0)
                  for term in t["terms"])
        old = sum(old_mentions.get(term, {}).get("count", 0)
                  for term in t["terms"])
        if new >= max(3, 2 * old):
            urls: set[str] = set()
            for term in t["terms"]:
                urls.update(new_mentions.get(term, {}).get("urls", []))
            out.append({"event_ticker": t["event_ticker"], "new": new,
                        "old": old, "urls": sorted(urls)})
    return out


def extract_entities(titles: list[str]) -> dict[str, set[str]]:
    """Capitalized-phrase entities per title (no NER dependency)."""
    stop = {"The", "This", "That", "With", "From", "After", "Says", "Said"}
    out: dict[str, set[str]] = {}
    for title in titles:
        ents = set()
        for m in re.finditer(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", title):
            if m.group(1) not in stop:
                ents.add(m.group(1))
        out[title] = ents
    return out


def entity_overlap_pairs(topics: list[dict], kept: list[dict],
                         max_pairs: int = 10) -> list[dict]:
    """Pairs whose kept articles share a capitalized entity."""
    ent_by_topic: dict[str, dict[str, set[str]]] = {}
    for t in topics:
        titles = [k["title"] for k in kept
                  if t["event_ticker"] in k.get("topics", [])]
        merged: dict[str, set[str]] = {}
        for title, ents in extract_entities(titles).items():
            for e in ents:
                merged.setdefault(e, set()).add(title)
        ent_by_topic[t["event_ticker"]] = merged
    keys = sorted(ent_by_topic)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            for e in sorted(set(ent_by_topic[a]) & set(ent_by_topic[b])):
                urls = sorted(k["url"] for k in kept
                              if k["title"] in ent_by_topic[a][e]
                              or k["title"] in ent_by_topic[b][e])
                pairs.append({"a": a, "b": b, "urls": urls,
                              "mode": "entity-overlap", "entity": e})
                break
    return _cap_pairs(pairs, max_pairs)


def _existing_hypotheses(graph_url: str) -> set[str]:
    """Hypothesis node ids already committed (best-effort, for challenges)."""
    try:
        data = _get_json(graph_url.rstrip("/") + "/graph")
        return {e["data"]["id"] for e in data.get("elements", [])
                if e.get("data", {}).get("kind") == "Hypothesis"}
    except Exception:
        return set()


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Correlation of two equal-length series; None when degenerate."""
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = (sum(d * d for d in dx) * sum(d * d for d in dy)) ** 0.5
    if not denom:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def market_closes(history: list[dict]) -> list[float]:
    out = []
    for c in history:
        v = c.get("close")
        if v is not None:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                continue
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def hypothesis_title(a: str, b: str, urls: list[str], arts: list,
                     model=None) -> tuple[str, float]:
    """One-sentence relation for a topic pair. LLM or extractive."""
    titles = [getattr(x, "title", "") for x in arts[:3]]
    if model is not None:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            msg = model.invoke([
                SystemMessage(
                    "You connect prediction-market topics through world news. "
                    "Reply with one sentence stating how the two topics are "
                    "related, then a confidence number 0-1 on its own line."),
                HumanMessage(
                    f"Topic A: {a}\nTopic B: {b}\n"
                    f"Shared articles: {'; '.join(titles)}"),
            ])
            lines = str(msg.content).strip().splitlines()
            conf = 0.6
            for line in lines:
                try:
                    conf = max(0.0, min(0.9, float(line.strip())))
                    break
                except ValueError:
                    continue
            return lines[0].strip()[:300] if lines else "", conf or 0.6
        except Exception:
            pass
    head = "; ".join(t[:80] for t in titles[:2]) or f"{len(urls)} articles"
    return f"shared coverage ({len(urls)} articles): {head}", 0.6


def _kept_from_articles(arts, snapshot_ts: str, topics: list[dict]) -> list[dict]:
    """NewsArticles -> kept dicts tagged with the topics whose terms hit."""
    term_topics: dict[str, set[str]] = {}
    for t in topics:
        for term in t["terms"]:
            term_topics.setdefault(term, set()).add(t["event_ticker"])
    url_topics: dict[str, set[str]] = {}
    kept = []
    for a in arts:
        term = (a.raw or {}).get("term", "")
        owners = term_topics.get(term, set())
        if not owners:
            continue
        url_topics.setdefault(a.url, set()).update(owners)
        kept.append({"url": a.url, "title": a.title,
                     "subjects": sorted(owners | {term}),
                     "snapshot": snapshot_ts, "seendate": a.published_at,
                     "topics": sorted(url_topics[a.url])})
    return kept


def _snapshot_stride_list(hours_back: float = 48, stride_hours: float = 6,
                          dest_dir: str = "/tmp",
                          max_snaps: int = 12) -> list[tuple[str, str]]:
    """Newest-first (ts, path) snapshots spaced ~stride_hours apart.

    Walks back hours_back, downloading what hasn't landed and skipping
    failures, so one late publish never blocks the dig. Bounded: at most
    max_snaps files no matter how wide the window.
    """
    from sources.ngrams import download_snapshot

    step = max(1, round(stride_hours * 60 / 15))
    cands = snapshot_candidates(time.time(),
                                lookback_min=int(hours_back * 60) + 30)
    out: list[tuple[str, str]] = []
    for ts in cands[::step]:
        if len(out) >= max_snaps:
            break
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        if not os.path.exists(dest):
            try:
                download_snapshot(ts, dest)
            except Exception:
                continue
        out.append((ts, dest))
    if not out:
        raise RuntimeError("no downloadable webngrams snapshot")
    return out


def scan_history(terms: list[str], hours_back: float = 48,
                 stride_hours: float = 6, dest_dir: str = "/tmp",
                 per_term_cap: int = 30) -> tuple[dict, list]:
    """Union-term counts merged over stride-spaced snapshots, newest first.

    Returns (merged_mentions, [(ts, mentions)]) so burst mode can compare
    the newest window against the oldest one.
    """
    used = _snapshot_stride_list(hours_back, stride_hours, dest_dir)
    counts: dict[str, int] = {}
    urls: dict[str, set[str]] = {}
    samples: dict[str, list] = {}
    per: list[tuple[str, dict]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ts: ex.submit(ngram_mentions, terms, path, "en",
                              per_term_cap)
                for ts, path in used}
        for ts, _ in used:
            try:
                m = futs[ts].result()
            except Exception:
                continue
            per.append((ts, m))
            for t, mm in m.items():
                counts[t] = counts.get(t, 0) + mm["count"]
                urls.setdefault(t, set()).update(mm["urls"])
                have = samples.setdefault(t, [])
                have.extend(mm["samples"][: max(0, per_term_cap - len(have))])
    merged = {t: {"count": counts.get(t, 0),
                  "urls": sorted(urls.get(t, set())),
                  "samples": samples.get(t, [])} for t in terms}
    return merged, per


def _snapshot_pair(dest_dir: str = "/tmp") -> tuple[str, str, str, str]:
    """(new_ts, new_path, old_ts, old_path): newest + ~1h-older snapshots.

    Walks back over unlanded marks like resolve_snapshot does — the
    freshest mark 404s whenever a publish runs late.
    """
    from sources.ngrams import download_snapshot

    new: tuple[str, str] | None = None
    old: tuple[str, str] | None = None
    cutoff = ""
    for ts in snapshot_candidates(time.time(), lookback_min=180):
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        if not os.path.exists(dest):
            try:
                download_snapshot(ts, dest)
            except Exception:
                continue
        if new is None:
            new = (ts, dest)
            cutoff = (datetime.datetime.strptime(
                ts, "%Y%m%d%H%M%S") - datetime.timedelta(minutes=45)).strftime(
                    "%Y%m%d%H%M%S")
            continue
        if ts <= cutoff:
            old = (ts, dest)
            break
    if new is None:
        raise RuntimeError("no downloadable webngrams snapshot")
    if old is None:
        old = new
    return new[0], new[1], old[0], old[1]


def _kalshi_markets(kalshi_url: str, event_ticker: str) -> list[dict]:
    """Market tickers + quotes for one event (no watchlist side effects)."""
    try:
        data = _get_json(
            kalshi_url.rstrip("/") +
            f"/markets?event={urllib.parse.quote(event_ticker)}")
    except Exception:
        return []
    markets = data.get("markets", [])
    return markets if isinstance(markets, list) else []


def _kalshi_history(kalshi_url: str, ticker: str) -> list[dict]:
    now = int(time.time())
    try:
        data = _get_json(
            kalshi_url.rstrip("/") +
            f"/history?ticker={urllib.parse.quote(ticker)}"
            f"&start={now - 86400}&end={now}&interval=60")
    except Exception:
        return []
    return data if isinstance(data, list) else []


# -- Deterministic subagent stages -------------------------------------
# Each stage is one step of the pipeline with an explicit ctx contract
# (reads documented keys, returns values merged back into ctx), so a
# future orchestrator can dispatch them individually via run_stage.
# run_cycle below is the reference dispatcher: same order, same logs,
# same summaries. No model reasoning lives here beyond the existing
# propose/audit calls; spend is unchanged.
STAGES = ("discover", "scan", "pair", "propose", "commit")


def run_stage(name: str, ctx: dict) -> dict:
    """Dispatch one STAGES step; merges its result into ctx, returns it."""
    fn = {"discover": stage_discover, "scan": stage_scan,
          "pair": stage_pair, "propose": stage_propose,
          "commit": stage_commit}.get(name)
    if fn is None:
        raise KeyError(f"unknown stage: {name} (want one of {STAGES})")
    res = fn(ctx)
    ctx.update(res)
    return res


def stage_discover(ctx: dict) -> dict:
    """ctx: graph_url, kalshi_url, event_slice, event_limit, sync_topics.
    Returns {topics, watch}. Raises on unreachable kalshi."""
    topics = discover_topics(ctx["kalshi_url"],
                             ctx.get("event_slice", ()),
                             ctx.get("event_limit", 30),
                             ctx.get("graph_url"))
    _ensure_event_nodes(ctx["graph_url"], topics)
    watch = (sync_watchlist(ctx["kalshi_url"], topics)
             if ctx.get("sync_topics", True)
             else {"synced": False, "reason": "disabled"})
    return {"topics": topics, "watch": watch}


def stage_scan(ctx: dict) -> dict:
    """ctx: topics, mode, cycle, history_snaps, history_hours,
    history_stride_hours. Returns {mentions, snap_ts, burst_new,
    burst_old}. Raises RuntimeError when no snapshot is available."""
    topics, mode = ctx["topics"], ctx["mode"]
    union = sorted({t for topic in topics for t in topic["terms"]})
    new_n = sum(1 for t in topics if t.get("new"))
    log(f"cycle={ctx.get('cycle', 0)} mode={mode} topics={len(topics)} "
        f"(new={new_n}) terms={len(union)}")
    if ctx.get("history_snaps", 1) > 1:
        try:
            new_m, per = scan_history(union, ctx.get("history_hours", 48),
                                      ctx.get("history_stride_hours", 6))
        except RuntimeError as e:
            raise RuntimeError(f"no snapshot: {e}")
        new_ts = per[0][0] if per else ""
        burst_new = per[0][1] if per else {}
        burst_old = per[-1][1] if len(per) > 1 else {}
    else:
        try:
            new_ts, new_snap, old_ts, old_snap = _snapshot_pair()
        except RuntimeError as e:
            raise RuntimeError(f"no snapshot: {e}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fut_new = ex.submit(ngram_mentions, union, new_snap, "en", 30)
            fut_old = (ex.submit(ngram_mentions, union, old_snap, "en", 30)
                       if mode == "burst" else None)
            new_m = fut_new.result()
            old_m = fut_old.result() if fut_old else {}
        burst_new, burst_old = new_m, old_m
    hit_terms = sum(1 for t in union if new_m.get(t, {}).get("count"))
    total_hits = sum(new_m.get(t, {}).get("count", 0) for t in union)
    log(f"cycle={ctx.get('cycle', 0)} scan hits: {hit_terms}/{len(union)} "
        f"terms, {total_hits} mentions")
    return {"mentions": new_m, "snap_ts": new_ts,
            "burst_new": burst_new, "burst_old": burst_old}


def stage_pair(ctx: dict) -> dict:
    """ctx: topics, mentions, burst_new, burst_old, snap_ts, mode,
    max_pairs, kalshi_url, graph_url, model, cycle.
    Returns {pairs} — or {pairs: [], done: summary} when the cycle
    ends here (empty set, odds-correlation mode)."""
    topics, mode = ctx["topics"], ctx["mode"]
    max_pairs = ctx.get("max_pairs", 10)
    pairs: list[dict] = []
    if mode == "co-mention":
        pairs = shared_url_pairs(topics, ctx["mentions"], max_pairs)
    elif mode == "shared-subject":
        pairs = shared_subject_pairs(topics, ctx["mentions"], max_pairs)
    elif mode == "burst":
        bursting = {b["event_ticker"]: b for b in
                    burst_topics(topics, ctx["burst_new"],
                                 ctx["burst_old"])}
        bt = [t for t in topics if t["event_ticker"] in bursting]
        pairs = shared_url_pairs(bt, ctx["mentions"], max_pairs)
        for p in pairs:
            p["mode"] = "burst"
    elif mode == "entity-overlap":
        arts = articles_from_mentions(ctx["mentions"], ctx["snap_ts"])
        kept = _kept_from_articles(arts, ctx["snap_ts"], topics)
        pairs = entity_overlap_pairs(topics, kept, max_pairs)
    elif mode == "odds-correlation":
        return {"pairs": [],
                "done": _odds_cycle(ctx["graph_url"], ctx["kalshi_url"],
                                    topics, max_pairs, ctx.get("model"))}
    log(f"cycle={ctx.get('cycle', 0)} pairs={len(pairs)} " +
        (", ".join(f"{p['a']}x{p['b']}:{len(p['urls'])}" for p in pairs[:5])
         if pairs else "none"))
    if not pairs:
        _post_json(ctx["graph_url"].rstrip("/") + "/findings",
                   {"finding": {"loop": "news", "mode": mode,
                                "pairs": 0,
                                "cycle": ctx.get("cycle", 0)}})
        return {"pairs": [],
                "done": {"ok": True, "mode": mode, "pairs": 0}}
    return {"pairs": pairs}


def stage_propose(ctx: dict) -> dict:
    """ctx: topics, pairs, mentions, snap_ts, graph_url, model,
    deliberate, cycle. Returns {all_props, pairs (live only),
    vetoed, llm_calls}."""
    topics, pairs = ctx["topics"], ctx["pairs"]
    model, cycle = ctx.get("model"), ctx.get("cycle", 0)
    deliberate = ctx.get("deliberate", False)
    arts = articles_from_mentions(ctx["mentions"], ctx["snap_ts"])
    kept = _kept_from_articles(arts, ctx["snap_ts"], topics)
    url_kept = {k["url"]: k for k in kept}
    agent = NewsAgent()
    skeptic = Skeptic()
    all_props: list[Proposal] = []
    llm_calls = 0
    # Third-agent audit: veto spurious pairs pre-commit; challenge the
    # ones that already committed (reversible flag, never deletion).
    title_of = {t["event_ticker"]: t["title"] for t in topics}
    existing_hids = _existing_hypotheses(ctx["graph_url"])
    audit_llm = resolve_model(model, role="auditor") if model else None
    live_pairs: list[tuple] = []  # (pair, title|None, conf|None)
    vetoed = 0
    for p in pairs:
        ev_titles = [url_kept[u]["title"] for u in p["urls"][:3]
                     if u in url_kept]
        hid = f"hypothesis:loop-{_slug(p['a'])}-{_slug(p['b'])}"
        if deliberate and audit_llm is not None:
            from squad.auditor import deliberate_pair  # noqa: E402

            d = deliberate_pair(
                p["a"], title_of.get(p["a"], p["a"]),
                p["b"], title_of.get(p["b"], p["b"]),
                ev_titles, p["mode"], audit_llm)
            llm_calls += d.turns_used
            keep, reason, dtitle, dconf = (d.keep, d.reason, d.title,
                                           d.confidence)
        else:
            if audit_llm is not None:
                llm_calls += 1
            r = audit_pair(p["a"], title_of.get(p["a"], p["a"]),
                           p["b"], title_of.get(p["b"], p["b"]),
                           ev_titles, p["mode"], audit_llm)
            keep, reason, dtitle, dconf = (r.keep, r.reason, None, None)
        if keep:
            live_pairs.append((p, dtitle, dconf))
            continue
        vetoed += 1
        log(f"cycle={cycle} veto {p['a']}x{p['b']}: {reason}")
        if hid in existing_hids:
            aid = f"audit:{hid}"
            all_props.append(Proposal(
                agent="auditor", action="add-node", entity_id=aid,
                detail=json.dumps(
                    {"kind": "Audit",
                     "title": f"audit flags {hid}: {reason}",
                     "props": {"target": hid, "verdict": "reject",
                               "reason": reason, "mode": p["mode"]}}),
                confidence=0.8))
            all_props.append(Proposal(
                agent="auditor", action="add-edge", entity_id=aid,
                detail=json.dumps({"src": aid, "dst": hid,
                                   "rel": "challenges"}),
                confidence=0.8))
    inv_llm = resolve_model(model, role="investigator") if model else None
    live = [p for p, _, _ in live_pairs]
    for p, dtitle, dconf in live_pairs:
        pair_terms = sorted(
            {term for t in topics if t["event_ticker"] in (p["a"], p["b"])
             for term in t["terms"]})
        for t in topics:
            if t["event_ticker"] in (p["a"], p["b"]):
                mine = [dict(url_kept[u], subjects=pair_terms)
                        for u in p["urls"] if u in url_kept]
                for prop in skeptic.review(agent.graph_proposals(
                        {"event_ticker": t["event_ticker"],
                         "terms": t["terms"]}, mine))[0]:
                    all_props.append(prop)
        hid = f"hypothesis:loop-{_slug(p['a'])}-{_slug(p['b'])}"
        ev_arts = [url_kept[u] for u in p["urls"][:3] if u in url_kept]
        if deliberate and dtitle:
            title, conf = dtitle, dconf
        else:
            if inv_llm is not None:
                llm_calls += 1
            title, conf = hypothesis_title(
                p["a"], p["b"], p["urls"],
                [type("A", (), {"title": k["title"]}) for k in ev_arts],
                inv_llm)
        all_props.append(Proposal(
            agent="investigator", action="add-node", entity_id=hid,
            detail=json.dumps({"kind": "Hypothesis", "title": title,
                               "props": {"mode": p["mode"],
                                         "evidence": len(p["urls"])}}),
            confidence=conf))
        for end in (p["a"], p["b"]):
            all_props.append(Proposal(
                agent="investigator", action="add-edge",
                entity_id=hid,
                detail=json.dumps({"src": hid, "dst": f"kalshi:{end}",
                                   "rel": "supports"}),
                confidence=0.7))
        for u in p["urls"][:3]:
            all_props.append(Proposal(
                agent="investigator", action="add-edge",
                entity_id=hid,
                detail=json.dumps({"src": hid, "dst": article_id(u),
                                   "rel": "supports"}),
                confidence=0.6))
    return {"all_props": all_props, "pairs": live,
            "vetoed": vetoed, "llm_calls": llm_calls}


def stage_commit(ctx: dict) -> dict:
    """ctx: all_props, pairs, topics, graph_url, mode, cycle,
    llm_calls, vetoed. Posts, derives, fans out followups, and
    returns the cycle summary."""
    all_props, pairs = ctx["all_props"], ctx["pairs"]
    topics, mode, cycle = ctx["topics"], ctx["mode"], ctx.get("cycle", 0)
    graph_url = ctx["graph_url"]
    approved, _ = Skeptic().review(all_props)
    posted = _post_json(
        graph_url.rstrip("/") + "/proposals",
        {"proposals": [{"agent": p.agent, "action": p.action,
                        "entity_id": p.entity_id, "detail": p.detail,
                        "confidence": p.confidence} for p in approved]})
    posted_derive = _post_json(
        graph_url.rstrip("/") + "/proposals",
        {"proposals": [{"agent": "connector", "action": "derive-links",
                        "entity_id": "", "detail": "",
                        "confidence": 0.9}]})
    fresh = posted_derive.get("derived_fresh", 0)
    hid_to_pair = {
        f"hypothesis:loop-{_slug(p['a'])}-{_slug(p['b'])}": p for p in pairs}
    followups: list[dict] = []
    for v in posted.get("verdicts", []):
        # A freshly created Hypothesis is the new interconnect: the
        # other endpoint becomes a follow-up dig, same as Connector
        # dispatch. Re-created (duplicate) hypotheses fan out nothing.
        p = hid_to_pair.get(v.get("entity_id", ""))
        if p is not None and v.get("committed") and v.get("reason") == "created":
            for end in (p["a"], p["b"]):
                other = p["b"] if end == p["a"] else p["a"]
                followups.append({"type": "followup", "event_ticker": end,
                                  "terms": [t["title"] for t in topics
                                            if t["event_ticker"] == other][:1],
                                  "via": p["mode"]})
    _post_json(graph_url.rstrip("/") + "/findings",
               {"finding": {"loop": "news", "mode": mode, "cycle": cycle,
                            "pairs": len(pairs),
                            "revision": posted.get("revision"),
                            "derived_fresh": fresh,
                            "followups": followups,
                            "llm_calls": ctx.get("llm_calls", 0),
                            "vetoed": ctx.get("vetoed", 0)}})
    log(f"cycle={cycle} posted {len(approved)} proposals "
        f"rev={posted.get('revision')} derived_fresh={fresh} "
        f"followups={len(followups)} llm_calls={ctx.get('llm_calls', 0)} "
        f"vetoed={ctx.get('vetoed', 0)}")
    return {"ok": True, "mode": mode, "pairs": len(pairs),
            "proposals": len(approved),
            "revision": posted.get("revision"),
            "derived_fresh": fresh,
            "followups": len(followups),
            "followup_events": [
                {"event_ticker": f["event_ticker"], "via": f["via"]}
                for f in followups],
            "llm_calls": ctx.get("llm_calls", 0),
            "vetoed": ctx.get("vetoed", 0)}


def run_cycle(graph_url: str, kalshi_url: str, cycle: int = 0,
              event_slice: tuple = (), max_pairs: int = 10,
              mode_pin: str | None = None, model="auto",
              event_limit: int = 30, history_snaps: int = 1,
              history_hours: float = 48,
              history_stride_hours: float = 6,
              deliberate: bool = False,
              sync_topics: bool = True) -> dict:
    """One full explore -> propose -> commit cycle. Returns a summary.

    Reference dispatcher over STAGES (discover -> scan -> pair ->
    propose -> commit); an orchestrator may call run_stage directly.
    """
    ctx: dict = {
        "graph_url": graph_url, "kalshi_url": kalshi_url,
        "cycle": cycle, "event_slice": event_slice,
        "max_pairs": max_pairs, "mode": next_mode(cycle, mode_pin),
        "model": model, "event_limit": event_limit,
        "history_snaps": history_snaps, "history_hours": history_hours,
        "history_stride_hours": history_stride_hours,
        "deliberate": deliberate, "sync_topics": sync_topics,
    }
    try:
        run_stage("discover", ctx)
    except Exception as e:
        return {"ok": False, "reason": f"kalshi unreachable: {e}"}
    if len(ctx["topics"]) < 2:
        return {"ok": False,
                "reason": f"need 2+ topics, have {len(ctx['topics'])}"}
    try:
        run_stage("scan", ctx)
    except RuntimeError as e:
        return {"ok": False, "reason": str(e)}
    gated = run_stage("pair", ctx)
    if "done" in gated:
        return gated["done"]

    run_stage("propose", ctx)
    return run_stage("commit", ctx)


def _odds_cycle(graph_url: str, kalshi_url: str, topics: list[dict],
                max_pairs: int, model) -> dict:
    """Odds-correlation mode: co-moving markets nominate pairs for news."""
    closes: dict[str, list[float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for t in topics:
            markets = _kalshi_markets(kalshi_url, t["event_ticker"])
            live = [m for m in markets if m.get("ticker")]
            live.sort(key=lambda m: -(m.get("volume_24h") or 0))
            if live:
                futs[t["event_ticker"]] = ex.submit(
                    _kalshi_history, kalshi_url, live[0]["ticker"])
        for ticker, fut in futs.items():
            series = market_closes(fut.result())
            if series:
                closes[ticker] = series
    keys = sorted(closes)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            n = min(len(closes[a]), len(closes[b]))
            corr = pearson(closes[a][-n:], closes[b][-n:])
            if corr is not None and abs(corr) >= 0.7:
                pairs.append({"a": a, "b": b, "corr": round(corr, 3),
                              "mode": "odds-correlation", "urls": []})
    pairs.sort(key=lambda p: -abs(p["corr"]))
    pairs = _cap_pairs(pairs, max_pairs)
    if not pairs:
        return {"ok": True, "mode": "odds-correlation", "pairs": 0}
    all_props = []
    for p in pairs:
        hid = f"hypothesis:odds-{_slug(p['a'])}-{_slug(p['b'])}"
        all_props.append(Proposal(
            agent="investigator", action="add-node", entity_id=hid,
            detail=json.dumps(
                {"kind": "Hypothesis",
                 "title": f"odds co-move {p['corr']}: {p['a']} x {p['b']}",
                 "props": {"mode": "odds-correlation",
                           "corr": p["corr"]}}),
            confidence=0.7))
        for end in (p["a"], p["b"]):
            all_props.append(Proposal(
                agent="investigator", action="add-edge",
                entity_id=hid,
                detail=json.dumps({"src": hid, "dst": f"kalshi:{end}",
                                   "rel": "supports"}),
                confidence=0.7))
    approved, _ = Skeptic().review(all_props)
    posted = _post_json(
        graph_url.rstrip("/") + "/proposals",
        {"proposals": [{"agent": pr.agent, "action": pr.action,
                        "entity_id": pr.entity_id, "detail": pr.detail,
                        "confidence": pr.confidence} for pr in approved]})
    log("odds-correlation pairs=%d proposals=%d rev=%s llm_calls=0"
        % (len(pairs), len(approved), posted.get("revision")))
    _post_json(graph_url.rstrip("/") + "/findings",
               {"finding": {"loop": "news", "mode": "odds-correlation",
                            "pairs": len(pairs),
                            "revision": posted.get("revision"),
                            "llm_calls": 0}})
    return {"ok": True, "mode": "odds-correlation", "pairs": len(pairs),
            "proposals": len(approved), "revision": posted.get("revision"),
            "llm_calls": 0}


@click.command()
@click.option("--graph-url", default="http://127.0.0.1:8090")
@click.option("--kalshi-url", default="http://127.0.0.1:8080")
@click.option("--interval-min", type=float, default=15)
@click.option("--event", multiple=True, default=(),
              help="restrict to these event tickers (repeatable)")
@click.option("--max-pairs", type=int, default=10,
              help="pairs per cycle, 0 = no cap")
@click.option("--mode", type=click.Choice(list(MODES)), default=None,
              help="pin one exploration mode (default: rotate)")
@click.option("--model", default="auto",
              help="LLM model spec (auto|None|model-id); None = extractive")
@click.option("--once", is_flag=True, default=False)
@click.option("--loop", is_flag=True, default=False)
@click.option("--max-cycles", type=int, default=0,
              help="stop after N loop cycles (0 = run forever)")
@click.option("--event-limit", type=int, default=30,
              help="Kalshi events to explore per cycle")
@click.option("--history-snaps", type=int, default=1,
              help="snapshots per cycle, stride-spaced (1 = newest only)")
@click.option("--history-hours", type=float, default=48,
              help="how far back the history dig walks")
@click.option("--history-stride-hours", type=float, default=6,
              help="spacing between history snapshots (skips the rest)")
@click.option("--langsmith-project", default=None)
@click.option("--deliberate", is_flag=True, default=False,
              help="multi-turn propose->audit->revise per pair "
              "(experiment, opt-in only: measured 2026-10-31, no "
              "gain over single-shot on 11 fixtures, ~45% more "
              "calls; kept for future harder-fixture tests)")
@click.option("--sync-topics/--no-sync-topics", default=True,
              help="push discovered events to the kalshi service "
              "watchlist each cycle (fail-open)")
@click.option("--auto-next/--no-auto-next", default=True,
              help="carry next steps between cycles (follow-up digs, "
              "challenge re-scans, deep rounds after quiet streaks); "
              "explicit --mode/--event always win")
def main(graph_url, kalshi_url, interval_min, event, max_pairs, mode,
         model, once, loop, max_cycles, event_limit, history_snaps,
         history_hours, history_stride_hours,
         langsmith_project, deliberate, sync_topics,
         auto_next) -> None:
    from services.kalshi_auth import load_dotenv  # noqa: E402
    from squad.next_steps import (  # noqa: E402
        apply_next_steps, compute_next_steps)
    from squad.tracing import setup_langsmith, squad_span  # noqa: E402

    load_dotenv()
    ls = setup_langsmith(project=langsmith_project)
    log(f"langsmith={'on' if ls['enabled'] else 'off'} "
        f"project={ls['project']}")
    if model == "None":
        model = None
    cli_depth = {"history_snaps": history_snaps,
                 "history_hours": history_hours,
                 "history_stride_hours": history_stride_hours}
    recent: list = []
    cycle = 0
    while True:
        if auto_next:
            nxt = apply_next_steps(
                mode, tuple(event), cli_depth,
                compute_next_steps(recent))
            log(f"cycle={cycle} next: mode_pin={nxt['mode_pin']} "
                f"events={list(nxt['event_slice']) or 'all'} "
                f"snaps={nxt['history_snaps']} "
                f"({'; '.join(nxt['reasons'])})")
        else:
            nxt = {"mode_pin": mode, "event_slice": tuple(event),
                   "reasons": ["auto-next off"], **cli_depth}
        with squad_span("news-loop-cycle",
                        {"cycle": cycle, "mode": mode or "rotate"}) as span:
            try:
                summary = run_cycle(graph_url, kalshi_url, cycle,
                                    nxt["event_slice"], max_pairs,
                                    nxt["mode_pin"], model,
                                    event_limit, nxt["history_snaps"],
                                    nxt["history_hours"],
                                    nxt["history_stride_hours"], deliberate,
                                    sync_topics)
            except Exception as e:  # keep the loop alive; record and retry
                summary = {"ok": False,
                           "reason": f"{type(e).__name__}: {e}"}
            span.outputs = summary
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        print(f"[news-loop] {stamp} cycle={cycle} {summary}", flush=True)
        recent = (recent + [summary])[-5:]
        if not loop:
            sys.exit(0 if summary.get("ok") else 1)
        cycle += 1
        if max_cycles and cycle >= max_cycles:
            sys.exit(0)
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
