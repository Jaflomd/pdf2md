from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import Pdf2MdError, run_conversion


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="Convert local PDF/EPUB files into Markdown, chapter files, and chunk files.",
    )
    parser.add_argument("input_path", type=Path, help="Path to the source PDF, EPUB, or AZW3 file.")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("outputs"),
        help="Base output directory. Final files land under <outdir>/<input_stem>/.",
    )
    parser.add_argument(
        "--chunk-target",
        type=int,
        default=1000,
        help="Target token size per chunk.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=120,
        help="Overlap token budget between chunks.",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "docling", "pymupdf4llm"),
        default="auto",
        help="Primary engine for full-document Markdown extraction.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_conversion(
            input_path=args.input_path,
            outdir=args.outdir,
            chunk_target=args.chunk_target,
            chunk_overlap=args.chunk_overlap,
            engine=args.engine,
        )
    except Pdf2MdError as exc:
        parser.exit(status=2, message=f"pdf2md: error: {exc}\n")

    print(f"Wrote outputs to {result.output_root}")
    print(f"Full Markdown engine: {result.manifest['engine']['full_markdown_engine']}")
    if result.manifest["warnings"]:
        print("Warnings:")
        for warning in result.manifest["warnings"]:
            print(f"  - {warning}")
    return 0
