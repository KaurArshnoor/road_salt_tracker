from __future__ import annotations
import re
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from salt_tracker.discovery.base import BaseSource
from salt_tracker.models import DiscoveredDocument

FILE_EXT_RE = re.compile(r"\.(pdf|xlsx?|csv)$", re.IGNORECASE)
SALT_KEYWORDS = re.compile(r"salt|deic", re.IGNORECASE)


class PennsylvaniaSource(BaseSource):
    state = "PA"
    listing_urls = [
        "https://www.emarketplace.state.pa.us/Search.aspx?searchtype=contract&keywords=rock+salt",
        "https://www.dgs.pa.gov/Materials-Services-Procurement/COSTARS/Pages/COSTARS-Contracts.aspx",
    ]

    def discover(self) -> List[DiscoveredDocument]:
        docs: List[DiscoveredDocument] = []
        for listing_url in self.listing_urls:
            html = self.fetch(listing_url)
            docs.extend(self._parse_listing_page(html, listing_url))
        return docs

    def _parse_listing_page(self, html: str, base_url: str) -> List[DiscoveredDocument]:
        """eMarketplace/DGS pages mix contract detail pages and direct file
        links. We keep direct file links, and for anything that looks like a
        contract detail page (no file extension but salt-related link text)
        we follow one level deep to find the underlying PDF/XLSX."""
        soup = BeautifulSoup(html, "lxml")
        docs = []
        for a in soup.select("a[href]"):
            href = a["href"]
            link_text = a.get_text(" ", strip=True)
            url = urljoin(base_url, href)

            if FILE_EXT_RE.search(href):
                if not SALT_KEYWORDS.search(link_text) and not SALT_KEYWORDS.search(href):
                    continue
                docs.append(DiscoveredDocument(
                    state=self.state,
                    source_url=url,
                    source_type="state_website",
                    fiscal_year_hint=self._match_fiscal_year(link_text) or self._match_fiscal_year(href),
                    doc_format_hint=FILE_EXT_RE.search(href).group(1).lower(),
                ))
            elif SALT_KEYWORDS.search(link_text):
                # contract detail page -> follow one level to find the file
                try:
                    detail_html = self.fetch(url)
                except Exception:
                    continue
                detail_soup = BeautifulSoup(detail_html, "lxml")
                for detail_a in detail_soup.select("a[href]"):
                    detail_href = detail_a["href"]
                    if FILE_EXT_RE.search(detail_href):
                        docs.append(DiscoveredDocument(
                            state=self.state,
                            source_url=urljoin(url, detail_href),
                            source_type="state_website",
                            fiscal_year_hint=self._match_fiscal_year(link_text),
                            doc_format_hint=FILE_EXT_RE.search(detail_href).group(1).lower(),
                        ))
        return docs

    @staticmethod
    def _match_fiscal_year(text: str) -> int | None:
        m = re.search(r"(20\d{2})\s*[-/]\s*(20\d{2}|\d{2})", text)
        if not m:
            return None
        end = m.group(2)
        if len(end) == 2:
            end = m.group(1)[:2] + end
        return int(end)
