"""Single source of truth for the closed Hypothesis relation taxonomy.

The data lives in ``shared/hypothesis_taxonomy.json`` so the Python
squad and the JS view read the same file. Every Hypothesis node carries
``props.type`` in :data:`HYPOTHESIS_TYPES`; detection modes (hyphenated)
map onto those types through :data:`LEGACY_MODE_TYPE`; unknown modes stay
unknown so callers can fail open.

Do not restate these maps elsewhere. Add a new relation to the JSON and
both sides pick it up.
"""

import json
from pathlib import Path


_TAXONOMY_PATH = (
    Path(__file__).resolve().parent.parent / "shared" / "hypothesis_taxonomy.json"
)


def _load() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    with _TAXONOMY_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    types = tuple(data.get("types", ()))
    modes = tuple(data.get("detection_modes", ()))
    mode_to_type = dict(data.get("mode_to_type", {}))
    if not types or not modes or not mode_to_type:
        raise ValueError(f"incomplete taxonomy: {_TAXONOMY_PATH}")
    if len(set(types)) != len(types):
        raise ValueError(f"duplicate hypothesis type in {_TAXONOMY_PATH}")
    bad = {m: t for m, t in mode_to_type.items() if t not in types}
    if bad:
        raise ValueError(f"mode maps to unknown type in {_TAXONOMY_PATH}: {bad}")
    missing = [m for m in modes if m not in mode_to_type]
    if missing:
        raise ValueError(f"detection mode missing a type in {_TAXONOMY_PATH}: "
                         f"{missing}")
    return types, modes, mode_to_type


HYPOTHESIS_TYPES, DETECTION_MODES, LEGACY_MODE_TYPE = _load()

# Rotating detectors only. `transitive` is a layer-2 meta-hypothesis
# mode, so it lives in LEGACY_MODE_TYPE without being a detector.
MODES = DETECTION_MODES
MODE_HYPOTHESIS_TYPE = {m: LEGACY_MODE_TYPE[m] for m in DETECTION_MODES}


def normalize_hypothesis_type(mode) -> str | None:
    """Map a detection mode or stored type to the closed taxonomy.

    Returns the closed type, or None for unknown/empty modes (callers
    then fall back to a generic test, fail-open).
    """
    if not mode:
        return None
    m = str(mode).strip()
    if m in HYPOTHESIS_TYPES:
        return m
    return LEGACY_MODE_TYPE.get(m)
