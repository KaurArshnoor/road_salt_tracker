from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from salt_tracker.discovery.base import BaseSource
from salt_tracker.models import DiscoveredDocument

FILE_EXT_RE = re.compile(r"\.(pdf|xlsx?|csv)(\?|$)", re.IGNORECASE)
# michigan.gov links the contract number itself (e.g. "260000000712") to the
# PDF -- the URL itself doesn't necessarily end in .pdf (often a CMS/docstore
# link with a query string or no extension at all), so we match on link text
# being a long numeric contract ID instead of relying on the URL shape.
CONTRACT_NUMBER_RE = re.compile(r"^\d{8,}$")

# The page also links W-9 forms, insurance certs, etc under the same vendor
# section, and those also end in .pdf -- exclude by keyword so a generic
# file-extension match doesn't sweep them in as if they were pricing docs.
EXCLUDE_KEYWORDS_RE = re.compile(r"w-?9|insurance|certificate|logo|template", re.IGNORECASE)

# Heading-ish tags that introduce a new vendor section, plus a text pattern
# fallback for cases where the vendor name isn't in a heading tag at all
# (e.g. plain <p><strong>Detroit Salt (Early/Seasonal)</strong></p>).
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"}
VENDOR_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z .,&\-']+?)\s*(?:\(Early/Seasonal\)|\(Seasonal\)|\(Early\))?\s*$")

KNOWN_VENDORS = ["Compass Minerals", "Detroit Salt", "Morton Salt", "Cargill",
                  "American Rock Salt"]


class MichiganSource(BaseSource):
    state = "MI"
    listing_urls = [
        "https://www.michigan.gov/dtmb/procurement/mideal-extended-purchasing-program/"
        "mideal-contract-search/categories/folder-2/salt-bulk-rock"
    ]

    def discover(self) -> list[DiscoveredDocument]:
        docs: list[DiscoveredDocument] = []
        for listing_url in self.listing_urls:
            html = self.fetch(listing_url)
            docs.extend(self._parse_listing_page(html, listing_url))
        return docs

    def _parse_listing_page(self, html: str, base_url: str) -> list[DiscoveredDocument]:
        """Walks the page in document order, tracking the most recent
        vendor-looking heading/text so that when we hit a link -- whether
        it's a direct .pdf/.xlsx URL, OR the contract-number link Michigan
        actually uses (e.g. "Contract #: 260000000712" hyperlinked to the
        PDF) -- we can tag it with the right vendor, and skip W-9/insurance
        links that live in the same vendor section."""
        soup = BeautifulSoup(html, "lxml")
        body = soup.find("body") or soup
        page_text = soup.get_text(" ", strip=True)  # full text, for the season banner
        fy, period_raw = self._match_fiscal_year(page_text)

        docs: list[DiscoveredDocument] = []
        current_vendor: str | None = None

        for el in body.descendants:
            name = getattr(el, "name", None)
            if name is None:
                continue

            if name in HEADING_TAGS:
                text = el.get_text(" ", strip=True)
                vendor = self._extract_vendor_from_text(text)
                if vendor:
                    current_vendor = vendor
                continue

            if name != "a" or not el.get("href"):
                continue

            href = el["href"]
            link_text = el.get_text(" ", strip=True)

            is_contract_number_link = bool(CONTRACT_NUMBER_RE.match(link_text))
            filename_stem = self._filename_stem(href)
            is_contract_filename = bool(CONTRACT_NUMBER_RE.match(filename_stem))
            is_generic_file_link = bool(FILE_EXT_RE.search(href)) \
                and not EXCLUDE_KEYWORDS_RE.search(href) \
                and not EXCLUDE_KEYWORDS_RE.search(link_text)

            if not (is_contract_number_link or is_contract_filename or is_generic_file_link):
                continue

            # the contract number IS the link text (or filename) in Michigan's
            # "Contract #: <number>" pattern -- capture it as the reference
            # for provenance, rather than only using it to decide "is this a
            # contract document" and then discarding it
            contract_reference = link_text if is_contract_number_link \
                else (filename_stem if is_contract_filename else None)

            url = urljoin(base_url, href)
            vendor_hint = current_vendor or self._extract_vendor_from_text(link_text)
            fmt_match = FILE_EXT_RE.search(href)
            docs.append(DiscoveredDocument(
                state=self.state,
                source_url=url,
                source_type="state_website",
                vendor_hint=vendor_hint,
                fiscal_year_hint=fy,
                doc_format_hint=fmt_match.group(1).lower() if fmt_match else "pdf",
                contract_reference=contract_reference,
                contract_period_raw=period_raw,
                # exact effective/expiration dates aren't on this listing page --
                # they're inside the PDF itself (Notice of Contract page 1),
                # backfilled at extraction time in orchestrator.py
            ))

        return docs

    @staticmethod
    def _filename_stem(href: str) -> str:
        path = href.split("?")[0]
        filename = path.rsplit("/", 1)[-1]
        return filename.rsplit(".", 1)[0] if "." in filename else filename

    @staticmethod
    def _extract_vendor_from_text(text: str) -> str | None:
        if not text:
            return None
        for vendor in KNOWN_VENDORS:
            if vendor.lower() in text.lower():
                return vendor
        m = VENDOR_LINE_RE.match(text)
        if m and len(m.group(1).strip()) <= 60:
            return m.group(1).strip()
        return None

    @staticmethod
    def _match_fiscal_year(text: str) -> tuple[int | None, str | None]:
        # Michigan's page banner reads e.g. "ROAD SALT 2026/2027 WINTER
        # SEASON" -> FY = second (later) year, per the brief's convention.
        # Search the full visible text, not just raw HTML head/nav markup.
        # Returns (fiscal_year, raw_season_text) so the raw text can be kept
        # as contract_period_raw for provenance/audit purposes.
        m = re.search(r"ROAD SALT\s+(20\d{2})\s*[/\-\u2013\u2014]\s*(20\d{2}|\d{2})", text, re.IGNORECASE)
        if not m:
            # fallback: any year-range pattern near the word "SEASON"
            m = re.search(r"(20\d{2})\s*[/\-\u2013\u2014]\s*(20\d{2}|\d{2})\s+WINTER\s+SEASON", text, re.IGNORECASE)
        if not m:
            return None, None
        end = m.group(2)
        raw = f"{m.group(1)}/{end}"
        if len(end) == 2:
            end = m.group(1)[:2] + end
        return int(end), raw
