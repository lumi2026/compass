"""Background news watcher: poll GDELT topics, save theme digest for agents.

Run:  python3 scripts/collect_news.py --once [--topic Q ...] [--limit 10]
      python3 scripts/collect_news.py --loop [--interval-min 15]

Writes data_local/news_digest.json (atomic): per-topic articles + extractive
summary (counts, freshness lag, top domains, title keywords, headlines).
Agents investigating Kalshi price moves read the digest as background info.

Default source is NGrams 3.0 snapshots (fresh 15-min static host,
unthrottled). DOC 2.0 (`--source doc`) is the fallback; its index was
observed ~19h stale and it throttles to ~1 req / 5s.

Stdlib only. DOC requests are spaced by --gap-sec (GDELT throttles hard).
"""

import collections
import datetime
import json
import os
import sys
import tempfile
import time

import click

sys.path.insert(0, ".")

from sources.gdelt import GdeltSource  # noqa: E402
from sources.ngrams import (  # noqa: E402
    articles_from_mentions, ngram_mentions, resolve_snapshot,
)

DEFAULT_TOPICS = {
    "mamdani-nyc-mayor": '(Mamdani OR "New York mayor") sourcecountry:US',
    "fed-rates": '("Federal Reserve" OR "interest rates" OR "rate cut")',
    "trump-white-house": '(Trump OR "White House") sourcecountry:US',
    "us-economy": '(inflation OR "jobs report" OR unemployment) sourcecountry:US',
}

DEFAULT_NGRAM_TERMS = {
    "mamdani-nyc-mayor": ["zohran mamdani", "new york mayor", "mamdani"],
    "fed-rates": ["federal reserve", "interest rates", "rate cut"],
    "trump-white-house": ["donald trump", "white house"],
    "us-economy": ["inflation", "jobs report", "unemployment"],
}

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how in is it its of on or
    says say said says new over under with will would could after before amid
    among into out than then there their they them this that these those what
    when where which while who why vs via per amid among""".split()
)


def summarize_topic(name, query, articles, now=None):
    """Pure extractive summary over normalized NewsArticles. No network."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    arts = [
        {
            "title": a.title,
            "url": a.url,
            "seendate": a.published_at,
            "domain": (a.raw or {}).get("domain", ""),
        }
        for a in articles
    ]
    words = collections.Counter()
    for a in arts:
        for w in a["title"].lower().split():
            w = "".join(c for c in w if c.isalnum())
            if len(w) >= 4 and w not in STOPWORDS:
                words[w] += 1
    stamps = sorted(a["seendate"] for a in arts if a["seendate"])
    lag_min = None
    if stamps:
        try:
            newest = datetime.datetime.strptime(
                stamps[-1], "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
            lag_min = round((now - newest).total_seconds() / 60)
        except ValueError:
            lag_min = None
    return {
        "name": name,
        "query": query,
        "count": len(arts),
        "oldest_seendate": stamps[0] if stamps else None,
        "newest_seendate": stamps[-1] if stamps else None,
        "lag_minutes": lag_min,
        "top_domains": collections.Counter(
            a["domain"] for a in arts if a["domain"]
        ).most_common(5),
        "keywords": [w for w, _ in words.most_common(10)],
        "headlines": arts[:5],
        "articles": arts,
    }


def _write_digest(digest, out):
    with tempfile.NamedTemporaryFile(
        "w", dir=os.path.dirname(out) or ".", delete=False
    ) as f:
        json.dump(digest, f, indent=1)
        tmp = f.name
    os.replace(tmp, out)
    print(f"[news] wrote {out}")


def collect_once_ngrams(topics_terms, limit, out, snapshot=None):
    now = datetime.datetime.now(datetime.timezone.utc)
    digest = {
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "ngrams",
        "topics": {},
        "errors": {},
    }
    try:
        if snapshot:
            ts = os.path.basename(snapshot).removeprefix(
                "ngram_").removesuffix(".json.gz")
            snap = snapshot
        else:
            ts, snap = resolve_snapshot()
        print(f"[news] snapshot {ts}", flush=True)
    except Exception as e:
        digest["errors"]["snapshot"] = f"{type(e).__name__}: {e}"
        _write_digest(digest, out)
        return 1
    for name, terms in topics_terms.items():
        print(f"[news] {name}: {terms} ...", flush=True)
        try:
            mentions = ngram_mentions(terms, snap, per_term_cap=limit)
            total = sum(m["count"] for m in mentions.values())
            arts = articles_from_mentions(mentions, ts)
            digest["topics"][name] = summarize_topic(
                name, ",".join(terms), arts, now=now)
            digest["topics"][name]["ngram_hits"] = total
            digest["topics"][name]["snapshot"] = ts
            print(f"[news] {name}: {total} hits, {len(arts)} urls",
                  flush=True)
        except Exception as e:  # keep other topics alive; record failure
            digest["errors"][name] = f"{type(e).__name__}: {e}"
            print(f"[news] {name}: FAIL {e}", flush=True)
    _write_digest(digest, out)
    return 0 if digest["topics"] else 1


def collect_once(topics, timespan, limit, gap_sec, out):
    now = datetime.datetime.now(datetime.timezone.utc)
    digest = {
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "doc",
        "timespan": timespan,
        "topics": {},
        "errors": {},
    }
    names = list(topics)
    for i, name in enumerate(names):
        if i and gap_sec > 0:
            time.sleep(gap_sec)
        query = topics[name]
        print(f"[news] {name}: {query} ...", flush=True)
        try:
            arts = GdeltSource(
                query=query, timespan=timespan, sort="datedesc"
            ).fetch_normalized(limit=limit)
            digest["topics"][name] = summarize_topic(name, query, arts, now=now)
            print(f"[news] {name}: {len(arts)} articles", flush=True)
        except Exception as e:  # keep other topics alive; record failure
            digest["errors"][name] = f"{type(e).__name__}: {e}"
            print(f"[news] {name}: FAIL {e}", flush=True)
    _write_digest(digest, out)
    return 0 if digest["topics"] else 1


@click.command()
@click.option("--source", type=click.Choice(["ngrams", "doc"]),
              default="ngrams", help="news source (default: ngrams)")
@click.option("--topic", multiple=True, default=(),
              help="doc query string (or comma-separated ngram terms "
              "with --source ngrams); repeatable")
@click.option("--name", multiple=True, default=(),
              help="names for --topic values, in order")
@click.option("--timespan", default="1days",
              help="doc only: GDELT timespan window")
@click.option("--limit", type=int, default=10)
@click.option("--gap-sec", type=float, default=240,
              help="doc only: spacing between topic requests")
@click.option("--snapshot", default=None,
              help="ngrams only: pin a snapshot .json.gz path")
@click.option("--out", default="data_local/news_digest.json")
@click.option("--once", is_flag=True, default=False,
              help="single round (default; --loop repeats)")
@click.option("--loop", is_flag=True, default=False)
@click.option("--interval-min", type=float, default=15)
def main(source, topic, name, timespan, limit, gap_sec, snapshot, out,
         once, loop, interval_min) -> None:
    topic = list(topic)
    name = list(name)
    if source == "ngrams":
        if topic:
            names = name or [f"topic-{i}" for i in range(len(topic))]
            topics_terms = {n: [w.strip() for w in q.split(",") if w.strip()]
                            for n, q in zip(names, topic)}
        else:
            topics_terms = dict(DEFAULT_NGRAM_TERMS)
        run = lambda: collect_once_ngrams(topics_terms, limit, out, snapshot)
    else:
        if topic:
            names = name or [f"topic-{i}" for i in range(len(topic))]
            topics = dict(zip(names, topic))
        else:
            topics = dict(DEFAULT_TOPICS)
        run = lambda: collect_once(topics, timespan, limit, gap_sec, out)

    if not loop:
        sys.exit(run())
    while True:
        run()
        print(f"[news] sleeping {interval_min} min ...", flush=True)
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    main()
