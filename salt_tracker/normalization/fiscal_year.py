from __future__ import annotations
"""Normalize contract-period strings into a single FY int, per the road-salt
industry convention: fiscal year runs Oct 1 - Sep 30, and is named for the
year it ends in. So "2025-2026", "2025/26", "Winter 2025-26" all normalize
to FY2026."""

import re

# Ordered so more specific patterns are tried before generic ones.
PATTERNS = [
    re.compile(r"FY\s*'?(\d{2,4})", re.IGNORECASE),                      # FY2026, FY26, FY'26
    re.compile(r"(20\d{2})\s*[-/]\s*(20\d{2}|\d{2})"),                    # 2025-2026, 2025/26
    re.compile(r"\b(20\d{2})\b"),                                        # bare year -> assume it's already FY
]


def normalize_fiscal_year(raw: str | None) -> int | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()

    m = PATTERNS[0].search(text)
    if m:
        y = m.group(1)
        return int(y) if len(y) == 4 else 2000 + int(y)

    m = PATTERNS[1].search(text)
    if m:
        start, end = m.group(1), m.group(2)
        if len(end) == 2:
            end = start[:2] + end
        return int(end)  # FY = later year of the range

    m = PATTERNS[2].search(text)
    if m:
        return int(m.group(1))

    return None


def fiscal_year_window(fy: int) -> tuple[str, str]:
    """

FY2026 -> (2025-10-01, 2026-09-30)."""
    return f"{fy - 1}-10-01", f"{fy}-09-30"
