"""The hypothesis taxonomy is defined once and shared with the view."""

import json
from pathlib import Path

import squad.auditor as auditor
import squad.investigator as investigator
import squad.taxonomy as taxonomy

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared" / "hypothesis_taxonomy.json"


def test_shared_file_is_the_single_source():
    data = json.loads(SHARED.read_text(encoding="utf-8"))
    assert list(taxonomy.HYPOTHESIS_TYPES) == data["types"]
    assert list(taxonomy.DETECTION_MODES) == data["detection_modes"]
    assert taxonomy.LEGACY_MODE_TYPE == data["mode_to_type"]
    assert taxonomy.MODE_HYPOTHESIS_TYPE == {
        m: data["mode_to_type"][m] for m in data["detection_modes"]}


def test_squad_modules_use_the_shared_taxonomy():
    assert investigator.HYPOTHESIS_TYPES == taxonomy.HYPOTHESIS_TYPES
    assert investigator.LEGACY_MODE_TYPE == taxonomy.LEGACY_MODE_TYPE
    assert auditor.normalize_hypothesis_type is \
        taxonomy.normalize_hypothesis_type


def test_news_loop_uses_the_shared_taxonomy():
    import scripts.news_loop as nl

    assert nl.HYPOTHESIS_TYPES == taxonomy.HYPOTHESIS_TYPES
    assert nl.MODE_HYPOTHESIS_TYPE == taxonomy.MODE_HYPOTHESIS_TYPE
    assert nl.MODES == taxonomy.DETECTION_MODES


def test_normalize_maps_types_and_modes():
    assert taxonomy.normalize_hypothesis_type("co_mention") == "co_mention"
    assert taxonomy.normalize_hypothesis_type("co-location") == "co_location"
    assert taxonomy.normalize_hypothesis_type("transitive") == "shared_context"
    assert taxonomy.normalize_hypothesis_type("re-audit") is None
    assert taxonomy.normalize_hypothesis_type("") is None
    assert taxonomy.normalize_hypothesis_type(None) is None


def test_every_mode_targets_a_known_type():
    assert set(taxonomy.MODE_HYPOTHESIS_TYPE) == set(taxonomy.MODES)
    assert set(taxonomy.LEGACY_MODE_TYPE.values()) <= set(
        taxonomy.HYPOTHESIS_TYPES)
