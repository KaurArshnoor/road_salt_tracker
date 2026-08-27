from __future__ import annotations

"""OCR fallback for scanned/image-only PDFs, triggered by low text density
in pdf_extractor.text_density(). Output is per-page raw text handed to the
LLM extractor -- OCR text on a scanned schedule is rarely clean enough for
deterministic column parsing."""

from dataclasses import dataclass

import pytesseract
from pdf2image import convert_from_path


@dataclass
class OcrPage:
    page_number: int
    text: str


def ocr_pdf(path: str, dpi: int = 300) -> list[OcrPage]:
    images = convert_from_path(path, dpi=dpi)
    pages = []
    for i, img in enumerate(images, start=1):
        text = pytesseract.image_to_string(img)
        pages.append(OcrPage(page_number=i, text=text))
    return pages
