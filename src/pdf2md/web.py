from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slugify import slugify

from .pipeline import Pdf2MdError, SUPPORTED_INPUTS, run_conversion


UPLOAD_FORMAT_ORDER = (".pdf", ".epub", ".docx", ".azw3")
FORMAT_LABELS = {
    ".pdf": "PDF",
    ".epub": "EPUB",
    ".docx": "Word (.docx)",
    ".azw3": "Kindle (.azw3)",
}
OUTPUT_GOALS: dict[str, dict[str, str]] = {
    "Markdown limpio": {
        "kicker": "Solo el texto bien puesto",
        "summary": "Quédate con el `document.md` principal y una vista fácil de leer.",
        "note": "La interfaz priorizará el Markdown principal y su descarga directa.",
    },
    "Capítulos navegables": {
        "kicker": "Trabajar por secciones",
        "summary": "Ideal si quieres revisar el documento por partes sin perder estructura.",
        "note": "La interfaz destacará la navegación y descarga por capítulos.",
    },
    "Bundle para RAG": {
        "kicker": "Llevarlo a agentes o pipelines",
        "summary": "Usa chunks, manifest y el bundle completo listo para automatizar.",
        "note": "La interfaz pondrá primero el bundle técnico y el contexto estructurado.",
    },
}


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
    fast_mode: bool = False,
    page_group_size: int = 30,
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
            fast_mode=fast_mode,
            page_group_size=page_group_size,
            output_name=Path(source_name).stem,
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
        archive_name=f"{manifest['output']['bundle_name']}.zip",
        chapter_markdowns=chapter_markdowns,
    )


def render_app() -> None:
    import streamlit as st

    st.set_page_config(
        page_title="pdf2md",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_theme_css(st)

    st.session_state.setdefault("conversion", None)
    st.session_state.setdefault("conversion_error", None)

    _render_header(st)

    left_col, right_col = st.columns((0.88, 1.12), gap="large")
    with left_col:
        with st.form("convert-form"):
            uploaded = _render_upload_panel(st)
            output_goal, fast_mode, page_group_size, engine, chunk_target, chunk_overlap = _render_controls_panel(st)

            convert_clicked = st.form_submit_button(
                "Convertir documento",
                type="primary",
                use_container_width=True,
            )

    if convert_clicked:
        if uploaded is None:
            st.session_state["conversion"] = None
            st.session_state["conversion_error"] = "Primero sube un archivo para convertir."
        else:
            try:
                with st.spinner("Convirtiendo documento..."):
                    st.session_state["conversion"] = convert_document_bytes(
                        file_bytes=uploaded.getvalue(),
                        filename=uploaded.name,
                        chunk_target=int(chunk_target),
                        chunk_overlap=int(chunk_overlap),
                        engine=engine,
                        fast_mode=fast_mode,
                        page_group_size=int(page_group_size),
                    )
                st.session_state["conversion_error"] = None
            except Pdf2MdError as exc:
                st.session_state["conversion"] = None
                st.session_state["conversion_error"] = str(exc)

    with right_col:
        error_message = st.session_state.get("conversion_error")
        if error_message:
            st.error(error_message)

        conversion: WebConversionResult | None = st.session_state.get("conversion")
        if conversion is None:
            _render_empty_state(st, output_goal)
            return

        _render_results(st, conversion, output_goal)


def main() -> int:
    from streamlit.web import cli as stcli

    script_path = Path(__file__).resolve()
    sys.argv = ["streamlit", "run", str(script_path)]
    return stcli.main()


def _render_header(st: Any) -> None:
    st.markdown(
        """
        <div class="app-header">
          <span class="eyebrow">pdf2md</span>
          <h1>PDF, EPUB o Word a Markdown. En una sola pantalla.</h1>
          <p>Sube el archivo, elige el tipo de salida y trabaja en dark mode sin una interfaz cargada.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_upload_panel(st: Any) -> Any:
    st.markdown(
        """
        <div class="section-heading">
          <span class="section-label">Archivo</span>
          <h3>Sube tu documento</h3>
          <p>PDF, EPUB, Word (.docx) o AZW3.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    uploaded = st.file_uploader(
        "Documento de entrada",
        type=[suffix.lstrip(".") for suffix in UPLOAD_FORMAT_ORDER if suffix in SUPPORTED_INPUTS],
        label_visibility="collapsed",
        help="Soporta PDF, EPUB, Word (.docx) y AZW3. Para AZW3 se necesita Calibre si lo corres localmente.",
    )

    st.markdown(_format_badges_html(), unsafe_allow_html=True)

    if uploaded is None:
        st.caption("Si el PDF está escaneado como imagen, esta versión todavía no hace OCR.")
        return None

    file_kind = FORMAT_LABELS.get(Path(uploaded.name).suffix.lower(), Path(uploaded.name).suffix.upper())
    st.markdown(
        f"""
        <div class="uploaded-card">
          <span class="section-label">Seleccionado</span>
          <strong>{uploaded.name}</strong>
          <p>{file_kind} · {_human_size(uploaded.size)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return uploaded


def _render_controls_panel(st: Any) -> tuple[str, bool, int, str, int, int]:
    st.markdown(
        """
        <div class="section-heading">
          <span class="section-label">Salida</span>
          <h3>Escoge qué quieres :)</h3>
          <p>Esto solo cambia qué resultado te priorizo al mostrarlo.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    output_goal = st.radio(
        "Objetivo del output",
        options=list(OUTPUT_GOALS),
        horizontal=True,
        label_visibility="collapsed",
    )
    selected_goal = OUTPUT_GOALS[output_goal]
    st.caption(selected_goal["note"])

    with st.expander("Ajustes avanzados", expanded=False):
        fast_mode = st.toggle(
            "Fast mode",
            value=True,
            help="Usa PyMuPDF4LLM y fuerza secciones por páginas para priorizar velocidad.",
        )
        if fast_mode:
            page_group_size = st.number_input(
                "Páginas por Markdown",
                min_value=1,
                max_value=500,
                value=30,
                step=1,
            )
            engine = "pymupdf4llm"
            chunk_target = 1000
            chunk_overlap = 120
            st.caption("Fast mode usa PyMuPDF4LLM, agrupa por páginas y sigue generando chunks.")
        else:
            page_group_size = 30
            engine = st.selectbox(
                "Engine",
                options=("pymupdf4llm", "auto", "docling"),
                help="`pymupdf4llm` es el modo más rápido. `auto` prioriza estructura, pero tarda más.",
            )
            number_col_1, number_col_2 = st.columns(2, gap="small")
            with number_col_1:
                chunk_target = st.number_input(
                    "Tokens por chunk",
                    min_value=100,
                    max_value=4000,
                    value=1000,
                    step=50,
                )
            with number_col_2:
                chunk_overlap = st.number_input(
                    "Overlap",
                    min_value=0,
                    max_value=1000,
                    value=120,
                    step=10,
                )

    return output_goal, fast_mode, int(page_group_size), engine, int(chunk_target), int(chunk_overlap)


def _render_empty_state(st: Any, output_goal: str) -> None:
    note = OUTPUT_GOALS[output_goal]["note"]
    st.markdown(
        f"""
        <div class="result-card empty-state-card">
          <span class="section-label">Resultado</span>
          <h3>El resultado aparece aquí.</h3>
          <p>{note}</p>
          <p>Preview, descargas y secciones sin tener que bajar la página.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_results(st: Any, conversion: WebConversionResult, output_goal: str) -> None:
    manifest = conversion.manifest
    chapter_lookup = dict(conversion.chapter_markdowns)
    chapter_titles = list(chapter_lookup)
    selected_chapter_title = chapter_titles[0] if chapter_titles else None
    selected_chapter_markdown = chapter_lookup.get(selected_chapter_title or "")
    total_chunks = sum(int(chapter["chunk_count"]) for chapter in manifest["chapters"])
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    processing = manifest.get("processing", {})
    segmentation_mode = str(processing.get("segmentation_mode") or "chapters")
    section_label = "Bloques" if segmentation_mode == "page-batch" else "Capítulos"
    single_section_label = "bloque" if segmentation_mode == "page-batch" else "capítulo"

    st.markdown(
        f"""
        <div class="result-card success-banner">
          <span class="section-label">Listo</span>
          <h2>{conversion.source_name} ya está convertido.</h2>
          <p>{OUTPUT_GOALS[output_goal]["note"]}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        _render_metric_strip_html(
            (
                ("Páginas", str(manifest["source"]["page_count"])),
                (section_label, str(len(manifest["chapters"]))),
                ("Chunks", str(total_chunks)),
                ("Engine", str(manifest["engine"]["full_markdown_engine"])),
            )
        ),
        unsafe_allow_html=True,
    )

    if chapter_titles:
        selected_chapter_title = st.selectbox(
            f"{section_label} para descarga rápida",
            options=chapter_titles,
            index=0,
            label_visibility="collapsed",
        )
        selected_chapter_markdown = chapter_lookup[selected_chapter_title]

    download_cols = st.columns(3, gap="small")
    download_cols[0].download_button(
        "Descargar document.md",
        data=conversion.document_markdown,
        file_name=f"{Path(conversion.source_name).stem}.md",
        mime="text/markdown",
        use_container_width=True,
    )
    if selected_chapter_title and selected_chapter_markdown:
        download_cols[1].download_button(
            f"Descargar {single_section_label}",
            data=selected_chapter_markdown,
            file_name=f"{Path(conversion.source_name).stem}-{slugify(selected_chapter_title)}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        download_cols[1].markdown(
            """
            <div class="download-placeholder">
              <strong>Sección individual</strong>
              <p>Este documento no quedó separado en archivos intermedios.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    download_cols[2].download_button(
        "Descargar bundle .zip",
        data=conversion.archive_bytes,
        file_name=conversion.archive_name,
        mime="application/zip",
        use_container_width=True,
    )

    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            st.markdown(
                f"""
                <div class="warning-card">
                  <strong>Ojo</strong>
                  <p>{warning}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    tab_markdown, tab_chapters, tab_manifest = st.tabs(["Markdown", section_label, "Manifest"])

    with tab_markdown:
        st.text_area(
            "Markdown crudo",
            value=conversion.document_markdown,
            height=360,
            label_visibility="collapsed",
        )

    with tab_chapters:
        if not chapter_titles:
            st.info("No se detectaron secciones separadas para este documento.")
        else:
            chapter_title = st.selectbox(
                f"{section_label} para revisar",
                options=chapter_titles,
                index=0,
                key="chapter-preview-select",
            )
            chapter_markdown = chapter_lookup[chapter_title]
            chapter_col_1, chapter_col_2 = st.columns(2, gap="large")
            with chapter_col_1:
                st.caption(f"Markdown del {single_section_label}")
                st.text_area(
                    "Chapter markdown",
                    value=chapter_markdown,
                    height=320,
                    label_visibility="collapsed",
                    key="chapter-markdown-preview",
                )
            with chapter_col_2:
                st.caption(f"{section_label} seleccionado")
                st.text_area(
                    "Selected chapter",
                    value=chapter_markdown,
                    height=320,
                    label_visibility="collapsed",
                    key="chapter-markdown-preview-side",
                )

    with tab_manifest:
        st.text_area(
            "Manifest",
            value=manifest_text,
            height=360,
            label_visibility="collapsed",
        )


def _render_metric_strip_html(items: tuple[tuple[str, str], ...]) -> str:
    badges = "".join(
        f"""
        <div class="metric-pill">
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
        """
        for label, value in items
    )
    return f'<div class="metric-strip">{badges}</div>'


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
    cleaned["output"]["root_path"] = cleaned["output"]["bundle_name"]

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


def _format_badges_html() -> str:
    badges = "".join(
        f'<span class="format-pill">{FORMAT_LABELS[suffix]}</span>'
        for suffix in UPLOAD_FORMAT_ORDER
        if suffix in SUPPORTED_INPUTS
    )
    return f'<div class="badge-row">{badges}</div>'


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _inject_theme_css(st: Any) -> None:
    st.markdown(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

          :root {
            --bg: #0b0f14;
            --surface: #10161d;
            --surface-strong: #141b23;
            --ink: #eef3fb;
            --muted: #8c97a8;
            --line: rgba(255, 255, 255, 0.08);
            --accent: #8ab4ff;
            --accent-soft: rgba(138, 180, 255, 0.12);
            --shadow: none;
            --radius-xl: 22px;
            --radius-lg: 16px;
            --radius-md: 12px;
          }

          .stApp {
            background:
              radial-gradient(circle at top, rgba(138, 180, 255, 0.08), transparent 26%),
              linear-gradient(180deg, #0b0f14 0%, #0d1117 100%);
            color: var(--ink);
          }

          .block-container {
            max-width: 1280px;
            padding-top: 0.9rem;
            padding-bottom: 0.8rem;
          }

          h1, h2, h3, h4 {
            font-family: "Inter", "Avenir Next", "Helvetica Neue", sans-serif;
            color: var(--ink);
            letter-spacing: -0.035em;
          }

          p, li, label, div, span {
            font-family: "Inter", "Avenir Next", "Helvetica Neue", sans-serif;
          }

          code, pre, textarea {
            font-family: "JetBrains Mono", "SFMono-Regular", monospace !important;
          }

          .app-header {
            margin-bottom: 0.85rem;
          }

          .app-header h1 {
            font-size: clamp(1.55rem, 2.8vw, 2.45rem);
            line-height: 1.02;
            margin: 0.25rem 0 0.4rem;
            max-width: 16ch;
          }

          .app-header p {
            max-width: 72ch;
            margin: 0;
            color: var(--muted);
            line-height: 1.45;
            font-size: 0.95rem;
          }

          .eyebrow,
          .section-label,
          .format-pill {
            display: inline-flex;
            align-items: center;
            padding: 0.3rem 0.65rem;
            border-radius: 999px;
            background: var(--accent-soft);
            color: var(--accent);
            font-size: 0.74rem;
            font-weight: 600;
            letter-spacing: 0.02em;
          }

          div[data-testid="stForm"] {
            border: 1px solid var(--line);
            border-radius: var(--radius-xl);
            background: var(--surface);
            padding: 1rem 1rem 0.9rem;
            box-shadow: var(--shadow);
            min-height: calc(100vh - 170px);
          }

          .result-card,
          .uploaded-card,
          .warning-card,
          .empty-state-card,
          .download-placeholder,
          .success-banner {
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            background: var(--surface-strong);
            box-shadow: var(--shadow);
          }

          .result-card {
            padding: 0.95rem 1rem;
          }

          .uploaded-card,
          .warning-card,
          .download-placeholder {
            padding: 0.85rem 0.95rem;
          }

          .uploaded-card strong,
          .warning-card strong,
          .download-placeholder strong {
            display: block;
            color: var(--ink);
            font-size: 0.96rem;
            margin-bottom: 0.18rem;
          }

          .uploaded-card p,
          .warning-card p,
          .download-placeholder p,
          .empty-state-card p,
          .success-banner p {
            margin: 0;
            color: var(--muted);
            line-height: 1.45;
            font-size: 0.9rem;
          }

          .section-heading {
            margin: 0 0 0.7rem;
          }

          .section-heading.compact {
            margin-top: 0.9rem;
          }

          .section-heading h3 {
            font-size: 1.02rem;
            margin: 0.35rem 0 0.12rem;
          }

          .section-heading p {
            margin: 0;
            color: var(--muted);
            line-height: 1.4;
            font-size: 0.9rem;
          }

          .badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            margin: 0.55rem 0 0.7rem;
          }

          .format-pill {
            background: transparent;
            color: var(--muted);
            border: 1px solid var(--line);
          }

          .uploaded-card {
            margin-top: 0.65rem;
          }

          .success-banner {
            margin: 0 0 0.7rem;
          }

          .success-banner h2 {
            margin: 0.38rem 0 0.16rem;
            font-size: clamp(1.1rem, 2vw, 1.45rem);
          }

          .metric-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            margin-bottom: 0.7rem;
          }

          .metric-pill {
            border: 1px solid var(--line);
            border-radius: 14px;
            background: var(--surface);
            padding: 0.7rem 0.8rem;
          }

          .metric-pill span {
            display: block;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--muted);
            margin-bottom: 0.32rem;
          }

          .metric-pill strong {
            display: block;
            font-size: 1rem;
            color: var(--ink);
          }

          .empty-state-card {
            min-height: calc(100vh - 170px);
            display: flex;
            flex-direction: column;
            justify-content: center;
          }

          .empty-state-card h3 {
            margin: 0.38rem 0 0.18rem;
            font-size: 1.18rem;
          }

          .warning-card {
            margin-top: 0.55rem;
          }

          .download-placeholder {
            height: 100%;
            min-height: 44px;
            display: flex;
            flex-direction: column;
            justify-content: center;
          }

          div[data-testid="stFileUploader"] section {
            background: #0d1319;
            border: 1px dashed rgba(255, 255, 255, 0.12);
            border-radius: 14px;
            padding: 0.9rem;
          }

          div[data-testid="stRadio"] > div {
            gap: 0.3rem;
          }

          div[data-testid="stRadio"] label p {
            font-size: 0.9rem;
          }

          div[data-testid="stButton"] > button,
          div[data-testid="stFormSubmitButton"] > button,
          div[data-testid="stDownloadButton"] > button {
            min-height: 40px;
            border-radius: 12px;
            border: 1px solid var(--line);
            background: #161d26;
            color: var(--ink);
            font-weight: 600;
            box-shadow: none;
          }

          div[data-testid="stFormSubmitButton"] > button[kind="primary"],
          div[data-testid="stButton"] > button[kind="primary"] {
            background: var(--ink);
            color: #0b0f14;
            border-color: transparent;
          }

          div[data-baseweb="select"] > div,
          div[data-testid="stNumberInput"] input,
          div[data-testid="stTextArea"] textarea {
            border-radius: 12px !important;
            background: #0d1319 !important;
            color: var(--ink) !important;
            border-color: var(--line) !important;
          }

          div[data-baseweb="select"] * {
            color: var(--ink) !important;
          }

          div[data-testid="stTabs"] {
            margin-top: 0.55rem;
            border: 1px solid var(--line);
            border-radius: 18px;
            background: var(--surface-strong);
            padding: 0.35rem 0.55rem 0.55rem;
            min-height: calc(100vh - 360px);
          }

          div[data-testid="stTabs"] button {
            font-weight: 600;
            color: var(--muted);
          }

          div[data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--ink);
          }

          div[data-testid="stAlert"] {
            border-radius: 14px;
            background: #151b23;
            border: 1px solid var(--line);
          }

          div[data-testid="stCaptionContainer"] p {
            color: var(--muted);
          }

          label[data-testid="stWidgetLabel"] p {
            color: var(--muted);
            font-size: 0.84rem;
          }

          @media (max-width: 900px) {
            .app-header h1 {
              max-width: none;
            }

            .metric-strip {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            div[data-testid="stForm"] {
              min-height: auto;
            }

            .empty-state-card,
            div[data-testid="stTabs"] {
              min-height: auto;
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_app()
