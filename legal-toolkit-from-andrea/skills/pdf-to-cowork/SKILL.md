---
name: pdf-to-cowork
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

> **Version:** v2026.07.29-1 (shared edition) · **Last updated:** 2026.08.05

# PDF → Cowork Converter

## Why This Skill Exists

Cowork's Read tool renders PDF pages as images. For small PDFs (under ~10
pages), this works fine — Claude can visually read the pages. But for large
legal documents (100+ page transcripts, lengthy briefs, exhibit binders),
the Read tool either fails outright or would require Claude to manually
transcribe hundreds of pages from images — slow, token-expensive, and not
truly verbatim.

This skill runs a Python script that converts PDFs into files Cowork can
Read and Grep natively. It uses a hybrid architecture (pdftotext for text
extraction, PyMuPDF for font metadata) and document-type-aware routing to
choose the best output format automatically.

---

## HARD RULE: No OCR and No Speculative PDF Reading

**This is a session-killing constraint. Violating it wastes the user's
entire token allotment for the session. There are no exceptions unless
the user explicitly grants permission in the current session.**

### What is absolutely banned

1. **Never invoke OCR tools.** Do not call `tesseract`, `pytesseract`,
   `ocrmypdf`, `easyocr`, or any optical character recognition library
   or CLI tool. Not to "check" a single page. Not even if the PDF
   appears to be scanned. Not even if extraction comes back empty.
   The ONLY exception: the user explicitly says to run OCR in the current
   session (e.g., "go ahead and OCR it", "run tesseract on this").
   A general instruction like "convert this PDF" is NOT OCR permission.

2. **Never install OCR packages.** Do not `pip install pytesseract`,
   `pip install easyocr`, or `apt-get install tesseract-ocr`. If the
   extraction pipeline produces empty/image-only results, that is the
   correct outcome for a scanned PDF — report it and stop.

3. **Never visually read PDF pages during this skill.** Do not use
   Claude's `Read` tool on the source PDF file to inspect pages as
   images. The Read tool renders each PDF page as a base64 image
   (~2,500+ tokens per page). "Just checking the first 10 pages" on a
   50-page PDF burns 25,000+ tokens before the actual conversion even
   starts — and the conversion itself costs zero Claude tokens because
   it runs in bash. There is no reason to visually inspect the PDF
   during a conversion run. The Python script handles all extraction
   and validation. Claude reads the SCRIPT OUTPUT, not the PDF.

### What to do when a PDF is scanned (image-only)

The validation system flags image-only pages as `[IMAGE-ONLY]` in the
CONTENT GAPS block. If most or all of the PDF is image-only:

1. Report to the user: "This PDF is scanned/image-only — no extractable
   text layer. The converter can't extract content without OCR."
2. Ask: "Do you want me to run OCR on this? It will use significant
   session tokens."
3. **Wait for explicit yes** before proceeding. Silence or ambiguity
   is not permission.
4. If the user says no (or doesn't answer), stop. Do not attempt
   workarounds. Do not try to visually read pages as a substitute.

### Why this matters

- A single tesseract call on a 100-page PDF can consume 50,000+ tokens.
- Claude visually reading 10 PDF pages through the Read tool burns
  ~25,000 tokens.
- Either one can exhaust a session's token budget, leaving the user
  unable to do any other work for the rest of the session.
- The Python text-extraction pipeline (pdftotext, pdfplumber, pypdf)
  runs in bash and costs zero Claude tokens for the extraction itself.

---

## Architecture

### Hybrid Approach

The script uses two complementary tools:

1. **pdftotext -layout (CLI)** — Gold standard for raw text extraction.
   Preserves spatial layout, handles multi-column documents perfectly, and
   produces clean verbatim text. Used for ALL text extraction.
2. **PyMuPDF / fitz (Python)** — Font metadata overlay. Detects body font,
   heading sizes, bold/italic spans, caption blocks, and repeating
   headers/footers. Used ONLY for structural inference — never for text
   extraction.
3. **pdfplumber / pypdf** — Fallback text extraction if pdftotext is
   unavailable.

### Native Text Sidecar (checked FIRST — discovery productions)

Before any extraction, the script checks for a producing party's native
text sidecar next to the PDF: `<parent>/Text/<stem>.txt`, then
`<parent>/<stem>.txt`. Discovery platforms (DISCO, Relativity, Everlaw)
ship these load-file `.txt` companions containing **letter-perfect native
text** pulled from the original electronic file — not OCR of a scanned
image. When a sidecar is found, the script emits a verbatim native-sourced
`.md` and skips PDF text extraction entirely. This is the correct, no-OCR
way to handle scanned production PDFs whose embedded OCR layer is garbage.

- Header method line reads `EXTRACTION METHOD: DISCO native text sidecar
  (<name>)`. The "DISCO" label is literal in the code but applies to any
  `Text/` sidecar regardless of platform.
- **CRITICAL — Bates numbers and CONFIDENTIAL / PROTECTED designations are
  burned into the PDF image and are NOT present in the native text.** The
  sidecar carries only the document body. The Bates number lives in the
  filename; confidentiality stamps are not recoverable from the text. Any
  downstream work that cites a produced document by Bates number, or that
  turns on a confidentiality designation, MUST go back to the original PDF
  image — the `_COWORK.md` body will not contain them.
- To bypass sidecar sourcing (e.g., to measure the enrichment path against
  a known-good sidecar), set `COWORK_IGNORE_SIDECAR=1`.

### Document-Type Routing

The script auto-detects document type and routes accordingly:

- **Depositions** (monospaced font, line-number patterns, Q/A markers)
  → `.txt` output using pdftotext -layout
- **Two-column documents** (balanced left/right content blocks)
  → `.txt` output using pdftotext -layout
- **Everything else** (briefs, motions, orders, contracts, memos, letters)
  → `.md` output with structural headings, inline emphasis, caption
  blockquotes, and HTML comment page markers

### Why Not Markdown for All Documents

Depositions and condensed transcripts have spatial layout that IS the useful
structure — line numbers, Q/A alignment, 2-column page arrangements.
Markdown headings and bold/italic add nothing to testimony text, and
pymupdf's block-level approach fragments the Q/A reading order in
multi-column formats. pdftotext -layout preserves these perfectly.

### Markdown Features (.md output)

When the script produces `.md` output, it infers structure from font metadata:

- **Headings** — All-caps centered titles → `## HEADING`. Bold+larger text →
  `## ` or `### `. Much-larger text → `# `. Conservative heuristics avoid
  false positives on numbered paragraphs and all-caps body paragraphs.
- **Inline emphasis** — Bold body-size text → `**bold**`. Italic → `*italic*`.
  Case citations and exhibit references are preserved.
- **Caption blocks** — Court filing captions (§ symbols, party-vs-party
  structure) → blockquote (`> `) rendering.
- **Drop-cap small caps** — Span-joining fixes PDFs where each word's initial
  letter is stored at a larger size than the remaining letters (common in
  legal document footers).
- **Header/footer stripping** — Repeating text in the top/bottom 60px zone
  is detected and stripped. Fuzzy matching handles page-number variations
  ("– Page 3", "– Page 4"). Threshold is based on pages with content in the
  zone, not total pages, so exhibit-heavy documents are handled correctly.
- **Page markers** — `<!-- Page N of M -->` HTML comments (invisible when
  rendered, searchable in raw text).

### Output Location

All output files are saved to a `_cowork_txt/` subfolder inside the same
directory as the source PDF:

```
Documents/Matters/Example Matter/Court Papers/
├── 2025-08-08 Motion to Dismiss.pdf
├── Guaranty Agreement.pdf
├── _cowork_txt/
│   ├── 2025-08-08 Motion to Dismiss_COWORK.md
│   ├── Guaranty Agreement_COWORK.md
│   └── MANIFEST.md
```

The underscore prefix keeps the subfolder sorted out of the way. The
`_COWORK` suffix makes companion files immediately identifiable.

### Validation System

Every conversion runs a post-extraction validation:

1. **Page count match** — Compares extracted page count against the PDF's
   true page count (from metadata). Any mismatch flags missing pages.
2. **Content threshold** — Pages with fewer than 20 characters are flagged.
   Empty pages = `[IMAGE-ONLY]`. Thin pages = `[PARTIAL]`.
3. **Sequence integrity** — Verifies page markers are sequential (1, 2,
   3 … N) with no gaps.
4. **Gap classification** — Each flagged page gets a label:
   - `[IMAGE-ONLY]` — Signature page, scanned exhibit, handwritten notes
   - `[PARTIAL]` — Degraded text layer, partial OCR, mostly-image page
   - `[EXTRACTION FAILED]` — Method error on that specific page

### CONTENT GAPS Block

Every output file has a CONTENT GAPS block immediately after the file
header, before any page text. This block is the first thing Cowork sees
when it reads the file:

```
======================================================================
CONTENT GAPS (2 of 49 pages affected)
----------------------------------------------------------------------
PAGE  23:  [IMAGE-ONLY]             No extractable text — image/scan/signature page.
           → Review original PDF page 23 for this content.
PAGE  47:  [PARTIAL]                Only 12 characters extracted (expected ~2000+).
           → Review original PDF page 47 for this content.
----------------------------------------------------------------------
COMPLETENESS: 47/49 pages fully extracted (95.9%)
======================================================================
```

If there are no gaps:

```
======================================================================
CONTENT GAPS:  None — all 49 pages fully extracted
======================================================================
```

### MANIFEST.md

A manifest file in each `_cowork_txt/` folder tracks every conversion:

```markdown
# Cowork Conversion Manifest

Last updated: 2026-05-06 14:23

## Respondents Motion to DQ.pdf
- Converted: 2026-05-06 14:23
- Pages: 39/66 fully extracted | Method: pdftotext (CLI)
- Format: .md
- Gaps: PAGE 15 (partial), PAGE 16 (image-only), ...
- Completeness: 59.1%

## Guaranty Agreement.pdf
- Converted: 2026-05-06 14:20
- Pages: 8/8 fully extracted | Method: pdftotext (CLI)
- Format: .md
- Gaps: None
- Completeness: 100%
```

---

## How to Run

### Step 0: Pre-flight — check bash and dependencies

Before running the script, verify that bash is available and dependencies
are installed:

```bash
echo "bash OK" && which pdftotext && python3 -c "import pdfplumber; import pypdf; print('deps OK')"
```

If pdfplumber or pypdf are missing:
```bash
pip install pdfplumber pypdf --break-system-packages -q
```

Install PyMuPDF for Markdown output (required for heading/emphasis detection):
```bash
pip install PyMuPDF --break-system-packages --target /tmp/pymupdf_pkg -q
```

Set the Python path for the session:
```bash
export PYTHONPATH=/tmp/pymupdf_pkg:$PYTHONPATH
```

If PyMuPDF cannot be installed, the script still works — it falls back to
`.txt` output automatically. Markdown structural features (headings, bold,
italic, caption blocks) require PyMuPDF.

If bash itself is unavailable (returns "Workspace still starting" or
"Workspace unavailable" after 3 retries with 10-second waits), STOP.
Report to the user that the conversion requires bash and it's currently
down. Do NOT attempt to manually transcribe from the Read tool as a
workaround — that produces non-verbatim output and defeats the purpose.

### Step 1: Locate the script

The script is bundled with this skill. In the Cowork bash environment,
the skill directory is mounted read-only under the skills path. Locate it:
```bash
find /sessions -name "pdf_to_cowork_md.py" -path "*/skills/*" 2>/dev/null
```

Copy the script to a writable location before running:
```bash
cp "$(find /sessions -name 'pdf_to_cowork_md.py' -path '*/skills/*' 2>/dev/null | head -1)" /tmp/pdf_to_cowork_md.py
```

### Step 2: Convert a single PDF

```bash
PYTHONPATH=/tmp/pymupdf_pkg:$PYTHONPATH python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf"
```

Output saved to `_cowork_txt/` in the same parent directory as the PDF.
Format is auto-detected: `.md` for briefs/contracts/orders, `.txt` for
depositions and two-column documents.

To specify a custom output path:
```bash
PYTHONPATH=/tmp/pymupdf_pkg:$PYTHONPATH python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf" \
  --output "/custom/path/file_COWORK.md"
```

### Step 3: Force a specific format

```bash
# Force Markdown output (even for depositions)
python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf" --format md

# Force plain text output (skip Markdown inference)
python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf" --format txt

# Auto-detect (default)
python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf" --format auto
```

### Step 4: Batch-convert a matter folder

```bash
PYTHONPATH=/tmp/pymupdf_pkg:$PYTHONPATH python3 /tmp/pdf_to_cowork_md.py "/path/to/matter_folder/"
```

Converts every `.pdf` in the folder. Add `--skip-existing` to skip PDFs
that already have a companion output file:

```bash
python3 /tmp/pdf_to_cowork_md.py "/path/to/matter_folder/" --skip-existing
```

### Step 5: Force a specific extraction method

If `pdftotext` produces garbled output for a specific file:
```bash
python3 /tmp/pdf_to_cowork_md.py "/path/to/file.pdf" --method pdfplumber
```

### Step 6: Report results to the user

After conversion, always:

1. Read the MANIFEST.md and report: files converted, formats used,
   completeness percentages, any gaps detected.
2. For files with gaps: list the specific pages and gap types. Tell
   the user which original PDFs they need to review manually for those
   pages.
3. If any files failed entirely, report the error and which method(s)
   were attempted.

---

## CRITICAL: Reading _COWORK Files in Other Skills

**Every skill and every session that reads a `_COWORK.md` or `_COWORK.txt`
file MUST check the CONTENT GAPS block first.**

**Native-sidecar files carry an extra caveat.** If the header shows
`EXTRACTION METHOD: DISCO native text sidecar`, the text is verbatim native
content but **does not include the Bates number or any CONFIDENTIAL /
PROTECTED designation** — those are burned into the original PDF image
only. Never cite a produced document's Bates number or assert its
confidentiality status from a sidecar-sourced `_COWORK.md`; open the
original PDF for those.

Before relying on the text content:

1. Read the first 30 lines of the file (the header + CONTENT GAPS block).
2. If CONTENT GAPS shows gaps on pages relevant to your task, flag this
   to the user: "This file has content gaps on pages X, Y, Z. I'm working
   from the extracted text but those pages may have content I can't see.
   Check the original PDF."
3. Never silently work around missing content. If a gap falls in the
   middle of testimony you're analyzing, or in an exhibit you're
   summarizing, say so.

The MANIFEST.md file in each `_cowork_txt/` folder gives a quick overview
without opening individual files.

To programmatically check for gaps:

Using Cowork's Grep tool:
- Pattern: `CONTENT GAPS \(` — if it matches, there are gaps
- Pattern: `CONTENT GAPS:  None` — if it matches, the file is complete
- Pattern: `\[CONTENT GAP` — finds all inline gap markers in the body

---

## Using Converted Files in Cowork

Once the output file exists, Cowork can work with it using native tools:

**Read tool** — read the full file or specific line ranges:
```
Read the file at [path]/_cowork_txt/Motion_COWORK.md
```

**Grep tool** — search for terms across the document:
```
Grep for "fiduciary" in [path]/_cowork_txt/Motion_COWORK.md
```

**Read specific pages** — use the page markers to navigate:
- For `.md` files: `Grep for "<!-- Page 10 of"` to find the line number
- For `.txt` files: `Grep for "=== PAGE 10 of"` to find the line number
Then Read from that offset.

These are Cowork's native file tools — no bash required to USE the
converted files. Bash is only needed for the initial conversion.

---

## Two-Column Legal Transcripts

Court transcripts are often formatted in two columns per printed page.
These are auto-detected and routed to `.txt` output, where
`pdftotext -layout` preserves the spatial layout:

```
   1   PRESIDENT: After you have done your          1   the court should not make an order in the terms of
   2   introductions, Ms Moran, perhaps I might      2   Rule 11, which you all know about.
```

This is correct and fully searchable. Both columns are captured verbatim.

---

## Limitations

| Situation | Result |
|---|---|
| Text-based PDF (transcript, brief, judgment) | Full extraction — .md or .txt based on type |
| Scanned PDF with no embedded text | Image-only flags; original PDF required |
| Scanned production PDF WITH native `.txt` sidecar (DISCO/Relativity/Everlaw) | Verbatim native text used — no OCR; **Bates # and CONFIDENTIAL stamp NOT in text** (Bates is in filename) |
| Signatures on some pages | Text pages extracted; signature pages flagged |
| Password-protected PDF | All methods fail; user must provide unlocked version |
| Very large PDF (200+ pages) | pdftotext handles fine; pdfplumber may be slow |
| Exhibits with embedded images + text | Text extracted; image content not captured (flagged) |
| Bash unavailable | Conversion cannot run — report to user, do not workaround |
| OCR requested without permission | BLOCKED — never run tesseract/pytesseract/ocrmypdf unless the user explicitly approves in current session |
| Claude Read tool on source PDF | BLOCKED — never visually inspect PDF pages during conversion; read script output only |
| PyMuPDF unavailable | Falls back to .txt output (no Markdown structural features) |
| Garbled text on scanned/corrupted pages | Flagged as PARTIAL; original PDF review needed |

---

## Dependencies

- `pdftotext` — part of `poppler-utils` (typically pre-installed in Cowork sandbox)
- `pdfplumber` — Python package (`pip install pdfplumber --break-system-packages -q`)
- `pypdf` — Python package (`pip install pypdf --break-system-packages -q`)
- `PyMuPDF` — Python package (`pip install PyMuPDF --break-system-packages --target /tmp/pymupdf_pkg -q`).
  Required for Markdown output. If unavailable, script falls back to .txt.

---

## Output File Naming

```
[Original stem]_COWORK.md    (for briefs, motions, contracts, orders)
[Original stem]_COWORK.txt   (for depositions, two-column transcripts)
```

Examples:
- `Guaranty Agreement.pdf` → `Guaranty Agreement_COWORK.md`
- `Respondents Motion to DQ.pdf` → `Respondents Motion to DQ_COWORK.md`
- `Clark Condensed Transcript.pdf` → `Clark Condensed Transcript_COWORK.txt`

The `_COWORK` suffix makes these files immediately identifiable as
machine-extracted companions to the original PDFs.

---

## Testing Hooks (local/dev only)

- `structure_plaintext(text)` — text-only entry point (no PDF) used by the
  regression harness' do-no-harm test.
- `COWORK_IGNORE_SIDECAR=1` — environment variable that bypasses sidecar
  sourcing so the enrichment path can be scored against a ground-truth
  sidecar `.txt`. No effect unless set.

---

## CLI Reference

```
usage: pdf_to_cowork_md.py [-h] [--output OUTPUT] [--method {pdftotext,pdfplumber,pypdf}]
                           [--format {md,txt,auto}] [--skip-existing] [--no-subfolder]
                           [--json-report JSON_REPORT] input

positional arguments:
  input                 PDF file path, or directory to batch-convert

optional arguments:
  --output, -o          Output path (single-file mode only)
  --method, -m          Force text extraction method (default: auto-tries all)
  --format, -f          Output format: md (Markdown), txt (plain text), auto (detect). Default: auto.
  --skip-existing       Directory mode: skip PDFs that already have output
  --no-subfolder        Save output next to PDF instead of in _cowork_txt/
  --json-report         Path to write JSON report (for skill integration)
```
