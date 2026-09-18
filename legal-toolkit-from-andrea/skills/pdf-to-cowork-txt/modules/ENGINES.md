# pdf-to-cowork-txt — Engines, Routing, Quality Gates, OCR

Reference module for `SKILL.md`. Nothing here changes how the script is
run; it explains what happens inside so gaps and engine choices can be
interpreted.

## Engine chain

Every structured engine is quality-gated against the pdftotext reference
extraction and falls through automatically:

```
.md   opendataloader-pdf  →  pymupdf4llm  →  legacy font renderer  →  plain text
.txt  opendataloader-pdf  →  pdftotext -layout
image-only pages (either format)  →  tesseract OCR in bash, then the chain above
```

**Quality gate.** An engine's output is accepted only if it keeps ≥ 70% of
the reference extraction's alphanumeric characters (pages that came from
OCR are excluded from the comparison) and shows no duplicate-text-layer
doubling (`page_doubling_fraction < 0.30`). Otherwise the next tier runs.
The header's `EXTRACTION METHOD:` records the winner, e.g.
`pdftotext (CLI) + tesseract OCR (4 pg) + opendataloader-pdf 2.5.9`.

### 1. opendataloader-pdf (first tier, both formats)

Apache 2.0 Java PDF-structure extractor (`pip install opendataloader-pdf`,
needs Java 11+ on PATH). XY-Cut++ reading order, heading hierarchy,
numbered / bulleted / nested lists, real Markdown tables (ruled by default;
`--table-method cluster` adds whitespace-clustered borderless tables),
`~~strikethrough~~` (`--detect-strikethrough`), off-page and hidden-text
content-safety filters. Local Java mode only — no hybrid/AI backend.

- **`.md`** uses ODL's Markdown writer, then normalizes for verbatim
  fidelity: the synthetic `- ` bullet is removed from the document's own
  ordinals (`- 1. text` → `1. text`, `- (a)` → `(a)`); empty tables from
  signature-page rectangles are dropped; per-word `~~x~~ ~~y~~` runs are
  merged. Inline **bold**/*italic* is NOT marked (ODL has no span-level
  emphasis) — use `--md-engine pymupdf4llm` when that matters.
- **`.txt`** is built from ODL's JSON element tree, not its text writer
  (2.5.x drops list content nested inside table cells — which is exactly
  what a boxed condensed transcript is). Child keys walked: `kids`, `list
  items`, `rows`, `cells`. Consecutive list items are single-spaced.
- **Condensed transcripts.** A 2-up/4-up sheet is a table of mini-pages.
  When every cell starts with its own `Page N` label, cells are emitted
  in label order (13, 14, 15, 16), not ODL's row order (13, 15, 14, 16).
  Validated on a real 33-page condensed transcript: character parity with
  pdftotext within 7% on every page.
- **ODL's own header/footer/watermark filter is kept OFF**
  (`include_header_footer=True`). It is position-based over the whole
  page and, on repeated-layout documents, dropped body text, Bates stamps
  and CONFIDENTIAL legends. The skill's zone-limited detector (below)
  handles headers/footers instead.
- `--use-struct-tree` (tagged-PDF reading order) is opt-in; it tested
  worse than XY-cut on a LibreOffice-tagged PDF.

### 2. pdftotext -layout (reference)

Verbatim reference for every quality gate and the `.txt` fallback
(side-by-side rendering of multi-column pages). Absent on most Windows
machines; pdfplumber, then pypdf, takes its place automatically (tier 6). Leading and trailing empty
pages are kept and flagged `[IMAGE-ONLY]` — only pdftotext's trailing
form-feed artifact is dropped. Duplicate text-layer dedup (line-level,
page-halves, doubled footers) runs on its output.

### 3. PyMuPDF (font metadata)

Body font, heading sizes, bold/italic spans, caption blocks, drop-cap
joining, duplicate-layer font detection, repeating header/footer
detection. Structural inference only; never the text source.

### 4. pymupdf4llm (second-tier `.md`)

Font-hierarchy headings, real Markdown tables, **bold**/*italic*, list
bullets. Its integrated OCR (on by default from 1.28) is hard-disabled
(`use_ocr=False, force_ocr=False`).

### 5. Legacy font renderer (third-tier `.md`)

Infers structure from PyMuPDF metadata: all-caps centered titles →
`## HEADING`; bold+larger → `##`/`###`; much larger → `#`; bold body text
→ `**bold**`; italic → `*italic*`; court captions (§ columns) →
blockquote. Conservative heuristics avoid false headings on numbered
paragraphs and all-caps body text.

### 6. pdfplumber / pypdf (reference fallbacks)

Used when pdftotext is absent. pypdf also builds the temporary working
copy for `--password` (decrypt once) and `--pages` (subset PDF).

### 7. Tesseract OCR

For pages the reference extraction found empty, near-empty (< 20 chars)
or failed: PyMuPDF renders at 300 dpi → `tesseract --psm 3` writes text +
TSV (word confidences and boxes) → the text takes the page's place.
Parallel via `--ocr-workers` (default 2); ~3–4 s per page.

When no `tesseract` executable is on PATH, `ocr_page` uses the Tesseract
engine compiled into PyMuPDF (`Page.get_textpage_ocr`) with the bundled
`tessdata/` folder beside the script, or `TESSDATA_PREFIX`. Same DPI, same
language flag, same stamp detection from word boxes (already in PDF
points). Word confidences are not exposed in this mode, so
`[OCR-LOW-CONFIDENCE]` cannot fire; EXTRACTION METHOD reads `via PyMuPDF,
no confidence scores` and OCR PAGES names the backend. Backend selection
is automatic — executable if present, else PyMuPDF; with neither and no
tessdata folder, `OCR skipped: ...` names all three remedies and image
pages stay `[IMAGE-ONLY]`.

- Mean word confidence < 60% → `[OCR-LOW-CONFIDENCE]` gap; text kept.
- < 20 alphanumeric characters after OCR → stays `[IMAGE-ONLY]`
  (photos, handwriting, blank slip-sheets).
- `--ocr force` OCRs every page (replaces a garbage vendor text layer);
  `--ocr off` restores the old flag-only behavior.
- Bates/legend stamps on OCR'd pages are detected from the TSV word boxes
  (pixels → PDF points, bottom-left origin) by the same detector as
  text-layer pages.

## Document-type routing

| Detected | Output | Signals |
|---|---|---|
| Deposition | `.txt` | monospaced font, line-number column, Q./A. markers |
| Two-column | `.txt` | balanced left/right block clusters |
| Everything else | `.md` | briefs, motions, orders, contracts, letters |

Why not Markdown for transcripts: line numbers and Q/A alignment are the
useful structure; headings and emphasis add nothing. With the default
`.txt` engine a condensed transcript reads mini-page by mini-page in
transcript order with original line breaks and running headers kept.
`--txt-engine pdftotext` gives the printed side-by-side layout instead.

## Header/footer stripping (`.md` only)

Repeating text in the top/bottom 60 pt zones is detected across pages
with page-number-insensitive matching (`Page 3 of 66`, `- Page 4`,
trailing numbers) and stripped from the body — including from raw-text
fallback pages. Table rows are never stripped. Threshold is 40% of pages
that have content in the zone. Stamps stripped here are preserved in the
page markers (see `OUTPUT-FORMAT.md`). `.txt` keeps everything verbatim.

## Validation

1. Page count: extracted pages vs the PDF's true count; missing pages →
   `[EXTRACTION FAILED]`.
2. Content threshold: empty page → `[IMAGE-ONLY]`, < 20 chars →
   `[PARTIAL]` (both then go to OCR under `--ocr auto`).
3. `[OCR-LOW-CONFIDENCE]` from the OCR pass.
4. Sequential page markers `1..N`.
