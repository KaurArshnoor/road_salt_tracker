from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from salt_tracker.discovery.base import BaseSource
from salt_tracker.models import DiscoveredDocument

FILE_EXT_RE = re.compile(r"\.(pdf|xlsx?|csv)(\?|$)", re.IGNORECASE)
REPLACES_RE = re.compile(r"Replaces\s+(\d{4,6})")
AWARD_NUMBER_RE = re.compile(r"Award:\s*(\d{4,6})")
DATE_RE = re.compile(r"[A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{4}")
CONTRACT_PERIOD_WINDOW = 150


class NewYorkSource(BaseSource):
    """NY OGS publishes one page per statewide award (e.g. award-23409), and
    each page links directly to the current award's documents plus states
    which prior award number it replaces (e.g. "Award: 23409 (Replaces
    23358)"). That gives us a native way to walk backward through contract
    years without needing the Wayback Machine -- see _crawl_award_chain."""

    state = "NY"
    listing_urls = ["https://ogs.ny.gov/award-23409"]  # seed page; update when a new award supersedes this one
    max_history_hops = 6  # ~ FY2022-FY2026 back from a current award, one hop per contract cycle

    def discover(self) -> list[DiscoveredDocument]:
        docs: list[DiscoveredDocument] = []
        for start_url in self.listing_urls:
            docs.extend(self._crawl_award_chain(start_url))
        return docs

    def _crawl_award_chain(self, start_url: str) -> list[DiscoveredDocument]:
        docs: list[DiscoveredDocument] = []
        seen: set[str] = set()
        url: str | None = start_url

        for hop in range(self.max_history_hops):
            if not url or url in seen:
                break
            seen.add(url)
            try:
                html = self.fetch(url)
            except Exception as e:
                print(f"  [NY] stopping award chain at {url} -- fetch failed: {e}")
                break

            page_docs, prior_url = self._parse_award_page(html, url)
            for d in page_docs:
                if hop > 0:
                    d.source_type = "state_website"  # still a live page, just a superseded award
            docs.extend(page_docs)
            url = prior_url

        return docs

    def _parse_award_page(self, html: str, base_url: str) -> tuple[list[DiscoveredDocument], str | None]:
        soup = BeautifulSoup(html, "lxml")
        page_text = soup.get_text(" ", strip=True)
        fy = self._extract_fiscal_year(page_text)
        award_number = self._extract_award_number(page_text)
        period_raw, period_start, period_end = self._extract_contract_period(page_text)

        docs = []
        for a in soup.select("a[href]"):
            href = a["href"]
            fmt_match = FILE_EXT_RE.search(href)
            if not fmt_match:
                continue
            docs.append(DiscoveredDocument(
                state=self.state,
                source_url=urljoin(base_url, href),
                source_type="state_website",
                vendor_hint=None,  # NY awards are statewide/multi-vendor -- vendor comes from the pricing sheet content
                fiscal_year_hint=fy,
                doc_format_hint=fmt_match.group(1).lower(),
                contract_reference=award_number,
                contract_period_raw=period_raw,
                contract_period_start=period_start,
                contract_period_end=period_end,
            ))

        return docs, self._find_prior_award_url(page_text, base_url)

    @staticmethod
    def _extract_fiscal_year(page_text: str) -> int | None:
        """"Contract Period: September 12, 2025 - August 31, 2026" -> FY2026
        (later year of the range). Older award pages sometimes use a
        different date format or an en-dash instead of a hyphen, so rather
        than matching one exact date pattern, we just grab a window of text
        after "Contract Period" and take the latest 4-digit year in it --
        format-agnostic and works whether the separator is "-", "\u2013", "to", etc."""
        idx = page_text.find("Contract Period")
        if idx == -1:
            return None
        window = page_text[idx: idx + CONTRACT_PERIOD_WINDOW]
        years = re.findall(r"20\d{2}", window)
        return max(int(y) for y in years) if years else None

    @staticmethod
    def _extract_award_number(page_text: str) -> str | None:
        m = AWARD_NUMBER_RE.search(page_text)
        return m.group(1) if m else None

    @staticmethod
    def _extract_contract_period(page_text: str) -> tuple[str | None, str | None, str | None]:
        """Returns (raw snippet as printed, ISO start date, ISO end date).
        Same format-agnostic window approach as _extract_fiscal_year, but
        also tries to parse the two date-like substrings in that window into
        real ISO dates for provenance/drill-down use -- falls back to just
        the raw text (with no parsed dates) if the format is too irregular
        to parse confidently, rather than guessing wrong. The raw snippet is
        trimmed to end right after the second date match, not the full fixed
        window, so it doesn't run on into unrelated text further down the page."""
        idx = page_text.find("Contract Period")
        if idx == -1:
            return None, None, None
        window = page_text[idx: idx + CONTRACT_PERIOD_WINDOW]

        matches = list(DATE_RE.finditer(window))
        if len(matches) < 2:
            raw = window.split(":", 1)[1].strip() if ":" in window else window.strip()
            return raw, None, None

        raw = window[:matches[1].end()]
        raw = raw.split(":", 1)[1].strip() if ":" in raw else raw.strip()

        start_iso = end_iso = None
        try:
            start_iso = date_parser.parse(matches[0].group()).date().isoformat()
            end_iso = date_parser.parse(matches[1].group()).date().isoformat()
        except (ValueError, OverflowError):
            pass  # irregular date format -- keep raw text, leave parsed dates null
        return raw, start_iso, end_iso

    @staticmethod
    def _find_prior_award_url(page_text: str, base_url: str) -> str | None:
        m = REPLACES_RE.search(page_text)
        if not m:
            return None
        return urljoin(base_url, f"/award-{m.group(1)}")
