"""Cross-link helpers: term extraction + shared-URL pairing (offline)."""

import sys

sys.path.insert(0, ".")

from scripts.cross_link import (  # noqa: E402
    anchored_terms,
    extract_terms,
    is_generic_term,
    shared_pairs,
)


def test_extract_terms_prefers_bigrams():
    # bigrams lead in order, then proper-noun unigrams as backstops
    assert extract_terms("Will Kevin Warsh be out as chairman?") == [
        "kevin warsh", "warsh chairman", "kevin", "warsh",
    ]


def test_extract_terms_falls_back_to_long_unigram():
    assert extract_terms("Bitcoin") == ["bitcoin"]
    assert extract_terms("Will it?") == []


def test_extract_terms_keeps_country_anchor_past_bigram_cap():
    terms = extract_terms(
        "Who will win the 2030 Brazilian presidential election?")
    assert "brazilian" in terms  # the tail bigram is cut, the unigram stays
    assert "presidential" not in terms and "election" not in terms


def test_extract_terms_generic_title_has_no_unigrams():
    assert extract_terms("Presidential Election") == ["presidential election"]


def test_is_generic_term_needs_every_token_generic():
    assert is_generic_term("presidential election")
    assert is_generic_term("senate")
    assert not is_generic_term("brazilian presidential")
    assert not is_generic_term("brazil")


def test_anchored_terms_drops_genre_phrases():
    assert anchored_terms(
        ["presidential election", "brazilian presidential", "brazilian"]
    ) == ["brazilian presidential", "brazilian"]


def test_shared_pairs_finds_cross_topic_urls():
    got = shared_pairs({
        "A": ["u1", "u2"],
        "B": ["u2", "u3"],
        "C": ["u9"],
    })
    assert got == {("A", "B"): ["u2"]}
