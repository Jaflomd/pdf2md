from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from pdf2md.pipeline import UnsupportedInputError, run_conversion


def _make_pdf(path: Path) -> None:
    pdf = fitz.open()
    page_1 = pdf.new_page()
    page_1.insert_text((72, 72), "Chapter 1", fontsize=24)
    page_1.insert_text((72, 120), "This is content for chapter 1.", fontsize=12)

    page_2 = pdf.new_page()
    page_2.insert_text((72, 72), "Chapter 2", fontsize=24)
    page_2.insert_text((72, 120), "This is content for chapter 2.", fontsize=12)

    pdf.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 2]])
    pdf.save(path)


def test_run_conversion_with_pymupdf_engine_writes_expected_outputs(tmp_path: Path) -> None:
    source = tmp_path / "sample.pdf"
    outdir = tmp_path / "out"
    _make_pdf(source)

    result = run_conversion(
        input_path=source,
        outdir=outdir,
        chunk_target=80,
        chunk_overlap=10,
        engine="pymupdf4llm",
    )

    root = result.output_root
    assert (root / "document.md").exists()
    assert (root / "manifest.json").exists()
    assert (root / "chapters" / "01-chapter-1.md").exists()
    assert (root / "chapters" / "02-chapter-2.md").exists()
    assert (root / "chunks" / "index.jsonl").exists()

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"]["full_markdown_engine"] == "pymupdf4llm"
    assert len(manifest["chapters"]) == 2


def test_auto_engine_falls_back_to_pymupdf_when_docling_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "fallback.pdf"
    outdir = tmp_path / "out"
    _make_pdf(source)

    from pdf2md import pipeline

    def fail_docling(_: Path) -> str:
        raise RuntimeError("docling unavailable in test")

    monkeypatch.setattr(pipeline, "_extract_full_markdown_with_docling", fail_docling)

    result = run_conversion(
        input_path=source,
        outdir=outdir,
        chunk_target=80,
        chunk_overlap=10,
        engine="auto",
    )

    assert result.manifest["engine"]["fallback_used"] is True
    assert result.manifest["engine"]["full_markdown_engine"] == "pymupdf4llm"
    assert result.manifest["warnings"]


def test_azw3_requires_calibre_when_converter_missing(tmp_path: Path) -> None:
    source = tmp_path / "book.azw3"
    source.write_bytes(b"not-a-real-kindle-file")

    with pytest.raises(UnsupportedInputError):
        run_conversion(
            input_path=source,
            outdir=tmp_path / "out",
            engine="auto",
        )
