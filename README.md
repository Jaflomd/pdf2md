# pdf2md

`pdf2md` convierte documentos locales a un bundle Markdown listo para usar:

- `document.md` completo
- `chapters/*.md` por capítulos
- `chunks/**/*.md` para RAG o agentes
- `manifest.json` y `chunks/index.jsonl` con metadata liviana
- carpeta de salida determinística: `<outdir>/<slug>--<sha256-corto>/`

La app ahora tiene dos modos:

- CLI para procesos locales y automatizables
- UI web minimalista para alumnos con subida de `PDF` y descarga directa del Markdown o del bundle `.zip`

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

La interfaz online está pensada para un flujo corto:

- subir un `PDF`
- elegir si quieres priorizar `document.md` o el bundle `.zip`
- dividir el bundle cada `N` páginas
- previsualizar `document.md`
- descargar `document.md`
- descargar un bloque individual
- descargar todo el bundle generado en `.zip`

La web fuerza `Fast Mode` con `pymupdf4llm` para priorizar velocidad y una UX más predecible. El CLI mantiene los otros formatos y engines.

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

Para la versión live en Streamlit Community Cloud este repo ya quedó preparado con:

- `app.py` listo para correr sin instalación editable del paquete
- `requirements.txt` para que Community Cloud instale dependencias Python desde GitHub
- `.streamlit/config.toml` con base dark

Pasos:

1. Entra a [share.streamlit.io](https://share.streamlit.io/).
2. Conecta tu cuenta de GitHub si todavía no lo has hecho.
3. Crea una app nueva apuntando a este repo público: [github.com/Jaflomd/pdf2md](https://github.com/Jaflomd/pdf2md)
4. Usa `main` como branch y `app.py` como archivo principal.

Según la documentación oficial de Streamlit Community Cloud, las apps se despliegan desde GitHub y normalmente quedan con una URL `*.streamlit.app`. También recomiendan usar `requirements.txt` para dependencias y exigen permisos admin sobre el repo al desplegar.

Fuentes:

- [Deploy your app on Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [App dependencies for your Community Cloud app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [Connect your GitHub account](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/connect-your-github-account)

## Deploy Free en Render

Este repo también quedó listo para desplegarse gratis en Render desde GitHub con [render.yaml](/Users/jaflomd/Library/Mobile%20Documents/com~apple~CloudDocs/Jaflo%20OS/6%20Development/pdf2md/render.yaml).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Jaflomd/pdf2md/tree/main)

La configuración usa:

- `plan: free`
- `buildCommand: pip install -r requirements.txt`
- `startCommand: streamlit run app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true`

Notas importantes según la documentación oficial:

- GitHub Pages no sirve para esta app porque es hosting estático de `HTML/CSS/JS`, y aquí necesitamos Python en servidor: [GitHub Pages](https://docs.github.com/es/pages/getting-started-with-github-pages/about-github-pages)
- Render Free sí corre apps Python, pero hace spin down tras `15 minutos` sin tráfico: [Render Free](https://render.com/docs/free)
- Cada servicio web en Render recibe una URL pública `*.onrender.com`: [Render Web Services](https://render.com/docs/web-services)
