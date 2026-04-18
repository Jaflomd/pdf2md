from __future__ import annotations

import json
from pathlib import Path

import fitz

from pdf2md.cli import main


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


def test_cli_json_summary(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sample.pdf"
    outdir = tmp_path / "out"
    _make_pdf(source)

    exit_code = main(
        [
            str(source),
            "--outdir",
            str(outdir),
            "--engine",
            "pymupdf4llm",
            "--json",
            "--name",
            "cli-check",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["bundle_name"].startswith("cli-check--")
    assert payload["segmentation_mode"] == "chapters"
    assert Path(payload["output_root"]).exists()
