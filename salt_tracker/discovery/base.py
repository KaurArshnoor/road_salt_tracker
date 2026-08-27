from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List
import requests

from salt_tracker.models import DiscoveredDocument

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RoadSaltTracker/1.0; +research use)"
}


class BaseSource(ABC):
    """One subclass per state. `discover()` returns every document link the
    source currently exposes (deltas/dedupe happen at ingestion time via
    content hash, so discovery can be a dumb "list everything" call)."""

    state: str = ""
    listing_urls: List[str] = []

    def __init__(self, listing_urls: List[str] | None = None, session: requests.Session | None = None):
        if listing_urls:
            self.listing_urls = listing_urls
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    @abstractmethod
    def discover(self) -> List[DiscoveredDocument]:
        ...
