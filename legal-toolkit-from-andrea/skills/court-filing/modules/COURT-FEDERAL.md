# Court-Filing Module — Federal Court

## Court Type: `federal`

This module defines the caption layout, certificate language, and court-specific rules for **United States District Court** filings.  Read the core `SKILL.md` first — this file only contains federal-specific overrides and additions.

**Validator/patch flag:** `--court-type federal`

---

## When This Applies

- The user says "federal court"
- The court is a U.S. District Court (e.g., "Northern District of Texas", "Southern District of New York")
- The cause number follows federal format (e.g., `3:24-CV-00123-D`, `1:26-cv-01652`)

---

## Caption Layout: Federal Court

**Authoritative source:** `specs/COURT_FILING_FEDERAL_CAPTION_SPEC.md` (bundled with this skill).

The caption is built from that spec — not from inferred examples, not from the heuristics previously listed in this module.  **Read the spec doc before generating any federal caption.**  The summary below is for orientation only; the spec governs in any conflict.

### Document Assembly Order

```
 1.  Court Name Header (centered paragraph ABOVE table) ─┐
 2.  Caption Table (3-col, 1 row, borderless)            ├── governed by FEDERAL_CAPTION_SPEC.md
                                                          ┘
 3.  Document Title (DocumentTitle style — bold, all caps, underline, centered)
 4.  I. Summary of the Motion (Heading 1, body = BodyTextIndent, NOT ListParagraph)
 5.  II+ Remaining Sections (Heading 1/2/3, body = ListParagraph, continuous numbering)
 6.  Signature Block
 7.  [If includeCOConf] Certificate of Conference
 8.  [If includeCOS] Certificate of Service
```

**Hard rule:** Never include a `TO THE HONORABLE JUDGE OF SAID COURT:` preamble in these filings.

### Caption — locked-spec summary (orientation only — read spec for full rules)

| Property | Value |
|---|---|
| Court name header | 3-line centered paragraph with `<w:br/>` line breaks; bold, ALL CAPS, Century Schoolbook 12 pt; `spaceAfter=240` |
| Header line 1 | `IN THE UNITED STATES DISTRICT COURT` |
| Header line 2 | `FOR THE [DISTRICT]` |
| Header line 3 | `[DIVISION]` (omitted when district has no formal divisions) |
| Empty paragraph between header and table | None — the 240 DXA `spaceAfter` provides the gap |
| Table layout | 1 row, 3 columns |
| Column widths | 4320 / 720 / 4320 DXA (3.0" / 0.5" / 3.0") |
| Borders | All sides `nil` on table AND every cell |
| Cell margins | 0 on all sides for every cell |
| Cell vertical alignment | top |
| Paragraphs per cell | `9 + 6 × (relationships − 1)` — identical across all three columns |
| Font | Century Schoolbook 12 pt everywhere |
| Spacing | Single, 0 pt before, 0 pt after on every paragraph (header is the only documented exception) |
| `v.` styling | Plain weight — **NOT italic**.  Lowercase, trailing period. |
| Same-side parties | Single paragraph, comma-separated, Oxford comma + ALL CAPS `AND` before last name |
| Dual-role labels | Slash separator (e.g., `Defendant / Third-Party Plaintiff,`) |
| Column 3 anchor | Single anchor at first `v.` row (row 5).  All other column-3 rows blank. |
| Case number format | Single line: `Civil Action No. [NUMBER]` (right-aligned) |

### Build implementation

The caption is generated inside the docx-js build script (step 1 of the pipeline), following the geometry in `specs/COURT_FILING_FEDERAL_CAPTION_SPEC.md` line-for-line.

If `validate_federal.py`'s cap