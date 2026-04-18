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


WEB_UPLOAD_FORMATS = (".pdf",)
DOWNLOAD_MODES: dict[str, dict[str, str]] = {
    "Documento completo": {
        "summary": "Te dejo primero el Markdown completo listo para descargar.",
        "note": "También queda disponible el bundle `.zip` por bloques por si lo quieres reutilizar.",
        "primary": "md",
    },
    "Bundle por bloques": {
        "summary": "Te dejo primero el `.zip` con el Markdown completo y bloques por páginas.",
        "note": "El `document.md` completo sigue disponible en el mismo resultado.",
        "primary": "zip",
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

    left_col, right_col = st.columns((0.78, 1.22), gap="medium")
    with left_col:
        with st.form("convert-form"):
            uploaded = _render_upload_panel(st)
            download_mode, page_group_size = _render_controls_panel(st)
            convert_clicked = st.form_submit_button(
                "Convertir PDF",
                type="primary",
                use_container_width=True,
            )

    if convert_clicked:
        if uploaded is None:
            st.session_state["conversion"] = None
            st.session_state["conversion_error"] = "Primero sube un PDF."
        elif Path(uploaded.name).suffix.lower() != ".pdf":
            st.session_state["conversion"] = None
            st.session_state["conversion_error"] = "Esta versión online acepta solo archivos PDF."
        else:
            try:
                with st.spinner("Convirtiendo PDF..."):
                    st.session_state["conversion"] = convert_document_bytes(
                        file_bytes=uploaded.getvalue(),
                        filename=uploaded.name,
                        chunk_target=1000,
                        chunk_overlap=120,
                        engine="pymupdf4llm",
                        fast_mode=True,
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
            _render_empty_state(st, download_mode, page_group_size)
            return

        _render_results(st, conversion, download_mode)


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
          <h1>Sube un PDF y baja el Markdown.</h1>
          <p>Interfaz mínima para alumnos: un archivo, una elección simple y descargas claras.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_upload_panel(st: Any) -> Any:
    st.markdown(
        """
        <div class="panel-heading">
          <span class="panel-label">1</span>
          <div>
            <h3>PDF de entrada</h3>
            <p>Solo PDF. Si el archivo está escaneado como imagen, esta versión no hace OCR.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "PDF de entrada",
        type=[suffix.lstrip(".") for suffix in WEB_UPLOAD_FORMATS if suffix in SUPPORTED_INPUTS],
        label_visibility="collapsed",
        help="Sube un PDF digital. La app online prioriza velocidad y salida en Markdown.",
    )

    if uploaded is None:
        st.markdown(
            """
            <div class="upload-hint">
              <strong>Arrastra aquí tu PDF</strong>
              <p>Luego eliges el tipo de salida y lo descargas.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return None

    st.markdown(
        f"""
        <div class="file-card">
          <span class="file-pill">PDF</span>
          <strong>{uploaded.name}</strong>
          <p>{_human_size(uploaded.size)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return uploaded


def _render_controls_panel(st: Any) -> tuple[str, int]:
    st.markdown(
        """
        <div class="panel-heading compact">
          <span class="panel-label">2</span>
          <div>
            <h3>Qué quieres descargar</h3>
            <p>La conversión usa siempre Fast Mode para ir rápido.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    download_mode = st.radio(
        "Modo de salida",
        options=list(DOWNLOAD_MODES),
        horizontal=True,
        label_visibility="collapsed",
    )
    st.caption(DOWNLOAD_MODES[download_mode]["summary"])

    page_group_size = st.slider(
        "Páginas por bloque",
        min_value=5,
        max_value=100,
        value=30,
        step=5,
        help="Cuando descargues el bundle, cada archivo intermedio agrupa este número de páginas.",
    )

    st.markdown(
        f"""
        <div class="mini-note">
          <strong>Modo activo:</strong> PyMuPDF4LLM + bloques de {page_group_size} páginas.
        </div>
        """,
        unsafe_allow_html=True,
    )
    return download_mode, int(page_group_size)


def _render_empty_state(st: Any, download_mode: str, page_group_size: int) -> None:
    st.markdown(
        f"""
        <div class="result-card empty-state">
          <span class="panel-label">3</span>
          <h3>Tu resultado sale aquí</h3>
          <p>{DOWNLOAD_MODES[download_mode]["note"]}</p>
          <div class="step-list">
            <span>Sube un PDF</span>
            <span>Elige salida</span>
            <span>Convierte y descarga</span>
          </div>
          <div class="empty-footer">Bloques por defecto: {page_group_size} páginas.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_results(st: Any, conversion: WebConversionResult, download_mode: str) -> None:
    manifest = conversion.manifest
    section_lookup = dict(conversion.chapter_markdowns)
    section_titles = list(section_lookup)
    processing = manifest.get("processing", {})
    section_label = "Bloques" if processing.get("segmentation_mode") == "page-batch" else "Secciones"
    single_section_label = "bloque" if section_label == "Bloques" else "seccion"
    primary_kind = DOWNLOAD_MODES[download_mode]["primary"]

    st.markdown(
        f"""
        <div class="result-card success-card">
          <span class="panel-label">Listo</span>
          <h2>{conversion.source_name}</h2>
          <p>{DOWNLOAD_MODES[download_mode]["note"]}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        _render_metric_strip_html(
            (
                ("Páginas", str(manifest["source"]["page_count"])),
                (section_label, str(len(manifest["chapters"]))),
                ("Engine", str(manifest["engine"]["full_markdown_engine"])),
                ("Modo", "Fast"),
            )
        ),
        unsafe_allow_html=True,
    )

    selected_section_title = None
    selected_section_markdown = None
    if section_titles:
        selected_section_title = st.selectbox(
            f"{section_label} para descarga rápida",
            options=section_titles,
            index=0,
            label_visibility="collapsed",
        )
        selected_section_markdown = section_lookup[selected_section_title]

    downloads = {
        "md": (
            "Descargar document.md",
            conversion.document_markdown,
            f"{Path(conversion.source_name).stem}.md",
            "text/markdown",
        ),
        "zip": (
            "Descargar bundle .zip",
            conversion.archive_bytes,
            conversion.archive_name,
            "application/zip",
        ),
    }
    download_order = ("md", "zip") if primary_kind == "md" else ("zip", "md")
    download_cols = st.columns(3, gap="small")

    first_label, first_data, first_name, first_mime = downloads[download_order[0]]
    download_cols[0].download_button(
        first_label,
        data=first_data,
        file_name=first_name,
        mime=first_mime,
        use_container_width=True,
    )

    second_label, second_data, second_name, second_mime = downloads[download_order[1]]
    download_cols[1].download_button(
        second_label,
        data=second_data,
        file_name=second_name,
        mime=second_mime,
        use_container_width=True,
    )

    if selected_section_title and selected_section_markdown:
        download_cols[2].download_button(
            f"Descargar {single_section_label}",
            data=selected_section_markdown,
            file_name=f"{Path(conversion.source_name).stem}-{slugify(selected_section_title)}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    else:
        download_cols[2].markdown(
            """
            <div class="download-placeholder">
              <strong>Bloque individual</strong>
              <p>Este archivo no generó divisiones intermedias.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if manifest["warnings"]:
        for warning in manifest["warnings"]:
            st.markdown(
                f"""
                <div class="warning-card">
                  <strong>Nota</strong>
                  <p>{warning}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    tab_markdown, tab_sections = st.tabs(["Markdown", section_label])

    with tab_markdown:
        st.text_area(
            "Markdown completo",
            value=conversion.document_markdown,
            height=360,
            label_visibility="collapsed",
        )

    with tab_sections:
        if not section_titles:
            st.info("No hay bloques intermedios para este documento.")
        else:
            preview_title = st.selectbox(
                f"{section_label} para revisar",
                options=section_titles,
                index=0,
                key="section-preview-select",
            )
            st.text_area(
                f"Preview de {preview_title}",
                value=section_lookup[preview_title],
                height=360,
                label_visibility="collapsed",
            )


def _render_metric_strip_html(items: tuple[tuple[str, str], ...]) -> str:
    pills = "".join(
        f"""
        <div class="metric-pill">
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
        """
        for label, value in items
    )
    return f'<div class="metric-strip">{pills}</div>'


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


def _read_chapter_markdowns(output_root: Path, manifest: dict[str, Any]) -> list[tuple[str, str]]:
    chapter_markdowns: list[tuple[str, str]] = []
    for chapter in manifest["chapters"]:
        relative_path = chapter.get("relative_path")
        if not relative_path:
            continue
        chapter_path = output_root / str(relative_path)
        if not chapter_path.exists():
            continue
        chapter_markdowns.append((str(chapter["title"]), chapter_path.read_text(encoding="utf-8")))
    return chapter_markdowns


def _build_archive_bytes(output_root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_root.rglob("*")):
            if not path.is_file():
                continue
            archive.write(path, arcname=str(Path(output_root.name) / path.relative_to(output_root)))
    return buffer.getvalue()


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
          @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

          :root {
            --bg: #090b10;
            --panel: #11141b;
            --panel-strong: #171b23;
            --line: rgba(255, 255, 255, 0.08);
            --text: #f4f7fb;
            --muted: #9aa4b2;
            --soft: #7dd3fc;
            --soft-bg: rgba(125, 211, 252, 0.1);
            --radius-xl: 24px;
            --radius-lg: 18px;
            --radius-md: 14px;
          }

          .stApp {
            background:
              radial-gradient(circle at top, rgba(125, 211, 252, 0.10), transparent 22%),
              linear-gradient(180deg, #090b10 0%, #0d1117 100%);
            color: var(--text);
          }

          .block-container {
            max-width: 1200px;
            padding-top: 0.85rem;
            padding-bottom: 0.9rem;
          }

          h1, h2, h3, h4, p, li, label, div, span, button {
            font-family: "Manrope", "Avenir Next", sans-serif;
          }

          code, pre, textarea {
            font-family: "IBM Plex Mono", "SFMono-Regular", monospace !important;
          }

          .app-header {
            margin-bottom: 0.85rem;
          }

          .app-header h1 {
            margin: 0.3rem 0 0.45rem;
            font-size: clamp(1.8rem, 2.9vw, 2.8rem);
            line-height: 0.98;
            letter-spacing: -0.05em;
            max-width: 13ch;
          }

          .app-header p {
            margin: 0;
            color: var(--muted);
            max-width: 52ch;
            font-size: 0.96rem;
            line-height: 1.48;
          }

          .eyebrow,
          .panel-label,
          .file-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 2rem;
            padding: 0.28rem 0.7rem;
            border-radius: 999px;
            background: var(--soft-bg);
            color: var(--soft);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.03em;
          }

          div[data-testid="stForm"] {
            border: 1px solid var(--line);
            border-radius: var(--radius-xl);
            background: rgba(17, 20, 27, 0.92);
            padding: 1rem;
            min-height: calc(100vh - 180px);
          }

          .panel-heading {
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 0.75rem;
            align-items: start;
            margin-bottom: 0.7rem;
          }

          .panel-heading.compact {
            margin-top: 0.95rem;
          }

          .panel-heading h3,
          .result-card h3,
          .result-card h2 {
            margin: 0;
            letter-spacing: -0.03em;
          }

          .panel-heading p,
          .file-card p,
          .result-card p,
          .upload-hint p,
          .mini-note,
          .download-placeholder p,
          .warning-card p {
            margin: 0.2rem 0 0;
            color: var(--muted);
            line-height: 1.45;
          }

          .upload-hint,
          .file-card,
          .result-card,
          .warning-card,
          .mini-note,
          .download-placeholder {
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            background: var(--panel);
          }

          .upload-hint {
            padding: 0.95rem 1rem;
          }

          .file-card {
            display: grid;
            gap: 0.25rem;
            padding: 0.95rem 1rem;
          }

          .file-card strong {
            font-size: 1rem;
          }

          .mini-note {
            margin-top: 0.7rem;
            padding: 0.75rem 0.9rem;
            font-size: 0.88rem;
          }

          .result-card {
            padding: 1rem 1.05rem;
            margin-bottom: 0.7rem;
          }

          .success-card {
            background:
              linear-gradient(180deg, rgba(125, 211, 252, 0.07), transparent 70%),
              var(--panel);
          }

          .empty-state h3 {
            margin-top: 0.45rem;
          }

          .step-list {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.85rem;
          }

          .step-list span {
            padding: 0.48rem 0.72rem;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.04);
            color: var(--text);
            font-size: 0.84rem;
          }

          .empty-footer {
            margin-top: 0.85rem;
            color: var(--muted);
            font-size: 0.86rem;
          }

          .metric-strip {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            margin-bottom: 0.75rem;
          }

          .metric-pill {
            border: 1px solid var(--line);
            border-radius: var(--radius-md);
            background: var(--panel-strong);
            padding: 0.78rem 0.9rem;
          }

          .metric-pill span {
            display: block;
            color: var(--muted);
            font-size: 0.76rem;
            margin-bottom: 0.2rem;
          }

          .metric-pill strong {
            font-size: 1rem;
            letter-spacing: -0.02em;
          }

          .warning-card {
            padding: 0.85rem 0.95rem;
            margin: 0.65rem 0;
          }

          .warning-card strong {
            color: var(--text);
          }

          .download-placeholder {
            padding: 0.7rem 0.8rem;
            min-height: 74px;
          }

          div[data-baseweb="file-uploader"] {
            border-radius: var(--radius-lg) !important;
            border: 1px dashed rgba(255, 255, 255, 0.18) !important;
            background: rgba(255, 255, 255, 0.02);
          }

          div[data-baseweb="radio"] label,
          div[role="radiogroup"] label {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 0.4rem 0.85rem;
          }

          .stButton > button,
          .stDownloadButton > button,
          div[data-testid="stFormSubmitButton"] > button {
            border-radius: 999px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            min-height: 46px;
            font-weight: 800;
            letter-spacing: -0.01em;
            background: #f4f7fb;
            color: #090b10;
          }

          .stDownloadButton > button {
            background: var(--panel-strong);
            color: var(--text);
          }

          .stDownloadButton > button:hover,
          .stButton > button:hover,
          div[data-testid="stFormSubmitButton"] > button:hover {
            border-color: rgba(125, 211, 252, 0.5);
            color: inherit;
          }

          .stTabs [data-baseweb="tab-list"] {
            gap: 0.45rem;
          }

          .stTabs [data-baseweb="tab"] {
            border-radius: 999px 999px 0 0;
          }

          textarea {
            background: #0d1117 !important;
            color: var(--text) !important;
            border-radius: var(--radius-md) !important;
            border: 1px solid var(--line) !important;
            line-height: 1.5 !important;
            font-size: 0.88rem !important;
          }

          @media (max-width: 980px) {
            div[data-testid="stForm"] {
              min-height: auto;
            }

            .metric-strip {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_app()
