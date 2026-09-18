# pdf-to-cowork-txt — Output Format

What a `_COWORK.md` / `_COWORK.txt` file looks like and how to read it.

## Location and naming

```
<folder with the PDF>/
├── Guaranty Agreement.pdf
├── Clark Condensed Transcript.pdf
└── _cowork_txt/
    ├── Guaranty Agreement_COWORK.md
    ├── Clark Condensed Transcript_COWORK.txt
    ├── Guaranty Agreement_p5-7_COWORK.md      ← a --pages run
    └── MANIFEST.md
```

`--no-subfolder` writes beside the PDF; `--output` names the file.

## Header

```
======================================================================
FILE:              ACME ARB_0014410.pdf
SOURCE:            <path>
EXTRACTION METHOD: pdftotext (CLI) + tesseract OCR (4 pg) + opendataloader-pdf 2.5.9
OUTPUT FORMAT:     Markdown (.md)
TOTAL PAGES:       4
PAGE RANGE:        5-7 (of 40 in source PDF)          ← only with --pages
BATES RANGE:       ACME ARB_0014410 – ACME ARB_0014413 (4 pages stamped)
CONFIDENTIALITY:   CONFIDENTIAL - SUBJECT TO PROTECTIVE ORDER (3 pg), ...
OCR PAGES:         1-4 (tesseract, 300 dpi; text is OCR, not native)
CONVERTED:         2026-09-17 00:42
======================================================================
```

Then the CONTENT GAPS block, then a NOTE block naming the engine's
conventions, then the body.

## CONTENT GAPS block

```
======================================================================
CONTENT GAPS (2 of 49 pages affected)
----------------------------------------------------------------------
PAGE  23:  [IMAGE-ONLY]           No extractable text — image/scan/signature page.
          → Review original PDF page 23 for this content.
PAGE  47:  [OCR-LOW-CONFIDENCE]   OCR text (mean word confidence 48%) — verify against the image.
          → Review original PDF page 47 for this content.
----------------------------------------------------------------------
COMPLETENESS: 47/49 pages fully extracted (95.9%)
======================================================================
```

No gaps: `CONTENT GAPS:  None — all 49 pages fully extracted`.

| Type | Meaning | Body carries text? |
|---|---|---|
| `[IMAGE-ONLY]` | No text layer and OCR found nothing (photo, handwriting, blank slip-sheet) | No |
| `[PARTIAL]` | Under 20 characters and OCR did not improve it | What little there is |
| `[EXTRACTION FAILED]` | Page exists in the PDF but no method captured it | No |
| `[OCR-LOW-CONFIDENCE]` | OCR'd; mean word confidence under 60% | Yes — verify before quoting |

Each gapped page also carries an inline `[CONTENT GAP — PAGE N: ...]`
line in the body.

## Page markers

```
<!-- Page 7 of 40 -->                                                   .md
<!-- Page 7 of 40 | Bates EX_000129 | CONFIDENTIAL - SUBJECT TO PROTECTIVE ORDER -->
=== PAGE 7 of 40 ===                                                     .txt (between ==== rules)
=== PAGE 7 of 40 | Bates EX_000129 | HIGHLY CONFIDENTIAL - ATTORNEYS' EYES ONLY ===
```

With `--pages`, markers keep the ORIGINAL page numbers and `of` the
source total. Markdown markers are HTML comments: invisible when rendered,
searchable in raw text.

## Bates / confidentiality stamps

Production PDFs carry a Bates number and often a confidentiality legend in
the margin. Header/footer stripping would erase them from the body, so
they are captured into the page marker and the header instead. Source is
the opendataloader-pdf element tree (bounding box per element) or, on
OCR'd pages, tesseract's word boxes.

Detection rules: element within 100 pt of the top or bottom edge, under
90 characters, consisting of nothing but the stamp(s) and a page token.
Bates = `PREFIX[-_ ]digits`, 4+ digits (`EX_000129`, `ACME
ARB_0014410`, `GC-0004521`). Legends: CONFIDENTIAL, HIGHLY CONFIDENTIAL,
ATTORNEYS' EYES ONLY, SUBJECT TO PROTECTIVE ORDER, OUTSIDE COUNSEL EYES
ONLY, PROTECTED, PRIVILEGED, AEO, RESTRICTED — CAPS or Title Case only.
A number introduced by `NO.`, `CAUSE`, `CASE`, `DOCKET`, `INDEX`, `#` is a
docket number and ignored; spoken "marked confidential" in a transcript
line never matches. `--no-stamps` disables capture.

A marker with no stamp means none was detected on that page — not that
the page carries none. Lower-case legends, stamps without digits, and
mid-page placements are not detected.

## MANIFEST.md

```markdown
# Cowork Conversion Manifest

Last updated: 2026-09-17 00:42

## ACME ARB_0014410.pdf
- Converted: 2026-09-17 00:42
- Pages: 4/4 fully extracted | Method: pdftotext (CLI) + tesseract OCR (4 pg) + opendataloader-pdf 2.5.9
- Format: .md
- Gaps: None
- OCR: 4 page(s) via tesseract (1-4)
- Completeness: 100.0%

## Guaranty Agreement.pdf [pages 5-7]        ← --pages runs get their own entry
...
```

Entries are matched by whole heading line, so `X.pdf` and `X.pdf [pages
5-7]` never overwrite each other. Read MANIFEST.md, not the individual
files, for the report.

## Native text sidecar

Checked before any extraction. Locations, in order: `<parent>/Text/<stem>.txt`,
`<parent>/<stem>.txt`, and the load-file layout `VOL001/TEXT/<stem>.txt` or
`VOL001/TEXT/TEXT*/<stem>.txt` (any `TEXT*` folder one or two levels above
the PDF's `IMAGES/IMG001/`). When found, the file is written verbatim with
`EXTRACTION METHOD: DISCO native text sidecar (<name>)` and no extraction
or OCR runs. Set `COWORK_IGNORE_SIDECAR=1` to bypass.

**Sidecar caveat.** Native text is letter-perfect but carries no Bates
number and no confidentiality legend — those are burned into the image.
The Bates is in the filename; the legend is only in the original PDF.
Never cite Bates or assert confidentiality from a sidecar-sourced file.
Sidecars are skipped for `--pages` runs.

## Testing hooks (dev only)

`structure_plaintext(text)` — text-only entry point for the do-no-harm
regression test. `COWORK_IGNORE_SIDECAR=1` — score the extraction path
against a known-good sidecar.
