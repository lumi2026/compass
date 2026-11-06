"""Cross-topic link test: N unrelated Kalshi topics, deep snapshot walk.

Scans up to --lookback snapshots ONCE for the union of all topics'
terms (one pass per snapshot, file deleted after scanning so disk
stays bounded), then builds one shared graph: per-topic Scout +
news digraph, plus a Hypothesis for every topic pair sharing an
article URL (the article is the co-mention evidence).

Run:
  uv run python scripts/cross_link.py --events 30 --lookback 100 \\
      --out demo_local/cross_graph.json
"""

import json
import os
import re
import sys
import time

import click

sys.path.insert(0, ".")

from graph import GraphStore, GraphUpdateAPI, article_id, proposals_to_commit  # noqa: E402
from squad import Proposal, Skeptic  # noqa: E402
from squad.connector import Connector  # noqa: E402
from squad.investigator import Investigator  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402
from squad.pipeline import build_investigator_context  # noqa: E402
from squad.scout import Scout  # noqa: E402
from graph import to_cytoscape  # noqa: E402

STOP = set("""will the a an of to in on for who what when where be is are was
were by before after than more less most its it this that with from and or as
at end over under out up how many much his her their he she they we you have
has had do does did going get win take say says said new next first last high
low top no not vs via per day week month year time times will be des les der
die und""".split())


# Generic-token language lives in graph.store (next to subject_id) so
# the squad can fold subjects without importing scripts/.
from graph import (  # noqa: E402
    GENERIC_TOKENS as GENERIC_TOKENS,
    anchored_terms as anchored_terms,
    is_generic_term as is_generic_term,
)


def extract_terms(title: str, max_terms: int = 5,
                  max_unigrams: int = 4) -> list[str]:
    """Title -> search terms: ordered bigrams, then proper-noun unigrams.

    Bigrams lead (the old first-N contract, widened): multi-word phrases
    are what the NGrams 3.0 pre/post rules match best. Proper-noun
    unigrams follow so the country/office-holder token survives long
    titles whose tail used to be cut ("2030 Brazilian presidential
    election" lost "brazilian" past the old cap): a token qualifies when
    capitalized in the title (short ALLCAPS acronyms like GDP count),
    significant, non-numeric, and non-generic. Single-token titles keep
    the old longest-token fallback.
    """
    toks = [w for w in re.sub(r"[^a-z0-9 ]", " ", title.lower()).split()
            if w not in STOP and len(w) > 2]
    bigrams = [f"{a} {b}" for a, b in zip(toks, toks[1:])][:max_terms]
    if not bigrams and toks:
        longest = max(toks, key=len)
        if len(longest) >= 4:
            return [longest]
        return []
    proper = set()
    for w in re.findall(r"[A-Za-z0-9]+", title):
        low = w.lower()
        if not w[0].isupper() or low in STOP or len(low) <= 2:
            continue
        if low.isdigit() or low in GENERIC_TOKENS:
            continue
        if w.isupper() and len(w) > 5:
            continue  # long ALLCAPS is emphasis, not an acronym
        proper.add(low)
    uni, seen = [], set()
    for t in toks:
        if t in proper and t not in seen:
            seen.add(t)
            uni.append(t)
            if len(uni) >= max_unigrams:
                break
    return bigrams + uni


def shared_pairs(topic_urls: dict[str, list[str]]) -> dict[tuple, list[str]]:
    """URL sets per topic -> {(topic_a, topic_b): [shared urls]}."""
    by_url: dict[str, set[str]] = {}
    for topic, urls in topic_urls.items():
        for u in urls:
            by_url.setdefault(u, set()).add(topic)
    pairs: dict[tuple, list[str]] = {}
    for url, topics in by_url.items():
        ts = sorted(topics)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                pairs.setdefault((a, b), []).append(url)
    return pairs


def pick_events(limit: int) -> list[dict]:
    """Live Kalshi events, greedily diverse by first bigram."""
    from sources.kalshi import KalshiSource

    seen, out = set(), []
    for ev in KalshiSource().fetch_normalized(limit=limit * 2):
        terms = extract_terms(ev.title)
        if not terms or terms[0] in seen:
            continue
        seen.add(terms[0])
        out.append({"event_ticker": ev.external_id, "title": ev.title,
                    "terms": terms})
        if len(out) >= limit:
            break
    return out


def scan_union(tasks: list[dict], max_snapshots: int,
               dest_dir: str = "/tmp") -> tuple[dict, int]:
    """One pass per snapshot over the union of all terms.

    Downloads each snapshot, scans, deletes it (unless it was already
    cached) so disk stays bounded. Returns (per-term aggregates,
    snapshots_scanned).
    """
    from sources.ngrams import download_snapshot, ngram_mentions, snapshot_candidates

    union = sorted({t for task in tasks for t in task["terms"]})
    counts: dict[str, int] = {}
    urls: dict[str, set] = {}
    samples: dict[str, list] = {}
    scanned = 0
    cands = snapshot_candidates(time.time(), lookback_min=max_snapshots * 15 + 120)
    for n, ts in enumerate(cands):
        if scanned >= max_snapshots:
            break
        dest = os.path.join(dest_dir, f"ngram_{ts}.json.gz")
        downloaded = False
        if not os.path.exists(dest):
            try:
                download_snapshot(ts, dest)
                downloaded = True
            except Exception:
                continue
        try:
            m = ngram_mentions(union, dest, per_term_cap=30)
        except Exception:
            continue
        finally:
            if downloaded:
                try:
                    os.remove(dest)
                except OSError:
                    pass
        scanned += 1
        for t, mm in m.items():
            counts[t] = counts.get(t, 0) + mm["count"]
            urls.setdefault(t, set()).update(mm["urls"])
            have = samples.setdefault(t, [])
            for s in mm["samples"]:
                if len(have) >= 60:
                    break
                s = dict(s)
                s["_snap"] = ts
                have.append(s)
        if (n + 1) % 10 == 0 or scanned == max_snapshots:
            hit_terms = sum(1 for t in union if counts.get(t))
            print(f"[xlink] snap {scanned}/{max_snapshots} "
                  f"hit_terms={hit_terms}/{len(union)}", flush=True)
    return ({"counts": counts, "urls": urls, "samples": samples}, scanned)


@click.command()
@click.option("--events", type=int, default=30)
@click.option("--lookback", type=int, default=100)
@click.option("--seed", default="data_local/seed.json")
@click.option("--out", default="demo_local/cross_graph.json")
@click.option("--max-articles", type=int, default=3)
def main(events, lookback, seed, out, max_articles) -> None:
    tasks = pick_events(events)
    print(f"[xlink] topics={len(tasks)}")
    for t in tasks:
        print(f"  {t['event_ticker']}: {t['terms']}")
    if not tasks:
        print("[xlink] FAIL: no live events")
        sys.exit(1)

    agg, scanned = scan_union(tasks, lookback)
    print(f"[xlink] scanned={scanned} "
          f"total_hits={sum(agg['counts'].values())}")

    api = GraphUpdateAPI(GraphStore())
    skeptic = Skeptic()

    def markets_of(event: str) -> list:
        try:
            from sources.kalshi import KalshiSource
            return KalshiSource().fetch_markets(event)
        except Exception:
            return []

    topic_urls: dict[str, list[str]] = {}
    for task in tasks:
        ev = task["event_ticker"]
        markets = markets_of(ev)
        orch_tasks = {"markets": markets}
        from squad import Orchestrator
        orch = Orchestrator(agents=[Scout(markets_fn=lambda e, m=markets: m)],
                            skeptic=skeptic)
        for prop in orch.investigate(task, orch_tasks):
            proposals_to_commit(api, prop.agent, prop.action,
                                prop.entity_id, prop.detail)
        # kept articles: deduped urls across this topic's terms, newest first
        seen, kept = set(), []
        for term in task["terms"]:
            for s in agg["samples"].get(term, []):
                url = s.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                kept.append({"url": url,
                             "title": f"...{s.get('pre','')} "
                                      f"[{s.get('ngram','')}] "
                                      f"{s.get('post','')}..."[:160],
                             "subjects": list(task["terms"]),
                             "snapshot": s.get("_snap", ""),
                             "seendate": s.get("date", "") or s.get("_snap", "")})
                if len(kept) >= max_articles * 5:
                    break
            if len(kept) >= max_articles * 5:
                break
        topic_urls[ev] = [k["url"] for k in kept]
        agent = NewsAgent()
        for prop in skeptic.review(agent.graph_proposals(task, kept))[0]:
            proposals_to_commit(api, prop.agent, prop.action,
                                prop.entity_id, prop.detail)
        ictx = build_investigator_context(kept=kept, markets=markets)
        for prop in skeptic.review(Investigator().run(task, ictx))[0]:
            proposals_to_commit(api, prop.agent, prop.action,
                                prop.entity_id, prop.detail)

    # cross-topic hypotheses on shared article URLs
    pairs = shared_pairs(topic_urls)
    print(f"[xlink] shared-url pairs={len(pairs)}")
    url_title = {}
    for task in tasks:
        for term in task["terms"]:
            for s in agg["samples"].get(term, []):
                url_title.setdefault(s.get("url", ""), s.get("ngram", ""))
    top = sorted(pairs.items(), key=lambda kv: -len(kv[1]))[:20]
    for (a, b), urls in top:
        slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30]
        hid = f"hypothesis:xlink-{slug(a)}-{slug(b)}"
        detail = (f"cross-topic co-mention: {a} x {b} share "
                  f"{len(urls)} article(s)")
        for prop in skeptic.review([
                Proposal(
                    agent="investigator", action="add-node", entity_id=hid,
                    detail=json.dumps({"kind": "Hypothesis", "title": detail}),
                    confidence=min(0.9, 0.5 + 0.1 * len(urls))),
                Proposal(
                    agent="investigator", action="add-edge", entity_id=hid,
                    detail=json.dumps({"src": hid, "dst": f"kalshi:{a}",
                                       "rel": "asserts",
                                       "props": {"weight": len(urls),
                                                 "confidence": 0.7}}),
                    confidence=0.7),
                Proposal(
                    agent="investigator", action="add-edge", entity_id=hid,
                    detail=json.dumps({"src": hid, "dst": f"kalshi:{b}",
                                       "rel": "asserts",
                                       "props": {"weight": len(urls),
                                                 "confidence": 0.7}}),
                    confidence=0.7),
        ] + [Proposal(
                agent="investigator", action="add-edge", entity_id=hid,
                detail=json.dumps({"src": hid, "dst": article_id(u),
                                   "rel": "cites",
                                   "props": {"weight": 1,
                                             "confidence": 0.6}}),
                confidence=0.6)
             for u in urls[:5]])[0]:
            print(proposals_to_commit(api, prop.agent, prop.action,
                                      prop.entity_id, prop.detail).reason)

    (intent,) = Connector().run(tasks[0])
    print(proposals_to_commit(api, intent.agent, intent.action,
                              intent.entity_id, intent.detail).reason)

    payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges,
                            limit=None)  # full graph: links are the point
    api.save(out)
    print(f"[xlink] topics={len(tasks)} snapshots={scanned} "
          f"nodes={len(api.store.nodes)} edges={len(api.store.edges)} "
          f"elements={len(payload['elements'])}")
    print(f"[xlink] graph -> {out}")
    print("[xlink] OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
