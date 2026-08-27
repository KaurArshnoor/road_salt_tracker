from __future__ import annotations
"""LLM-based structured extraction for documents where deterministic
parsing fails (messy tables, footnoted contract language, OCR text).

Deliberately scoped to *extraction only*: the model pulls out the fields it
can read off the page (vendor, volume, price, period, etc). It never sums,
averages, or otherwise computes anything -- that happens in Python
(analytics/aggregate.py) so results stay reproducible and auditable back to
a page/document, not to a model's arithmetic.
"""

import json
import os

from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

MODEL = "llama-3.3-70b-versatile"  # swap for whatever Groq model you prefer

SYSTEM_PROMPT = """

You extract structured line items from government road \
salt procurement contract text. You are an extraction tool, not a \
calculator: report only values that are literally stated on the page. Do \
not sum, average, or infer totals that are not explicitly written.

For each distinct vendor/period/fill-type row you find, output an object \
with these fields (use null when a field is not present on the page):

- vendor: string
- county: string or null
- municipality: string or null
- contract_period_raw: string exactly as written (e.g. "2025-2026", "FY26")
- fill_type: one of "early_fill", "seasonal_fill", "other", null
- volume_tons: number or null
- price_per_ton: number or null
- contract_terms: short string of any notable terms/conditions, or null
- stated_line_total: number or null (ONLY if the document itself states a \
  dollar total for this row -- do not calculate one)

Respond with ONLY a JSON object of the form {"line_items": [...]}. No \
markdown fences, no preamble, no commentary. If no relevant line items are \
present, respond with {"line_items": []}."""


def _client() -> Groq:
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=20))
def extract_line_items_from_text(page_text: str, page_number: int, state: str,
                                  vendor_hint: str | None = None) -> list[dict]:
    if not page_text.strip():
        return []

    user_prompt = (
        f"State: {state}\n"
        f"Vendor hint from filename (may be wrong, verify against text): {vendor_hint}\n"
        f"Page {page_number} text:\n---\n{page_text[:12000]}\n---"
    )

    resp = _client().chat.completions.create(
        model=MODEL,
        max_tokens=4000,
        temperature=0,
        response_format={"type": "json_object"},  # Groq enforces valid JSON output
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw = resp.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []

    items = parsed.get("line_items", []) if isinstance(parsed, dict) else []
    if not isinstance(items, list):
        return []

    for item in items:
        item["source_page"] = page_number
    return items
