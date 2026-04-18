from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import tiktoken
from slugify import slugify


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+\.)\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
SUPPORTED_INPUTS = {".pdf", ".epub", ".azw3"}


class Pdf2MdError(RuntimeError):
    """Base runtime error for the converter."""


class UnsupportedInputError(Pdf2MdError):
    """Raised when the input format cannot be processed."""


class ScannedDocumentError(Pdf2MdError):
    """Raised when the input PDF appears to require OCR."""


class ExtractionError(Pdf2MdError):
    """Raised when Markdown extraction fails."""


@dataclass
class PreparedInput:
    original_path: Path
    working_path: Path
    input_format: str
    processing_format: str
    notes: list[str]
    tempdir: tempfile.TemporaryDirectory[str] | None = None


@dataclass
class DocumentInfo:
    page_count: int
    toc: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass
class PageChunk:
    page_number: int
    text: str
    metadata: dict[str, Any]


@dataclass
class Chapter:
    index: int
    title: str
    slug: str
    markdown: str
    page_start: int
    page_end: int
    origin: str
    relative_path: str = ""
    chunk_count: int = 0


@dataclass
class Block:
    kind: str
    text: str
    path: list[str]
    token_count: int
    heading_level: int | None = None


@dataclass
class ChunkRecord:
    chapter_index: int
    chapter_title: str
    chunk_index: int
    breadcrumb: list[str]
    text: str
    token_count: int
    oversize: bool
    page_start: int
    page_end: int
    relative_path: str = ""


@dataclass
class ConversionResult:
    output_root: Path
    manifest: dict[str, Any]


def run_conversion(
    input_path: Path,
    outdir: Path,
    chunk_target: int = 1000,
    chunk_overlap: int = 120,
    engine: str = "auto",
) -> ConversionResult:
    if chunk_target <= 0:
        raise Pdf2MdError("--chunk-target must be positive.")
    if chunk_overlap < 0:
        raise Pdf2MdError("--chunk-overlap cannot be negative.")

    prepared = _prepare_input(input_path)
    try:
        if prepared.processing_format == "pdf" and _looks_scanned(prepared.working_path):
            raise ScannedDocumentError(
                "The PDF looks scanned or image-only. v1 does not run OCR; convert it with OCR first."
            )

        info = _collect_document_info(prepared.working_path)
        page_chunks = _extract_page_chunks(prepared.working_path)

        warnings = list(prepared.notes)
        full_markdown, full_engine, fallback_used, heading_hints = _extract_full_markdown(
            prepared=prepared,
            page_chunks=page_chunks,
            requested_engine=engine,
            warnings=warnings,
        )

        chapters = _build_chapters(
            full_markdown=full_markdown,
            toc=info.toc,
            page_chunks=page_chunks,
            default_title=input_path.stem,
            heading_hints=heading_hints,
        )

        tokenizer_name, _ = _get_encoder()
        all_chunks: list[ChunkRecord] = []
        for chapter in chapters:
            chapter_chunks = _build_chunks_for_chapter(
                chapter=chapter,
                chunk_target=chunk_target,
                chunk_overlap=chunk_overlap,
            )
            chapter.chunk_count = len(chapter_chunks)
            all_chunks.extend(chapter_chunks)

        output_root = outdir / input_path.stem
        manifest = _write_outputs(
            output_root=output_root,
            prepared=prepared,
            info=info,
            full_markdown=full_markdown,
            full_engine=full_engine,
            requested_engine=engine,
            fallback_used=fallback_used,
            chunk_target=chunk_target,
            chunk_overlap=chunk_overlap,
            tokenizer_name=tokenizer_name,
            chapters=chapters,
            chunks=all_chunks,
            warnings=warnings,
        )
        return ConversionResult(output_root=output_root, manifest=manifest)
    finally:
        if prepared.tempdir is not None:
            prepared.tempdir.cleanup()


def _prepare_input(input_path: Path) -> PreparedInput:
    path = input_path.expanduser().resolve()
    if not path.exists():
        raise Pdf2MdError(f"Input file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_INPUTS:
        supported = ", ".join(sorted(SUPPORTED_INPUTS))
        raise UnsupportedInputError(f"Unsupported input format '{suffix}'. Supported: {supported}")

    if suffix == ".azw3":
        converter = shutil.which("ebook-convert")
        if converter is None:
            raise UnsupportedInputError(
                "AZW3 requires Calibre's 'ebook-convert' to convert locally into EPUB first."
            )
        tempdir = tempfile.TemporaryDirectory(prefix="pdf2md-azw3-")
        epub_path = Path(tempdir.name) / f"{path.stem}.epub"
        try:
            subprocess.run(
                [converter, str(path), str(epub_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() or exc.stdout.strip() or "unknown Calibre error"
            raise ExtractionError(f"AZW3 conversion via ebook-convert failed: {stderr}") from exc
        return PreparedInput(
            original_path=path,
            working_path=epub_path,
            input_format="azw3",
            processing_format="epub",
            notes=["Converted AZW3 to EPUB via Calibre before Markdown extraction."],
            tempdir=tempdir,
        )

    return PreparedInput(
        original_path=path,
        working_path=path,
        input_format=suffix.lstrip("."),
        processing_format=suffix.lstrip("."),
        notes=[],
    )


def _collect_document_info(path: Path) -> DocumentInfo:
    with fitz.open(path) as doc:
        toc = []
        for raw_entry in doc.get_toc():
            if len(raw_entry) < 3:
                continue
            level, title, page = raw_entry[:3]
            title = _clean_title(title, "")
            if not title:
                continue
            toc.append({"level": int(level), "title": title, "page": int(page)})
        return DocumentInfo(
            page_count=doc.page_count,
            toc=toc,
            metadata=dict(doc.metadata or {}),
        )


def _looks_scanned(path: Path, sample_pages: int = 3) -> bool:
    with fitz.open(path) as doc:
        if doc.page_count == 0:
            return False
        sampled = min(sample_pages, doc.page_count)
        total_chars = 0
        image_pages = 0
        for page_index in range(sampled):
            page = doc.load_page(page_index)
            text = page.get_text("text")
            total_chars += len(re.sub(r"\s+", "", text))
            if page.get_images(full=True):
                image_pages += 1
        avg_chars = total_chars / sampled
        return avg_chars < 80 and image_pages > 0


def _extract_page_chunks(path: Path) -> list[PageChunk]:
    import pymupdf4llm

    raw_chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    page_chunks: list[PageChunk] = []
    for raw in raw_chunks:
        metadata = dict(raw.get("metadata") or {})
        page_number = int(metadata.get("page_number") or len(page_chunks) + 1)
        text = (raw.get("text") or "").strip()
        page_chunks.append(PageChunk(page_number=page_number, text=text, metadata=metadata))

    if not page_chunks:
        raise ExtractionError("PyMuPDF4LLM returned no page chunks.")
    return page_chunks


def _extract_full_markdown(
    prepared: PreparedInput,
    page_chunks: list[PageChunk],
    requested_engine: str,
    warnings: list[str],
) -> tuple[str, str, bool, list[dict[str, Any]]]:
    processing_format = prepared.processing_format

    if requested_engine == "docling" and processing_format != "pdf":
        raise UnsupportedInputError("The docling engine in this tool only supports PDF inputs.")

    if processing_format == "epub":
        markdown = _join_page_chunks(page_chunks)
        if not markdown.strip():
            raise ExtractionError("PyMuPDF4LLM returned empty EPUB Markdown.")
        return markdown, "pymupdf4llm", False, []

    if requested_engine == "pymupdf4llm":
        markdown = _join_page_chunks(page_chunks)
        if not markdown.strip():
            raise ExtractionError("PyMuPDF4LLM returned empty Markdown.")
        return markdown, "pymupdf4llm", False, []

    try:
        markdown, heading_hints = _extract_full_markdown_with_docling(prepared.working_path)
        if markdown.strip():
            return markdown, "docling", False, heading_hints
        raise ExtractionError("Docling returned empty Markdown.")
    except Exception as exc:  # noqa: BLE001 - we need a robust fallback boundary.
        if requested_engine == "docling":
            raise ExtractionError(f"Docling extraction failed: {exc}") from exc
        markdown = _join_page_chunks(page_chunks)
        if not markdown.strip():
            raise ExtractionError(f"Docling failed and PyMuPDF4LLM fallback was empty: {exc}") from exc
        warnings.append(f"Docling failed; fell back to PyMuPDF4LLM. Detail: {exc}")
        return markdown, "pymupdf4llm", True, []


def _extract_full_markdown_with_docling(path: Path) -> tuple[str, list[dict[str, Any]]]:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    markdown = result.document.export_to_markdown()
    heading_hints: list[dict[str, Any]] = []
    for item, level in result.document.iterate_items():
        label = str(getattr(item, "label", ""))
        if label not in {"title", "section_header"}:
            continue
        text = _clean_title(str(getattr(item, "text", "")), "")
        if not text:
            continue
        prov = getattr(item, "prov", None) or []
        page_no = None
        for provenance in prov:
            page_no = getattr(provenance, "page_no", None)
            if page_no is not None:
                break
        heading_hints.append(
            {
                "text": text,
                "level": int(level),
                "page": int(page_no) if page_no is not None else None,
            }
        )
    return markdown.strip(), heading_hints


def _join_page_chunks(page_chunks: list[PageChunk]) -> str:
    parts = [chunk.text.strip() for chunk in page_chunks if chunk.text.strip()]
    return "\n\n".join(parts).strip() + "\n" if parts else ""


def _build_chapters(
    full_markdown: str,
    toc: list[dict[str, Any]],
    page_chunks: list[PageChunk],
    default_title: str,
    heading_hints: list[dict[str, Any]],
) -> list[Chapter]:
    chapters = _build_chapters_from_toc(toc=toc, page_chunks=page_chunks)
    if chapters:
        return chapters

    chapters = _build_chapters_from_headings(
        full_markdown=full_markdown,
        page_chunks=page_chunks,
        default_title=default_title,
        heading_hints=heading_hints,
    )
    if chapters:
        return chapters

    return [
        Chapter(
            index=0,
            title="Document",
            slug="document",
            markdown=full_markdown.strip() + "\n",
            page_start=1,
            page_end=max(1, len(page_chunks)),
            origin="document",
        )
    ]


def _build_chapters_from_toc(toc: list[dict[str, Any]], page_chunks: list[PageChunk]) -> list[Chapter]:
    page_count = len(page_chunks)
    if page_count == 0:
        return []

    top_level = []
    seen_keys: set[tuple[str, int]] = set()
    for entry in toc:
        if int(entry["level"]) != 1:
            continue
        page = max(1, min(page_count, int(entry["page"])))
        title = _clean_title(str(entry["title"]), "")
        if not title:
            continue
        key = (title.casefold(), page)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        top_level.append({"title": title, "page": page})

    if not top_level:
        return []

    chapters: list[Chapter] = []
    if top_level[0]["page"] > 1:
        preamble_md = _join_page_range(page_chunks, 1, top_level[0]["page"] - 1)
        if preamble_md.strip():
            chapters.append(
                Chapter(
                    index=0,
                    title="Preamble",
                    slug="preamble",
                    markdown=preamble_md,
                    page_start=1,
                    page_end=top_level[0]["page"] - 1,
                    origin="toc-preamble",
                )
            )

    next_index = 1
    for idx, entry in enumerate(top_level):
        start_page = entry["page"]
        next_page = top_level[idx + 1]["page"] if idx + 1 < len(top_level) else page_count + 1
        end_page = max(start_page, min(page_count, next_page - 1))
        markdown = _join_page_range(page_chunks, start_page, end_page)
        if not markdown.strip():
            continue
        title = _clean_title(entry["title"], f"Chapter {next_index}")
        chapters.append(
            Chapter(
                index=next_index,
                title=title,
                slug=_make_slug(title, f"chapter-{next_index}"),
                markdown=markdown,
                page_start=start_page,
                page_end=end_page,
                origin="toc",
            )
        )
        next_index += 1

    return chapters


def _build_chapters_from_headings(
    full_markdown: str,
    page_chunks: list[PageChunk],
    default_title: str,
    heading_hints: list[dict[str, Any]],
) -> list[Chapter]:
    lines = full_markdown.splitlines()
    headings: list[tuple[int, int, str]] = []
    for line_number, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if not match:
            continue
        title = _clean_title(match.group(2), "")
        if title:
            headings.append((line_number, len(match.group(1)), title))

    if not headings:
        return []

    target_level = min(level for _, level, _ in headings)
    selected = [(line_no, title) for line_no, level, title in headings if level == target_level]
    if len(selected) < 2:
        return []

    titles = [title for _, title in selected]
    inferred_pages = _infer_heading_page_starts(
        titles=titles,
        page_chunks=page_chunks,
        heading_hints=heading_hints,
    )

    chapters: list[Chapter] = []
    first_known_page = next((page for page in inferred_pages if page is not None), 1)
    if selected[0][0] > 0:
        preamble_md = "\n".join(lines[: selected[0][0]]).strip()
        if preamble_md:
            chapters.append(
                Chapter(
                    index=0,
                    title="Preamble",
                    slug="preamble",
                    markdown=preamble_md + "\n",
                    page_start=1,
                    page_end=max(1, (first_known_page or 1) - 1),
                    origin="heading-preamble",
                )
            )

    next_index = 1
    page_count = max(1, len(page_chunks))
    for idx, (line_number, title) in enumerate(selected):
        end_line = selected[idx + 1][0] if idx + 1 < len(selected) else len(lines)
        markdown = "\n".join(lines[line_number:end_line]).strip()
        if not markdown:
            continue

        page_start = inferred_pages[idx] or (chapters[-1].page_end if chapters else 1)
        next_page = None
        for candidate in inferred_pages[idx + 1 :]:
            if candidate is not None and candidate >= page_start:
                next_page = candidate
                break
        page_end = next_page - 1 if next_page is not None and next_page > page_start else page_count

        clean_title = _clean_title(title, default_title or f"Chapter {next_index}")
        chapters.append(
            Chapter(
                index=next_index,
                title=clean_title,
                slug=_make_slug(clean_title, f"chapter-{next_index}"),
                markdown=markdown + "\n",
                page_start=max(1, page_start),
                page_end=max(max(1, page_start), page_end),
                origin="heading",
            )
        )
        next_index += 1

    return chapters


def _infer_heading_page_starts(
    titles: list[str],
    page_chunks: list[PageChunk],
    heading_hints: list[dict[str, Any]],
) -> list[int | None]:
    hints_cursor = 0
    normalized_pages = [_normalize_search_text(chunk.text) for chunk in page_chunks]
    results: list[int | None] = []
    search_start = 0

    for title in titles:
        needle = _normalize_search_text(title)
        found_page: int | None = None

        while hints_cursor < len(heading_hints):
            hint = heading_hints[hints_cursor]
            hints_cursor += 1
            hint_text = _normalize_search_text(str(hint.get("text", "")))
            hint_page = hint.get("page")
            if hint_text != needle:
                continue
            if hint_page is not None:
                found_page = int(hint_page)
                break

        if needle and found_page is None:
            for page_index in range(search_start, len(normalized_pages)):
                if needle in normalized_pages[page_index]:
                    found_page = page_index + 1
                    break
            if found_page is None:
                for page_index, normalized_page in enumerate(normalized_pages):
                    if needle in normalized_page:
                        found_page = page_index + 1
                        break

        results.append(found_page)
        if found_page is not None:
            search_start = max(0, found_page - 1)

    return results


def _build_chunks_for_chapter(
    chapter: Chapter,
    chunk_target: int,
    chunk_overlap: int,
) -> list[ChunkRecord]:
    blocks = _split_markdown_blocks(chapter.markdown, chapter.title)
    normalized_blocks: list[Block] = []
    for block in blocks:
        if block.token_count > chunk_target and block.kind in {"paragraph", "list"}:
            normalized_blocks.extend(_split_oversized_block(block, chunk_target))
        else:
            normalized_blocks.append(block)

    chunks: list[ChunkRecord] = []
    if not normalized_blocks:
        return chunks

    start_index = 0
    chunk_index = 1
    while start_index < len(normalized_blocks):
        current_blocks: list[Block] = []
        total_tokens = 0
        index = start_index

        while index < len(normalized_blocks):
            block = normalized_blocks[index]
            if current_blocks and total_tokens + block.token_count > chunk_target:
                if all(existing.kind == "heading" for existing in current_blocks):
                    current_blocks.append(block)
                    total_tokens += block.token_count
                    index += 1
                break
            current_blocks.append(block)
            total_tokens += block.token_count
            index += 1
            if total_tokens >= chunk_target:
                break

        if not current_blocks:
            current_blocks = [normalized_blocks[start_index]]
            index = start_index + 1
            total_tokens = current_blocks[0].token_count

        chunk = _render_chunk_record(
            chapter=chapter,
            chunk_index=chunk_index,
            blocks=current_blocks,
            oversize=any(block.token_count > chunk_target for block in current_blocks) or total_tokens > chunk_target,
        )
        chunks.append(chunk)
        chunk_index += 1

        if index >= len(normalized_blocks):
            break

        overlap_tokens = 0
        overlap_count = 0
        cursor = len(current_blocks) - 1
        while cursor >= 0 and overlap_tokens < chunk_overlap:
            overlap_tokens += current_blocks[cursor].token_count
            overlap_count += 1
            cursor -= 1
        next_start = index - overlap_count if chunk_overlap > 0 else index
        start_index = max(start_index + 1, next_start)

    return chunks


def _render_chunk_record(
    chapter: Chapter,
    chunk_index: int,
    blocks: list[Block],
    oversize: bool,
) -> ChunkRecord:
    breadcrumb = _dedupe_path(blocks[0].path if blocks else [chapter.title])
    body = "\n\n".join(block.text.strip() for block in blocks if block.text.strip()).strip()
    if body:
        text = f"_Path: {' > '.join(breadcrumb)}_\n\n{body}\n"
    else:
        text = f"_Path: {' > '.join(breadcrumb)}_\n"

    return ChunkRecord(
        chapter_index=chapter.index,
        chapter_title=chapter.title,
        chunk_index=chunk_index,
        breadcrumb=breadcrumb,
        text=text,
        token_count=_count_tokens(text),
        oversize=oversize,
        page_start=chapter.page_start,
        page_end=chapter.page_end,
    )


def _split_markdown_blocks(markdown: str, chapter_title: str) -> list[Block]:
    lines = markdown.splitlines()
    heading_stack: list[str] = []
    blocks: list[Block] = []
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        stripped = line.strip()

        if not stripped:
            line_index += 1
            continue

        fence = _code_fence(stripped)
        if fence:
            end_index = line_index + 1
            while end_index < len(lines):
                if lines[end_index].strip().startswith(fence):
                    end_index += 1
                    break
                end_index += 1
            text = "\n".join(lines[line_index:end_index]).strip()
            blocks.append(
                Block(
                    kind="code",
                    text=text,
                    path=_dedupe_path([chapter_title, *heading_stack]),
                    token_count=_count_tokens(text),
                )
            )
            line_index = end_index
            continue

        match = HEADING_RE.match(stripped)
        if match:
            level = len(match.group(1))
            title = _clean_title(match.group(2), "Section")
            heading_stack = heading_stack[: max(level - 1, 0)]
            if len(heading_stack) == level - 1:
                heading_stack.append(title)
            else:
                heading_stack = [title]
            text = stripped
            blocks.append(
                Block(
                    kind="heading",
                    text=text,
                    path=_dedupe_path([chapter_title, *heading_stack]),
                    token_count=_count_tokens(text),
                    heading_level=level,
                )
            )
            line_index += 1
            continue

        if _is_table_line(stripped):
            end_index = line_index + 1
            while end_index < len(lines) and _is_table_line(lines[end_index].strip()):
                end_index += 1
            text = "\n".join(lines[line_index:end_index]).strip()
            blocks.append(
                Block(
                    kind="table",
                    text=text,
                    path=_dedupe_path([chapter_title, *heading_stack]),
                    token_count=_count_tokens(text),
                )
            )
            line_index = end_index
            continue

        if LIST_RE.match(line):
            end_index = line_index + 1
            while end_index < len(lines):
                candidate = lines[end_index]
                if not candidate.strip():
                    break
                if LIST_RE.match(candidate) or candidate.startswith((" ", "\t")):
                    end_index += 1
                    continue
                break
            text = "\n".join(lines[line_index:end_index]).strip()
            blocks.append(
                Block(
                    kind="list",
                    text=text,
                    path=_dedupe_path([chapter_title, *heading_stack]),
                    token_count=_count_tokens(text),
                )
            )
            line_index = end_index
            continue

        end_index = line_index + 1
        while end_index < len(lines):
            candidate = lines[end_index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if _code_fence(candidate_stripped) or HEADING_RE.match(candidate_stripped):
                break
            if _is_table_line(candidate_stripped) or LIST_RE.match(candidate):
                break
            end_index += 1
        text = "\n".join(lines[line_index:end_index]).strip()
        blocks.append(
            Block(
                kind="paragraph",
                text=text,
                path=_dedupe_path([chapter_title, *heading_stack]),
                token_count=_count_tokens(text),
            )
        )
        line_index = end_index

    return blocks


def _split_oversized_block(block: Block, chunk_target: int) -> list[Block]:
    sentences = [segment.strip() for segment in SENTENCE_SPLIT_RE.split(block.text.strip()) if segment.strip()]
    if len(sentences) <= 1:
        return _slice_block_by_tokens(block, chunk_target)

    pieces: list[Block] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join(current + [sentence]).strip()
        if current and _count_tokens(candidate) > chunk_target:
            text = " ".join(current).strip()
            pieces.extend(_finalize_split_piece(block, text, chunk_target))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        text = " ".join(current).strip()
        pieces.extend(_finalize_split_piece(block, text, chunk_target))
    return pieces


def _finalize_split_piece(block: Block, text: str, chunk_target: int) -> list[Block]:
    piece = Block(
        kind=block.kind,
        text=text,
        path=list(block.path),
        token_count=_count_tokens(text),
        heading_level=block.heading_level,
    )
    if piece.token_count <= chunk_target:
        return [piece]
    return _slice_block_by_tokens(piece, chunk_target)


def _slice_block_by_tokens(block: Block, chunk_target: int) -> list[Block]:
    tokenizer_name, encoder = _get_encoder()
    _ = tokenizer_name
    encoded = encoder.encode(block.text)
    pieces: list[Block] = []
    start = 0
    while start < len(encoded):
        stop = min(len(encoded), start + chunk_target)
        text = encoder.decode(encoded[start:stop]).strip()
        if text:
            pieces.append(
                Block(
                    kind=block.kind,
                    text=text,
                    path=list(block.path),
                    token_count=_count_tokens(text),
                    heading_level=block.heading_level,
                )
            )
        start = stop
    return pieces or [block]


def _write_outputs(
    output_root: Path,
    prepared: PreparedInput,
    info: DocumentInfo,
    full_markdown: str,
    full_engine: str,
    requested_engine: str,
    fallback_used: bool,
    chunk_target: int,
    chunk_overlap: int,
    tokenizer_name: str,
    chapters: list[Chapter],
    chunks: list[ChunkRecord],
    warnings: list[str],
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    chapters_dir = output_root / "chapters"
    chunks_dir = output_root / "chunks"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    document_md = full_markdown.strip() + "\n"
    (output_root / "document.md").write_text(document_md, encoding="utf-8")

    chunk_lookup: dict[int, list[ChunkRecord]] = {}
    for chunk in chunks:
        chunk_lookup.setdefault(chunk.chapter_index, []).append(chunk)

    for chapter in chapters:
        chapter_filename = f"{chapter.index:02d}-{chapter.slug}.md"
        chapter.relative_path = str(Path("chapters") / chapter_filename)
        (output_root / chapter.relative_path).write_text(chapter.markdown.strip() + "\n", encoding="utf-8")

        chapter_dir = chunks_dir / f"{chapter.index:02d}-{chapter.slug}"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        for chunk in chunk_lookup.get(chapter.index, []):
            chunk.relative_path = str(Path("chunks") / chapter_dir.name / f"{chunk.chunk_index:04d}.md")
            (output_root / chunk.relative_path).write_text(chunk.text, encoding="utf-8")

    index_path = chunks_dir / "index.jsonl"
    with index_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {
                        "chapter_index": chunk.chapter_index,
                        "chapter_title": chunk.chapter_title,
                        "chunk_index": chunk.chunk_index,
                        "relative_path": chunk.relative_path,
                        "token_count": chunk.token_count,
                        "oversize": chunk.oversize,
                        "breadcrumb": chunk.breadcrumb,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    manifest = {
        "source": {
            "path": str(prepared.original_path),
            "processing_path": str(prepared.working_path),
            "input_format": prepared.input_format,
            "processing_format": prepared.processing_format,
            "page_count": info.page_count,
            "metadata": info.metadata,
        },
        "engine": {
            "requested": requested_engine,
            "full_markdown_engine": full_engine,
            "page_engine": "pymupdf4llm",
            "fallback_used": fallback_used,
        },
        "chunking": {
            "target_tokens": chunk_target,
            "overlap_tokens": chunk_overlap,
            "tokenizer": tokenizer_name,
        },
        "chapters": [
            {
                "index": chapter.index,
                "title": chapter.title,
                "slug": chapter.slug,
                "origin": chapter.origin,
                "page_start": chapter.page_start,
                "page_end": chapter.page_end,
                "relative_path": chapter.relative_path,
                "chunk_count": chapter.chunk_count,
            }
            for chapter in chapters
        ],
        "warnings": warnings,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _join_page_range(page_chunks: list[PageChunk], start_page: int, end_page: int) -> str:
    snippets = [
        page_chunk.text.strip()
        for page_chunk in page_chunks
        if start_page <= page_chunk.page_number <= end_page and page_chunk.text.strip()
    ]
    return "\n\n".join(snippets).strip() + "\n" if snippets else ""


def _clean_title(title: str, fallback: str) -> str:
    cleaned = " ".join(title.replace("\u00a0", " ").split()).strip(" -:#")
    return cleaned or fallback


def _make_slug(title: str, fallback: str) -> str:
    slug = slugify(title, max_length=80, separator="-")
    return slug or fallback


def _normalize_search_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip().lower()
    return re.sub(r"[^a-z0-9 ]+", "", collapsed)


def _dedupe_path(path: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in path:
        if not item:
            continue
        if deduped and deduped[-1] == item:
            continue
        deduped.append(item)
    return deduped or ["Document"]


def _is_table_line(stripped_line: str) -> bool:
    return stripped_line.startswith("|")


def _code_fence(stripped_line: str) -> str | None:
    if stripped_line.startswith("```"):
        return "```"
    if stripped_line.startswith("~~~"):
        return "~~~"
    return None


_ENCODER_CACHE: tuple[str, tiktoken.Encoding] | None = None


def _get_encoder() -> tuple[str, tiktoken.Encoding]:
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE
    for encoding_name in ("o200k_base", "cl100k_base"):
        try:
            _ENCODER_CACHE = (encoding_name, tiktoken.get_encoding(encoding_name))
            return _ENCODER_CACHE
        except KeyError:
            continue
    raise Pdf2MdError("No supported tokenizer encoding found in tiktoken.")


def _count_tokens(text: str) -> int:
    _, encoder = _get_encoder()
    return len(encoder.encode(text, disallowed_special=()))
