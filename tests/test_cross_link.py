"""Cross-link helpers: term extraction + shared-URL pairing (offline)."""

import sys

sys.path.insert(0, ".")

from scripts.cross_link import extract_terms, shared_pairs  # noqa: E402


def test_extract_terms_prefers_bigrams():
    assert extract_terms("Will Kevin Warsh be out as chairman?") == [
        "kevin warsh", "warsh chairman",
    ]


def test_extract_terms_falls_back_to_long_unigram():
    assert extract_terms("Bitcoin") == ["bitcoin"]
    assert extract_terms("Will it?") == []


def test_shared_pairs_finds_cross_topic_urls():
    got = shared_pairs({
        "A": ["u1", "u2"],
        "B": ["u2", "u3"],
        "C": ["u9"],
    })
    assert got == {("A", "B"): ["u2"]}
