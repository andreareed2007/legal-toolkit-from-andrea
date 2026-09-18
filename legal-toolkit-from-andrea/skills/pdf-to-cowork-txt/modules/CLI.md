# pdf-to-cowork-txt — CLI Reference

```
usage: pdf_to_cowork_md.py [-h] [--output OUTPUT] [--method {pdftotext,pdfplumber,pypdf}]
                           [--format {md,txt,auto}] [--skip-existing] [--no-subfolder]
                           [--json-report JSON_REPORT]
                           [--md-engine {odl,pymupdf4llm,legacy}] [--txt-engine {odl,pdftotext}]
                           [--password PASSWORD] [--pages PAGES]
                           [--use-struct-tree] [--table-method {default,cluster}] [--sanitize]
                           [--content-safety-off LIST] [--threads THREADS] [--no-stamps]
                           [--ocr {auto,off,force}] [--ocr-lang LANG] [--ocr-workers N]
                           input
```

## Core

| Flag | Meaning |
|---|---|
| `input` | PDF file, or a directory to batch-convert (`*.pdf`, non-recursive) |
| `--output, -o` | Output path (single-file mode only) |
| `--format, -f` | `md`, `txt`, or `auto` (default: detect document type) |
| `--method, -m` | Force the reference extractor: `pdftotext`, `pdfplumber`, `pypdf` (default: try all) |
| `--skip-existing` | Batch: skip PDFs that already have a `_COWORK` output |
| `--no-subfolder` | Write beside the PDF instead of in `_cowork_txt/` |
| `--json-report PATH` | Machine-readable results (names, outputs, completeness, gap counts) |

## Engines

| Flag | Meaning |
|---|---|
| `--md-engine` | `odl` (default) → `pymupdf4llm` → `legacy`. Start of the `.md` chain; each tier still falls through when quality-gated. Env `COWORK_MD_ENGINE`. |
| `--txt-engine` | `odl` (default: reading order, line breaks kept) or `pdftotext` (side-by-side `-layout`). Env `COWORK_TXT_ENGINE`. |
| `--use-struct-tree` | Use the PDF's own structure tags for reading order/headings (opt-in; depends on producer tag quality) |
| `--table-method` | `default` (ruled borders) or `cluster` (borders + whitespace clustering, catches borderless tables) |
| `--sanitize` | Replace emails, phones, IPs, card numbers, URLs with placeholders — NOT verbatim, opt-in only |
| `--content-safety-off LIST` | Disable opendataloader-pdf filters: `all`, `hidden-text`, `off-page`, `tiny`, `hidden-ocg`, `background` |
| `--threads N` | opendataloader-pdf worker threads (default 1; >1 experimental) |
| `--no-stamps` | Do not capture Bates / confidentiality legends into page markers |

## Working copy

| Flag | Meaning |
|---|---|
| `--password, -p PW` | Encrypted PDF: decrypted once into a temp copy so every extractor sees a plain file. Missing or wrong password fails fast with a clear message. |
| `--pages SPEC` | `1,3,5-7`. Single-file only. Output `<stem>_p5-7_COWORK.*`; markers and CONTENT GAPS keep ORIGINAL page numbers; own MANIFEST entry; sidecar shortcut skipped. |

## OCR (tesseract in bash — zero Claude tokens)

| Flag | Meaning |
|---|---|
| `--ocr` | `auto` (default): OCR `[IMAGE-ONLY]`, `[PARTIAL]` and failed pages. `off`: never. `force`: every page (replace a garbage text layer). Env `COWORK_OCR`. |
| `--ocr-lang` | tesseract language(s), e.g. `eng` (default) or `eng+spa` |
| `--ocr-workers N` | Parallel tesseract processes (default 2) |
| *(backend)* | Automatic, not a flag: `tesseract` executable if on PATH (word confidences, `[OCR-LOW-CONFIDENCE]` gate), else the Tesseract engine built into PyMuPDF with bundled `tessdata/` or `TESSDATA_PREFIX` (no confidence scores). Header names which ran. |

## Examples

```bash
# One brief, auto-detected format
python3 /tmp/pdf_to_cowork_md.py "/path/Motion.pdf"

# Whole matter folder, skip what's done
python3 /tmp/pdf_to_cowork_md.py "/path/Court Papers/" --skip-existing

# Vendor production folder (image-only) — OCR with 4 workers, report file
python3 /tmp/pdf_to_cowork_md.py "/path/VOL001/IMAGES/IMG001/" --ocr-workers 4 --json-report /tmp/prod.json

# Condensed deposition, old side-by-side layout
python3 /tmp/pdf_to_cowork_md.py "/path/Smith (condensed).pdf" --txt-engine pdftotext

# Contract where bold/italic defined terms matter
python3 /tmp/pdf_to_cowork_md.py "/path/Credit Agreement.pdf" --md-engine pymupdf4llm

# Encrypted exhibit, pages 12-15 only
python3 /tmp/pdf_to_cowork_md.py "/path/Exhibit C.pdf" --password "pw" --pages 12-15

# Text layer is garbage (double letters, wrong glyphs) — OCR everything
python3 /tmp/pdf_to_cowork_md.py "/path/scan.pdf" --ocr force

# Borderless financial tables
python3 /tmp/pdf_to_cowork_md.py "/path/Schedule.pdf" --table-method cluster

# Reproduce the old no-OCR behavior
python3 /tmp/pdf_to_cowork_md.py "/path/file.pdf" --ocr off
```

## Environment overrides

`COWORK_MD_ENGINE`, `COWORK_TXT_ENGINE`, `COWORK_OCR`, `COWORK_IGNORE_SIDECAR=1`,
`TESSDATA_PREFIX` (folder holding `eng.traineddata` for the PyMuPDF OCR backend).
