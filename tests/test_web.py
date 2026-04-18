from __future__ import annotations

import io
import zipfile
from pathlib import Path

import fitz

from pdf2md.web import convert_document_bytes


def _make_pdf_bytes() -> bytes:
    pdf = fitz.open()
    page_1 = pdf.new_page()
    page_1.insert_text((72, 72), "Chapter 1", fontsize=24)
    page_1.insert_text((72, 120), "This is content for chapter 1.", fontsize=12)

    page_2 = pdf.new_page()
    page_2.insert_text((72, 72), "Chapter 2", fontsize=24)
    page_2.insert_text((72, 120), "This is content for chapter 2.", fontsize=12)

    pdf.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 2]])
    return pdf.tobytes()


def test_convert_document_bytes_returns_markdown_and_bundle(tmp_path: Path) -> None:
    _ = tmp_path
    result = convert_document_bytes(
        file_bytes=_make_pdf_bytes(),
        filename="sample.pdf",
        chunk_target=80,
        chunk_overlap=10,
        engine="pymupdf4llm",
    )

    assert "Chapter 1" in result.document_markdown
    assert result.manifest["source"]["path"] == "sample.pdf"
    assert result.manifest["engine"]["full_markdown_engine"] == "pymupdf4llm"
    assert len(result.chapter_markdowns) == 2

    with zipfile.ZipFile(io.BytesIO(result.archive_bytes)) as archive:
        names = set(archive.namelist())

    assert "sample/document.md" in names
    assert "sample/manifest.json" in names
