import io
import logging
from typing import List

import fitz  # PyMuPDF

from .models import PDFPage

logger = logging.getLogger(__name__)


class PDFExtractor:
    def __init__(self, pdf_source: str | bytes):
        """Initialize PDFExtractor from file path or bytes."""
        if isinstance(pdf_source, bytes):
            self.pdf_path = None
            self.doc = fitz.open(stream=pdf_source, filetype="pdf")
        else:
            self.pdf_path = pdf_source
            self.doc = fitz.open(pdf_source)

    def get_page_count(self) -> int:
        return len(self.doc)

    def extract_text(self, page_num: int) -> str:
        """Extract text from a specific page (0-indexed)."""
        page = self.doc[page_num]
        return page.get_text()

    def extract_all_text(self) -> List[str]:
        return [self.extract_text(i) for i in range(self.get_page_count())]

    def extract_pages_as_pdf(self, page_numbers: List[int]) -> bytes:
        """Extract specific pages and return as a new PDF in bytes.
        Args:
            page_numbers: List of page numbers (1-indexed)
        """
        if not page_numbers:
            raise ValueError("Cannot extract PDF with zero pages")

        new_doc = fitz.open()
        pages_added = 0
        for page_num in page_numbers:
            page_idx = page_num - 1
            if 0 <= page_idx < len(self.doc):
                new_doc.insert_pdf(self.doc, from_page=page_idx, to_page=page_idx)
                pages_added += 1

        if pages_added == 0:
            new_doc.close()
            raise ValueError(f"No valid pages found. Requested: {page_numbers}, PDF has {len(self.doc)} pages")

        pdf_bytes = new_doc.tobytes()
        new_doc.close()
        return pdf_bytes

    async def extract_pages_data(self, upload_id: str) -> List[PDFPage]:
        pages = []
        for page_num in range(self.get_page_count()):
            full_text = self.extract_text(page_num)
            text_preview = full_text[:200].replace("\n", " ").strip() + "..."
            pages.append(
                PDFPage(
                    page_num=page_num + 1,
                    thumbnail_url="",
                    text_preview=text_preview,
                    full_text=full_text,
                )
            )
        return pages

    def close(self):
        self.doc.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
