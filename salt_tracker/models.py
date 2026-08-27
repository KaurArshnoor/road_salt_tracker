from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiscoveredDocument:
    state: str
    source_url: str
    source_type: str  # state_website | wayback_machine | email
    vendor_hint: Optional[str] = None
    fiscal_year_hint: Optional[int] = None
    doc_format_hint: Optional[str] = None
    contract_reference: Optional[str] = None    # e.g. MI contract # "260000000712", NY award # "23409"
    contract_period_raw: Optional[str] = None   # as printed at the source, e.g. "2026/2027"
    contract_period_start: Optional[str] = None  # ISO date, when known explicitly at discovery time
    contract_period_end: Optional[str] = None    # ISO date, same


@dataclass
class ExtractedLineItem:
    document_id: int
    state: str
    county: Optional[str] = None
    municipality: Optional[str] = None
    vendor_raw: Optional[str] = None
    contract_period_raw: Optional[str] = None
    fiscal_year: Optional[int] = None
    fill_type: Optional[str] = None
    volume_tons: Optional[float] = None
    price_per_ton: Optional[float] = None
    line_total: Optional[float] = None
    contract_terms: Optional[str] = None
    source_page: Optional[int] = None
    extraction_method: str = "unknown"
    confidence_score: float = 0.0  # extraction-time confidence; orchestrator persists this as extraction_confidence
    extra: dict = field(default_factory=dict)
