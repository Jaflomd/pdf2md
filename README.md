# pdf2md

`pdf2md` convierte documentos locales a un bundle Markdown listo para usar:

- `document.md` completo
- `chapters/*.md` por capítulos
- `chunks/**/*.md` para RAG o agentes
- `manifest.json` y `chunks/index.jsonl` con metadata liviana
- carpeta de salida determinística: `<outdir>/<slug>--<sha256-corto>/`

La app ahora tiene dos modos:

- CLI para procesos locales y automatizables
- UI web con subida de archivo y descarga directa del Markdown o del bundle `.zip`

Todo corre localmente, sin llamadas LLM ni OCR en v1.

## Formatos soportados

- `PDF`: soportado
- `EPUB`: soportado
- `Word (.docx)`: soportado
- `AZW3`: soporte opcional si existe `ebook-convert` de Calibre; si no, el flujo falla con un mensaje claro

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## App web

```bash
streamlit run app.py
```

También puedes usar el entrypoint:

```bash
pdf2md-web
```

La interfaz permite:

- subir un documento
- elegir el engine
- activar `Fast Mode` para dividir el documento cada `N` páginas
- ajustar chunk target y overlap
- previsualizar `document.md`
- descargar `document.md`
- descargar todo el bundle generado en `.zip`

## CLI

```bash
python -m pdf2md ./libro.pdf --outdir ./outputs
python -m pdf2md ./book.epub --engine pymupdf4llm --split auto
python -m pdf2md ./manual.pdf --split pages --page-group-size 20
python -m pdf2md ./reporte.pdf --name reporte-finanzas --json
python -m pdf2md ./manual.pdf --fast-mode
```

Opciones principales:

- `--outdir`: carpeta base de salida
- `--name`: prefijo opcional del bundle; igual se agrega hash de contenido
- `--split`: `auto`, `chapters`, `pages`
- `--chunk-target`: objetivo de tokens por chunk, default `1000`
- `--chunk-overlap`: overlap por chunk, default `120`
- `--engine`: `auto`, `docling`, `pymupdf4llm`
- `--page-group-size`: páginas por archivo Markdown cuando el split termina en grupos por página
- `--fast-mode`: shortcut de compatibilidad para `--engine pymupdf4llm --split pages`
- `--json`: imprime un resumen compacto para automatización

## Notas de diseño

- `Docling` es el extractor principal para `PDF` completos.
- `PyMuPDF4LLM` produce el slicing por página y sirve como fallback.
- Para `EPUB`, el flujo usa `PyMuPDF4LLM` como extractor principal porque `Docling` no lo soporta nativamente.
- Si un PDF parece escaneado, el proceso aborta: v1 no hace OCR.
- El split `auto` intenta detectar capítulos por TOC o headings y, si no puede, cae a grupos por páginas.
- `Fast Mode` sigue generando chunks; no solo archivos por páginas.
- El pipeline limpia varios problemas comunes de PDFs: headers/footers repetidos, números de página sueltos, guiones de fin de línea y saltos raros dentro de párrafos.
- Si `tiktoken` no tiene encodings cacheados, el chunking cae a un contador offline aproximado para que el flujo siga funcionando sin red.

## Publicarlo desde GitHub

El repo queda listo para subirse a GitHub. Si quieres que además quede live como app interactiva, el siguiente paso natural es conectarlo a Streamlit Community Cloud o Render usando este mismo repo.
