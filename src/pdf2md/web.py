from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pipeline import Pdf2MdError, SUPPORTED_INPUTS, run_conversion


@dataclass
class WebConversionResult:
    source_name: str
    document_markdown: str
    manifest: dict[str, Any]
    archive_bytes: bytes
    archive_name: str
    chapter_markdowns: list[tuple[str, str]]


def convert_document_bytes(
    file_bytes: bytes,
    filename: str,
    chunk_target: int = 1000,
    chunk_overlap: int = 120,
    engine: str = "auto",
) -> WebConversionResult:
    source_name = _normalize_uploaded_name(filename)

    with tempfile.TemporaryDirectory(prefix="pdf2md-web-") as tempdir_name:
        tempdir = Path(tempdir_name)
        input_path = tempdir / source_name
        input_path.write_bytes(file_bytes)

        result = run_conversion(
            input_path=input_path,
            outdir=tempdir / "outputs",
            chunk_target=chunk_target,
            chunk_overlap=chunk_overlap,
            engine=engine,
        )

        manifest = _sanitize_manifest(result.manifest, source_name)
        (result.output_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        document_markdown = (result.output_root / "document.md").read_text(encoding="utf-8")
        chapter_markdowns = _read_chapter_markdowns(result.output_root, manifest)
        archive_bytes = _build_archive_bytes(result.output_root)

    return WebConversionResult(
        source_name=source_name,
        document_markdown=document_markdown,
        manifest=manifest,
        archive_bytes=archive_bytes,
        archive_name=f"{Path(source_name).stem}.zip",
        chapter_markdowns=chapter_markdowns,
    )


def render_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="pdf2md", layout="wide")
    st.title("pdf2md")
    st.write(
        "Sube un PDF o ebook y obtén el Markdown listo, junto con capítulos, chunks y manifest."
    )
    st.caption("La v1 corre localmente, conserva tablas y no hace OCR para PDFs escaneados.")

    with st.sidebar:
        st.header("Configuración")
        engine = st.selectbox(
            "Engine",
            options=("auto", "docling", "pymupdf4llm"),
            help="Docling intenta una extracción más estructurada; si falla, auto usa fallback.",
        )
        chunk_target = st.number_input(
            "Tokens por chunk",
            min_value=100,
            max_value=4000,
            value=1000,
            step=50,
        )
        chunk_overlap = st.number_input(
            "Overlap entre chunks",
            min_value=0,
            max_value=1000,
            value=120,
            step=10,
        )

    uploaded = st.file_uploader(
        "Documento de entrada",
        type=[suffix.lstrip(".") for suffix in sorted(SUPPORTED_INPUTS)],
        help="Soporta PDF, EPUB y AZW3. Para AZW3 se necesita Calibre si lo corres localmente.",
    )
    convert_clicked = st.button("Convertir a Markdown", type="primary", use_container_width=True)

    if convert_clicked:
        if uploaded is None:
            st.warning("Primero sube un archivo para convertir.")
        else:
            try:
                with st.spinner("Convirtiendo documento..."):
                    st.session_state["conversion"] = convert_document_bytes(
                        file_bytes=uploaded.getvalue(),
                        filename=uploaded.name,
                        chunk_target=int(chunk_target),
                        chunk_overlap=int(chunk_overlap),
                        engine=engine,
                    )
                st.session_state["conversion_error"] = None
            except Pdf2MdError as exc:
                st.session_state["conversion"] = None
                st.session_state["conversion_error"] = str(exc)

    error_message = st.session_state.get("conversion_error")
    if error_message:
        st.error(error_message)

    conversion: WebConversionResult | None = st.session_state.get("conversion")
    if conversion is None:
        st.info("Cuando conviertas un archivo, aquí aparecerán el preview y las descargas.")
        return

    manifest = conversion.manifest
    chapter_count = len(manifest["chapters"])
    total_chunks = sum(int(chapter["chunk_count"]) for chapter in manifest["chapters"])

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Páginas", int(manifest["source"]["page_count"]))
    metric_2.metric("Capítulos", chapter_count)
    metric_3.metric("Chunks", total_chunks)
    metric_4.metric("Engine", str(manifest["engine"]["full_markdown_engine"]))

    download_1, download_2 = st.columns(2)
    download_1.download_button(
        "Descargar document.md",
        data=conversion.document_markdown,
        file_name=f"{Path(conversion.source_name).stem}.md",
        mime="text/markdown",
        use_container_width=True,
    )
    download_2.download_button(
        "Descargar bundle .zip",
        data=conversion.archive_bytes,
        file_name=conversion.archive_name,
        mime="application/zip",
        use_container_width=True,
    )

    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            st.warning(warning)

    tab_markdown, tab_chapters, tab_manifest = st.tabs(["Markdown", "Capítulos", "Manifest"])

    with tab_markdown:
        st.text_area(
            "Preview de document.md",
            value=conversion.document_markdown,
            height=480,
        )

    with tab_chapters:
        if not conversion.chapter_markdowns:
            st.info("No se detectaron capítulos separados para este documento.")
        else:
            chapter_titles = [title for title, _ in conversion.chapter_markdowns]
            selected_title = st.selectbox("Capítulo", chapter_titles)
            selected_markdown = dict(conversion.chapter_markdowns)[selected_title]
            st.text_area(
                "Preview del capítulo",
                value=selected_markdown,
                height=420,
            )

    with tab_manifest:
        st.json(manifest, expanded=False)


def main() -> int:
    from streamlit.web import cli as stcli

    script_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(script_path)]
    return stcli.main()


def _normalize_uploaded_name(filename: str) -> str:
    raw_name = Path(filename or "document.pdf").name
    suffix = Path(raw_name).suffix.lower()
    if suffix in SUPPORTED_INPUTS:
        return raw_name
    if not suffix:
        return f"{raw_name}.pdf"
    return raw_name


def _sanitize_manifest(manifest: dict[str, Any], source_name: str) -> dict[str, Any]:
    cleaned = json.loads(json.dumps(manifest, ensure_ascii=False))
    cleaned["source"]["path"] = source_name

    input_format = str(cleaned["source"]["input_format"])
    processing_format = str(cleaned["source"]["processing_format"])
    if input_format == processing_format:
        cleaned["source"]["processing_path"] = source_name
    else:
        cleaned["source"]["processing_path"] = f"{Path(source_name).stem}.{processing_format}"
    return cleaned


def _read_chapter_markdowns(
    output_root: Path,
    manifest: dict[str, Any],
) -> list[tuple[str, str]]:
    chapter_markdowns: list[tuple[str, str]] = []
    for chapter in manifest["chapters"]:
        relative_path = chapter.get("relative_path")
        if not relative_path:
            continue
        chapter_path = output_root / str(relative_path)
        if not chapter_path.exists():
            continue
        chapter_markdowns.append(
            (
                str(chapter["title"]),
                chapter_path.read_text(encoding="utf-8"),
            )
        )
    return chapter_markdowns


def _build_archive_bytes(output_root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_root.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, arcname=str(Path(output_root.name) / path.relative_to(output_root)))
    return buffer.getvalue()


if __name__ == "__main__":
    render_app()
