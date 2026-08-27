from __future__ import annotations

"""Backfill historical contract years by querying the Wayback Machine CDX API
for prior snapshots of a listing page or document URL, then re-parsing those
snapshots with the same state parser used for the live site."""

from datetime import datetime

import requests

from salt_tracker.models import DiscoveredDocument

CDX_API = "https://web.archive.org/cdx/search/cdx"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RoadSaltTracker/1.0; +research use)"}


def list_snapshots(url: str, from_year: int = 2021, to_year: int | None = None,
                    timeout: int = 30) -> list[dict]:
    """Returns one entry per year with the closest available snapshot,
    deduped by (year) so we don't reprocess dozens of near-identical crawls
    of the same page. Returns [] (rather than raising) on any network
    failure so a slow/unreachable archive.org doesn't take down the rest
    of the discovery run."""
    to_year = to_year or datetime.now().year
    params = {
        "url": url,
        "output": "json",
        "from": f"{from_year}0101",
        "to": f"{to_year}1231",
        "filter": "statuscode:200",
        "collapse": "timestamp:4",  # collapse to one snapshot per year
        "fl": "timestamp,original",
    }
    try:
        resp = requests.get(CDX_API, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        rows = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [wayback] skipping {url} -- CDX lookup failed: {e}")
        return []

    if not rows or len(rows) < 2:
        return []
    header, *data = rows
    return [dict(zip(header, row)) for row in data]


def snapshot_url(timestamp: str, original_url: str) -> str:
    return f"https://web.archive.org/web/{timestamp}/{original_url}"


def discover_from_archive(source, listing_url: str, from_year: int = 2021) -> list[DiscoveredDocument]:
    """`source` is a BaseSource instance (e.g. MichiganSource) whose
    `_parse_listing_page` we reuse against archived HTML, so we don't
    duplicate per-state parsing logic for historical pages."""
    docs: list[DiscoveredDocument] = []
    for snap in list_snapshots(listing_url, from_year=from_year):
        archived_url = snapshot_url(snap["timestamp"], snap["original"])
        try:
            html = source.fetch(archived_url)
        except Exception as e:
            print(f"  [wayback] skipping snapshot {archived_url} -- fetch failed: {e}")
            continue
        parsed = source._parse_listing_page(html, archived_url)
        for d in parsed:
            d.source_type = "wayback_machine"
        docs.extend(parsed)
    return docs
