"""Single-agent prototype: explain a Kalshi topic with GDELT news.

Pipeline: Kalshi event markets -> n-gram term search over a webngrams
snapshot -> fetch original articles -> extractive summary -> Proposal.

Tools are plain stdlib functions so the squad (or a script) can call them
individually. NewsAgent chains them per the squad Agent protocol.
"""

import gzip
import html.parser
import io
import json
import re
import string
import urllib.request
from dataclasses import dataclass
from typing import Any

from sources.gdelt import GdeltSource  # noqa: F401  (doc reference)
from sources.kalshi import KalshiSource
from squad import Proposal
from squad.models import make_chat_model

SNAP_BASE = "https://data.gdeltproject.org/gdeltv3/webngrams"
SNAP_MARKS = ("01", "16", "31", "46")
UA = {"User-Agent": "compass-hackathon/0.1"}
STRIP = str.maketrans("", "", string.punctuation)


class _TextExtract(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


# -- tools -----------------------------------------------------------------

def kalshi_markets(event_ticker: str, limit: int = 10) -> list:
    """Tool: live markets (with prices) for a Kalshi event."""
    return KalshiSource().fetch_markets(event_ticker, limit=limit)


def snapshot_candidates(now_ts: float, lookback_min: int = 120) -> list[str]:
    """Tool: newest-first snapshot timestamps (YYYYMMDDHHMMSS)."""
    import datetime

    now = datetime.datetime.fromtimestamp(
        now_ts, tz=datetime.timezone.utc
    ).replace(second=0, microsecond=0)
    out, seen = [], set()
    t = now - datetime.timedelta(minutes=5)
    while len(out) < lookback_min // 15 + 1:
        mm = t.minute
        mark = "46" if mm >= 46 else "31" if mm >= 31 else "16" if mm >= 16 else "01"
        ts = t.strftime("%Y%m%d%H") + mark + "00"
        if ts not in seen:
            seen.add(ts)
            out.append(ts)
        t -= datetime.timedelta(minutes=15)
    return out


def download_snapshot(ts: str, dest: str) -> str:
    """Tool: fetch one webngrams snapshot file to dest. Returns dest."""
    req = urllib.request.Request(f"{SNAP_BASE}/{ts}.webngrams.json.gz", headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def _words(s: str) -> list[str]:
    return [w.translate(STRIP).lower() for w in s.split() if w.translate(STRIP)]


def match_record(r: dict, phrase: list[str]) -> bool:
    """Multi-gram match per the NGrams 3.0 doc: the middle word of the
    phrase is the ngram, words before it are the tail of pre, words
    after it are the head of post. Unigram = exact ngram match."""
    if r.get("type") != 1 or not phrase:
        return False
    mid = (len(phrase) - 1) // 2  # bigram: ngram is the FIRST word (doc rule)
    if r.get("ngram", "").translate(STRIP).lower() != phrase[mid]:
        return False
    pre, post = _words(r.get("pre", "")), _words(r.get("post", ""))
    before, after = phrase[:mid], phrase[mid + 1 :]
    return pre[len(pre) - len(before):] == before and post[: len(after)] == after


def ngram_mentions(
    terms: list[str],
    snapshot_gz: str,
    lang: str = "en",
    per_term_cap: int = 5,
) -> dict:
    """Tool: count + sample uni/multi-gram hits per term in a snapshot file.

    Streams the gzip (never loads all rows). Phrases use pre/post rules.
    Returns {term: {"count": int, "urls": [...], "samples": [records]}}.
    """
    want = {t.lower(): t.lower().split() for t in terms}
    counts: dict[str, int] = {t: 0 for t in want}
    urls: dict[str, set] = {t: set() for t in want}
    samples: dict[str, list] = {t: [] for t in want}
    with gzip.open(snapshot_gz, "rt") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("lang") != lang:
                continue
            for t, phrase in want.items():
                if match_record(r, phrase):
                    counts[t] += 1
                    urls[t].add(r.get("url", ""))
                    if len(samples[t]) < per_term_cap:
                        samples[t].append(r)
    return {
        t: {"count": counts[t], "urls": sorted(urls[t]), "samples": samples[t]}
        for t in want
    }


def fetch_article(url: str, timeout: int = 20, max_chars: int = 8000) -> str:
    """Tool: fetch an article URL, return visible text (tags stripped)."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(2_000_000).decode("utf-8", "ignore")
    doc = _TextExtract()
    doc.feed(raw)
    text = re.sub(r"\s+", " ", "".join(doc.parts)).strip()
    return text[:max_chars]


def summarize(text: str, terms: list[str], max_sentences: int = 3) -> str:
    """Tool: extractive summary — top sentences by term overlap + position."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 40]
    want = {t.lower() for t in terms}
    scored = []
    for i, s in enumerate(sents):
        low = s.lower()
        hits = sum(low.count(t) for t in want)
        if hits:
            scored.append((hits * 2 - i / max(len(sents), 1), i, s))
    scored.sort(reverse=True)
    return " ".join(s for _, _, s in scored[:max_sentences])


def llm_summarize(text: str, terms: list[str], model) -> str:
    """Tool: LLM summary via a LangChain chat model. Raises on failure
    so the caller can fall back to extractive summarize()."""
    from langchain_core.messages import HumanMessage, SystemMessage

    msg = model.invoke(
        [
            SystemMessage(
                "You summarize news for a prediction-market analyst. "
                "Reply with at most 3 sentences, only what is relevant to "
                f"these topics: {', '.join(terms)}. No preamble, no URLs."
            ),
            HumanMessage(text[:6000]),
        ]
    )
    return str(msg.content).strip()


# -- agent -----------------------------------------------------------------

@dataclass
class NewsAgent:
    """Single-agent prototype: news summary for one Kalshi topic."""

    name: str = "news-agent"
    snapshot_gz: str | None = None
    max_articles: int = 3
    model: Any = "auto"  # "auto" -> make_chat_model(); None -> extractive only

    def run(self, task: dict, context: dict | None = None) -> list[Proposal]:
        ticker = task["event_ticker"]
        terms = task["terms"]
        model = make_chat_model() if self.model == "auto" else self.model
        markets = kalshi_markets(ticker)
        lead = markets[0] if markets else None
        price = f"{lead.last_price}c" if lead and lead.last_price is not None else "?"

        mentions = ngram_mentions(terms, self.snapshot_gz or "", per_term_cap=5)
        total = sum(m["count"] for m in mentions.values())

        # Prefer lead-paragraph hits (pos <= 20), dedupe urls.
        cand: list[str] = []
        for m in mentions.values():
            for s in sorted(m["samples"], key=lambda r: r.get("pos", 99)):
                if s.get("url") and s["url"] not in cand:
                    cand.append(s["url"])
        cand = cand[: self.max_articles]

        lines = [
            f"markets={len(markets)}",
            f"ngram_hits_15min={total}",
            f"lead={lead.title if lead else '?'} @ {price}",
        ]
        ok = 0
        llm_ok = 0
        for url in cand:
            try:
                text = fetch_article(url)
            except Exception:
                continue
            summ, via = "", "extractive"
            if model is not None:
                try:
                    summ = llm_summarize(text, terms, model)
                    if summ:
                        via, llm_ok = "llm", llm_ok + 1
                except Exception:
                    summ, via = "", "extractive"
            if not summ:
                summ = summarize(text, terms)
            if not summ:
                continue
            ok += 1
            lines.append(f"- [{via}] {summ} [{url}]")
        if not ok:
            lines.append("(no articles fetchable; n-gram counts only)")
        lines.append(f"engine={'llm' if llm_ok else 'extractive'}")
        conf = min(0.9, 0.3 + 0.15 * ok + (0.1 if total > 5 else 0.0))
        return [
            Proposal(
                agent=self.name,
                action="news_summary",
                entity_id=f"kalshi:{ticker}",
                detail="\n".join(lines),
                confidence=round(conf, 2),
            )
        ]