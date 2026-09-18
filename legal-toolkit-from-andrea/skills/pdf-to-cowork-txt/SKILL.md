---
name: pdf-to-cowork-txt
description: >
  Convert PDF files into Markdown (.md) or plain-text (.txt) files that
  Cowork can fully read and search. USE THIS SKILL when the user says
  "convert PDF to txt", "convert to markdown", "make this PDF readable",
  "run the PDF converter", "pdf to cowork", "convert my PDFs", or when
  Cowork has flagged that it couldn't fully read a PDF file. Also triggers
  when the user uploads a PDF and wants a Cowork-readable version, or when
  a task requires reading a large PDF that failed in a prior session. Even
  casual triggers like "I need to read this whole transcript" or "can you
  search this PDF" on a large file should invoke this skill. NOT for
  creating new PDFs, summarizing, or PDF form-filling — this is a verbatim
  extraction tool only.
---

> **Version:** v2026.09.17-5 (shared edition) · **Last updated:** 2026.09.17

# PDF → Cowork Converter

## Why This Skill Exists

Cowork's Read tool renders PDF pages as images at ~2,500 tokens a page.
For large legal documents that fails outright or burns the session. This
skill runs a Python script that turns PDFs into `.md`/`.txt` files Cowork
can Read and Grep natively. Every step — text extraction, structure,
OCR, validation — runs in bash and costs zero Claude tokens.

---

## HARD RULE: Claude Never Reads PDF Pages Itself

**Session-killing constraint.** The expensive operation is Claude's `Read`
tool on a PDF. Everything this skill does instead — pdftotext, PyMuPDF,
opendataloader-pdf, Tesseract OCR — runs inside the script. Claude reads
the SCRIPT OUTPUT, never the PDF.

1. **Never use Claude's `Read` tool on a source PDF during this skill.**
   Not to "check" a page, not to see whether it is scanned, not as a
   fallback when extraction is thin. The script's validation reports
   everything Claude needs.
2. **Never install or call opendataloader-pdf's hybrid/AI backend** (no
   `--hybrid*`, no `opendataloader-pdf-hybrid`, no `[hybrid]` extra). It is
   a large ML install the skill does not need.
3. **pymupdf4llm's integrated OCR stays disabled** (`use_ocr=False`). The
   skill runs its own Tesseract pass (the `tesseract` executable, or the
   copy compiled into PyMuPDF when the executable is absent) with
   page-level reporting; a second uncontrolled OCR would double text and
   hide provenance.

**OCR is allowed and automatic.** Image-only and near-empty pages are
OCR'd by Tesseract (`--ocr auto`, the default) — the `tesseract`
executable when one is on PATH, otherwise the Tesseract engine compiled
into PyMuPDF with the bundled `tessdata/` folder. The output says exactly
which pages are OCR text and which backend ran (`OCR PAGES:` header line,
`+ tesseract OCR (N pg)` in EXTRACTION METHOD, `- OCR:` in MANIFEST) and,
with the executable, flags weak pages `[OCR-LOW-CONFIDENCE]`. The PyMuPDF
backend exposes no word confidences, so that gate cannot fire in that
mode; the header says `no confidence scores`. When a PDF is image-only,
do nothing special: run the script, read the header and CONTENT GAPS,
report the `[IMAGE-ONLY]` and `[OCR-LOW-CONFIDENCE]` page numbers to the
user. Do not open the PDF to look.

If the shell is unavailable ("Workspace unavailable" after 3 retries,
10 s apart), STOP and tell the user. Never transcribe from the Read tool
as a workaround.

---

## What the Script Does

- **Sidecar first.** A producing party's native `.txt` (DISCO `Text/`,
  Relativity `VOL001/TEXT/TEXT001/`) is used verbatim when present; no
  extraction runs. Sidecars carry no Bates or confidentiality stamps.
- **Document routing.** Depositions and two-column layouts → `.txt`;
  everything else → `.md`. Override with `--format`.
- **Engine chain, quality-gated.** `.md`: opendataloader-pdf → pymupdf4llm
  → legacy font renderer → plain. `.txt`: opendataloader-pdf → pdftotext
  -layout. Each tier must keep ≥ 70% of the pdftotext reference text and
  show no duplicate-layer doubling, or the next tier runs.
- **OCR.** Pages the reference extraction found empty or near-empty are
  rendered at 300 dpi and OCR'd by Tesseract (executable or PyMuPDF
  built-in); the text takes the page's place in the pipeline.
- **Bates / confidentiality stamps** are lifted from the margin (text
  layer or OCR word boxes) into each page marker and a `BATES RANGE:`
  header line, so header/footer stripping never loses them.
- **Validation.** Page-count match, per-page content threshold, gap
  classification, CONTENT GAPS block at the top of every file, MANIFEST.md
  per output folder.

Detail: `modules/ENGINES.md` (engines, routing, quality gates, OCR),
`modules/OUTPUT-FORMAT.md` (header, gaps, markers, stamps, MANIFEST,
sidecar), `modules/CLI.md` (every flag with examples).

---

## How to Run

### Step 0: Pre-flight

```bash
python3 -c "import fitz, pymupdf4llm, pdfplumber, pypdf; print('deps OK')"   # required set
which pdftotext tesseract java                                              # each optional
pip install pymupdf pymupdf4llm pdfplumber pypdf --break-system-packages -q  # add opendataloader-pdf only where Java exists
```

Required: PyMuPDF (`pymupdf`), `pymupdf4llm`, `pdfplumber`, `pypdf` — all
pip-installable without admin rights. Everything else is optional and
degrades, never blocks: no `pdftotext` (typical on Windows; do not install
poppler) → pdfplumber, then pypdf, is the reference extractor; no Java or
opendataloader-pdf → the chain falls through to pymupdf4llm; no `tesseract`
executable → the script uses PyMuPDF's built-in Tesseract with the bundled
`tessdata/` folder (or `TESSDATA_PREFIX`), without word confidences; no
pymupdf4llm → legacy renderer. Never install `opendataloader-pdf[hybrid]`.
Do not use the old `--target /tmp/pymupdf_pkg` PyMuPDF install or a
`PYTHONPATH` override — pymupdf4llm needs its own matching PyMuPDF.

### Step 1: Locate the script

Cowork sandbox (skills mount is read-only; copy first):
```bash
cp "$(find /sessions -name 'pdf_to_cowork_md.py' -path '*/skills/*' 2>/dev/null | head -1)" /tmp/pdf_to_cowork_md.py
```
Claude Code on Windows: run it in place from the synced skill folder
(`%USERPROFILE%\.claude\skills\pdf-to-cowork-txt\pdf_to_cowork_md.py`) —
no copy, and the bundled `tessdata/` is found beside it. Substitute that
path for `/tmp/pdf_to_cowork_md.py` below.

### Step 2: Convert

```bash
python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf"            # one file
python3 /tmp/pdf_to_cowork_md.py "/path/to/folder/" --skip-existing   # batch
```

Output goes to `_cowork_txt/` beside the source as `<stem>_COWORK.md` or
`.txt`, plus `MANIFEST.md`. Format is auto-detected.

### Step 3: Common adjustments

| Need | Flag |
|---|---|
| Force Markdown / plain text | `--format md` / `--format txt` |
| Inline bold/italic matters (case names, defined terms) | `--md-engine pymupdf4llm` |
| Side-by-side condensed transcript layout (old behavior) | `--txt-engine pdftotext` |
| Encrypted PDF | `--password "pw"` |
| Some pages only (markers keep original numbers) | `--pages 5-7` |
| Vendor text layer is garbage — OCR everything | `--ocr force` |
| Faster on a production folder | `--ocr-workers 4` |
| Borderless tables | `--table-method cluster` |
| Machine-readable results for the report | `--json-report /tmp/report.json` |

Full reference: `modules/CLI.md`.

### Step 4: Report to the user

Read `MANIFEST.md` (never the PDFs) and report: files converted, format,
completeness, OCR page counts, Bates ranges, and every gap with its page
numbers and type. For files that failed entirely, report the error and
the methods attempted.

---

## CRITICAL: Reading _COWORK Files in Other Skills

**Every skill and session that reads a `_COWORK.md` or `_COWORK.txt` file
MUST check the header and CONTENT GAPS block first** (first ~30 lines).

- **Gaps.** If CONTENT GAPS lists pages relevant to the task, say so:
  "This file has content gaps on pages X, Y, Z — those pages may hold
  content I can't see. Check the original PDF." Never work around missing
  content silently.
- **OCR text.** `OCR PAGES:` in the header names the pages whose text is
  OCR, not native. `[OCR-LOW-CONFIDENCE]` pages need verification against
  the image before quoting.
- **Bates and confidentiality.** Cite from the page markers
  (`<!-- Page 7 of 40 | Bates EX_000129 | CONFIDENTIAL ... -->`) and the
  `BATES RANGE:` / `CONFIDENTIALITY:` header lines. A marker with no stamp
  means none was found, not none exists.
- **Sidecar-sourced files** (`EXTRACTION METHOD: DISCO native text
  sidecar`) contain no Bates number or confidentiality legend at all —
  those are burned into the image. Never cite Bates or assert
  confidentiality from a sidecar file; open the original.

Grep patterns: `CONTENT GAPS \(` (gaps exist), `CONTENT GAPS:  None`
(complete), `\[CONTENT GAP` (inline markers), `<!-- Page 10 of` or
`=== PAGE 10 of` (jump to a page), `Bates EX_000129` (find a Bates page).

---

## Limitations

| Situation | Result |
|---|---|
| Text-layer PDF | Full extraction, `.md` or `.txt` by type |
| Image-only pages / scanned productions | OCR'd automatically; `OCR PAGES:` in header; weak pages flagged |
| Handwriting, photos, blank slip-sheets | OCR yields nothing → `[IMAGE-ONLY]`; original required |
| Production with native `.txt` sidecar | Verbatim native text; no stamps in the file |
| Password-protected PDF | `--password`; without it the run fails fast with a clear message |
| Inline bold/italic needed in `.md` | Default engine does not mark it — `--md-engine pymupdf4llm` |
| White-on-white hidden text, no drawn background | Not filtered; appears as ordinary text |
| Unusual stamp formats (lower-case legend, no digits, mid-page) | Not detected |
| Java / opendataloader-pdf / pdftotext / pymupdf4llm missing | Chain falls through; header records which engine ran |
| No `tesseract` executable | PyMuPDF built-in Tesseract with bundled `tessdata/`; no `[OCR-LOW-CONFIDENCE]` gate; header says `no confidence scores` |
| Shell unavailable | Cannot run — report, do not work around |
| Claude Read tool on the source PDF | BLOCKED |

---

## Dependencies

Required (pip, no admin): `pymupdf`, `pymupdf4llm`, `pdfplumber`, `pypdf`.
Bundled: `tessdata/eng.traineddata` (Tesseract English model, 4 MB) for
the PyMuPDF OCR backend. Optional, used when present: `pdftotext`
(poppler), `tesseract` executable (adds word confidences), `java` 11+ with
`opendataloader-pdf` (first-tier structure engine). Everything optional is
pre-installed in the Cowork sandbox; on a locked-down Windows laptop none
of it is, and the skill still converts and OCRs.

## Modules

- `modules/ENGINES.md` — engine chain, routing, Markdown features, quality
  gates, OCR mechanics, header/footer stripping
- `modules/OUTPUT-FORMAT.md` — header, CONTENT GAPS, page markers, stamps,
  MANIFEST, sidecar rules, naming
- `modules/CLI.md` — full flag reference and worked examples
