from __future__ import annotations
"""Normalize raw vendor strings to a canonical name. Exact/alias matches
first (vendor_aliases.yaml), then fuzzy matching against known canonical
names for anything not seen before. Low-confidence fuzzy matches are left
unresolved so the quality layer routes them to human review instead of
silently guessing."""

from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

ALIASES_PATH = Path(__file__).resolve().parent.parent.parent / "vendor_aliases.yaml"
FUZZY_MATCH_THRESHOLD = 88


def _load_aliases() -> dict[str, str]:
    with open(ALIASES_PATH) as f:
        raw = yaml.safe_load(f) or {}
    return {k.strip().lower(): v for k, v in raw.items()}


_ALIASES = _load_aliases()
_CANONICAL_NAMES = sorted(set(_ALIASES.values()))


def normalize_vendor(raw: str | None) -> tuple[str | None, float]:
    """

Returns (canonical_name_or_None, confidence 0-1)."""
    if not raw or not raw.strip():
        return None, 0.0

    key = raw.strip().lower()
    if key in _ALIASES:
        return _ALIASES[key], 1.0

    match = process.extractOne(raw, _CANONICAL_NAMES, scorer=fuzz.token_sort_ratio)
    if match and match[1] >= FUZZY_MATCH_THRESHOLD:
        return match[0], match[1] / 100

    return None, 0.0  # unresolved -> flagged for review downstream


def add_alias(raw: str, canonical: str) -> None:
    """Persist a human-reviewed alias so future documents auto-resolve."""
    global _ALIASES, _CANONICAL_NAMES
    with open(ALIASES_PATH, "a") as f:
        f.write(f"\n{raw}: {canonical}")
    _ALIASES[raw.strip().lower()] = canonical
    _CANONICAL_NAMES = sorted(set(_ALIASES.values()))
