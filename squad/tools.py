"""Shared agent toolbox: article fetch + text/LLM summary + relevance.

Generic building blocks every squad agent can use (news, investigator,
graph). No source-specific logic here: callers supply text and key
subjects. LLM tools take any LangChain chat model and raise on failure
so callers can fall back to the extractive path.
"""

import html.parser
import re
import urllib.request

UA = {"User-Agent": "compass-hackathon/0.1"}


class _TextExtract(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = True

    def handle_endtag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


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


def llm_relevant(text: str, terms: list[str], model,
                 question: str | None = None) -> tuple[bool, str]:
    """Tool: LLM relevance filter. Returns (keep, reason).

    Fail-open: any reply that is not an explicit NO keeps the article,
    so unparseable model output never blanks the pipeline. Relevance is
    subject-level: an article substantively about a key subject counts,
    even when it does not directly answer the market question."""
    from langchain_core.messages import HumanMessage, SystemMessage

    scope = (f"The analyst's question is: {question}. "
             f"Key subjects: {', '.join(terms)}. "
             if question else
             f"Key subjects: {', '.join(terms)}. ")
    msg = model.invoke(
        [
            SystemMessage(
                "You filter news for a prediction-market analyst. " + scope +
                "Reply with YES or NO first, then a one-line reason. "
                "YES = the article is substantively about one or more of "
                "the key subjects; it need not answer the question "
                "directly. "
                "NO = no key subject covered in substance, passing "
                "mention only, gossip, or boilerplate."
            ),
            HumanMessage(text[:4000]),
        ]
    )
    raw = str(msg.content).strip()
    first = raw.split(None, 1)[0].upper().rstrip(":,.") if raw.split() else ""
    if first == "NO":
        return False, raw
    return True, raw
