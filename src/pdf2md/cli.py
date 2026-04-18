from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pdf2MdError, run_conversion


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description=(
            "Convert a local PDF, EPUB, DOCX, or AZW3 into a deterministic Markdown bundle "
            "with document, section, and chunk outputs."
        ),
        epilog=(
            "Examples:\n"
            "  pdf2md ./book.pdf\n"
            "  pdf2md ./manual.pdf --split pages --page-group-size 20\n"
            "  pdf2md ./report.pdf --name acme-report --engine pymupdf4llm --json\n"
            "  pdf2md ./scanned.pdf --fast-mode"
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument("input_path", type=Path, help="Path to the source PDF, EPUB, DOCX, or AZW3 file.")
    parser.add_argument(
        "-o",
        "--outdir",
        type=Path,
        default=Path("outputs"),
        help="Base directory where the deterministic bundle folder will be written.",
    )
    parser.add_argument(
        "--name",
        help="Optional human-friendly bundle name prefix. The tool still appends a stable content hash.",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "docling", "pymupdf4llm"),
        default="auto",
        help="Engine used for the full-document Markdown export.",
    )
    parser.add_argument(
        "--split",
        choices=("auto", "chapters", "pages"),
        default="auto",
        help="How to segment section files. 'auto' prefers chapters and falls back to page groups.",
    )
    parser.add_argument(
        "--page-group-size",
        type=int,
        default=30,
        help="Pages per section when split mode resolves to page groups.",
    )
    parser.add_argument(
        "--chunk-target",
        type=int,
        default=1000,
        help="Target token size per chunk file.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=120,
        help="Approximate overlap budget between neighboring chunks.",
    )
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="Compatibility shortcut for '--engine pymupdf4llm --split pages'.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a compact JSON summary instead of human-readable status lines.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    requested_engine = "pymupdf4llm" if args.fast_mode else args.engine
    requested_split = "pages" if args.fast_mode else args.split

    try:
        result = run_conversion(
            input_path=args.input_path,
            outdir=args.outdir,
            chunk_target=args.chunk_target,
            chunk_overlap=args.chunk_overlap,
            engine=requested_engine,
            fast_mode=args.fast_mode,
            page_group_size=args.page_group_size,
            split_mode=requested_split,
            output_name=args.name,
        )
    except Pdf2MdError as exc:
        parser.exit(status=2, message=f"pdf2md: error: {exc}\n")

    manifest = result.manifest
    summary = {
        "output_root": str(result.output_root),
        "bundle_name": manifest["output"]["bundle_name"],
        "engine": manifest["engine"]["full_markdown_engine"],
        "segmentation_mode": manifest["processing"]["segmentation_mode"],
        "sections": len(manifest["chapters"]),
        "chunks": sum(int(chapter["chunk_count"]) for chapter in manifest["chapters"]),
        "warnings": manifest["warnings"],
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    print(f"Wrote bundle to {result.output_root}")
    print(f"Bundle name: {summary['bundle_name']}")
    print(f"Engine: {summary['engine']}")
    print(f"Segmentation: {summary['segmentation_mode']}")
    print(f"Sections: {summary['sections']} | Chunks: {summary['chunks']}")
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"  - {warning}")
    return 0
