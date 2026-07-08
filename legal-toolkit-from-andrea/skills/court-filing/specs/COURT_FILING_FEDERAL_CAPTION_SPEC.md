# Federal Court Caption — Locked Spec

**Status:** Authoritative. The court-filing skill builds U.S. District Court captions from this spec, not from inferred examples or module-level heuristics.
**Last updated:** 2026-04-27
**Companion handoff:** (internal build reference — not needed to use this skill)
**Reference build script (python-docx safety net):** (internal build reference — not needed to use this skill)
**Sibling spec:** `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md` (TX state) — this spec mirrors its structure and house-style rules.

This spec covers U.S. District Court captions only.  Texas state, NY Supreme Court, and Texas Business Court captions are out of scope and have their own modules/specs.

---

## Scope

This spec governs everything from the centered court name header through the bottom of the caption table.  The caption template ends at the bottom of the table.

Out of scope (handled by other parts of the skill, not this spec):
- Title block (e.g., `DEFENDANT'S MOTION TO COMPEL ARBITRATION`)
- Preamble line — **never include "TO THE HONORABLE JUDGE OF SAID COURT:" in any of these filings.** Hard rule, identical to TX state.
- Body, headings, prayer, signature block, certificates, footer

---

## Page setup

- Paper: US Letter, portrait
- Margins: 1.0" top, bottom, left, right (1440 DXA)
- Content width: 6.5" (9360 DXA)

## Font (entire caption)

- **Century Schoolbook, 12 pt** — never Times New Roman
- Caption block: single-spaced
- 0 pt before, 0 pt after on every paragraph in the caption (with the documented exception of the court-name-header paragraph, which carries `spaceAfter=240`)

---

## 1. Court name header (centered, ABOVE the table)

Federal captions begin with a centered, three-line court name header above the caption table.  The header is built as a single paragraph with two `<w:br/>` line breaks separating the lines.  The entire paragraph is bold, centered, ALL CAPS, Century Schoolbook 12 pt.

### Three lines

1. `IN THE UNITED STATES DISTRICT COURT`
2. `FOR THE [DISTRICT]`  — e.g., `FOR THE NORTHERN DISTRICT OF TEXAS`, `FOR THE SOUTHERN DISTRICT OF NEW YORK`
3. `[DIVISION]`  — e.g., `DALLAS DIVISION`, `FORT WORTH DIVISION`

### Division line

The division line is **always included** when the district has formal divisions (NDTX, EDTX, SDTX, NDIL, etc.).  When the district has no divisions (SDNY, EDNY, DDC, DCT, etc.), the division line is omitted and the header is two lines.

The build input `divisionName` is required for districts with divisions; pass an empty string to suppress the line for districts without divisions.

### Spacing

- One single paragraph
- `spacing: { after: 240, line: 240, lineRule: "auto" }`
- `contextualSpacing: true`
- All three TextRuns: `font: "Century Schoolbook"`, `size: 24`, `bold: true`

### Followed by

- **Zero** empty paragraphs between the header and the caption table.  The 240 DXA `spaceAfter` provides the gap.

---

## 2. Caption table — structural properties

| Property | Value |
|---|---|
| Layout | 1 row, 3 columns |
| Total width | 6.5" (9360 DXA) |
| Column 1 (Parties) | 3.0" (4320 DXA) |
| Column 2 (Section signs) | 0.5" (720 DXA) |
| Column 3 (Case number) | 3.0" (4320 DXA) |
| Borders | All sides `nil` on table AND every cell |
| Cell vertical alignment | top |
| Cell margins (every cell) | 0 left, 0 right, 0 top, 0 bottom |
| Paragraphs per cell | Identical across all three columns — see row-count formula below |

These widths match the TX state spec exactly.  The earlier 4140 / 360 / 4860 widths in COURT-FEDERAL.md are **retired**.  Do not re-introduce them.

Line count is identical across all three columns.  Hard requirement so visual rows align horizontally regardless of font metric quirks.

---

## 3. Row-count formula

```
rows = 9 + 6 × (relationships − 1)
```

A "relationship" is a `v.`  Each additional `v.` adds 6 paragraphs to every column.

| Relationships | Example | Rows per cell |
|---|---|---|
| 1 | P v. D | 9 |
| 2 | P v. D v. 3PD | 15 |
| 3 | P v. D v. 3PD v. 4PD | 21 |

Multiple parties on the same side of a `v.` do NOT add rows.  They go on a single line, comma-separated.

Identical to TX state.

The formula assumes no party names wrap.  When any block wraps, the wrap-handling rule (Section 3a) adds paragraphs to all three columns to keep visual rows aligned.  The same rule appears in `COURT_FILING_TX_STATE_CAPTION_SPEC.md` and `COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md`.

---

## 3a. Wrap handling

For most federal filings at 12 pt in a 3.0" column, party names fit on one line and the row-count formula in Section 3 holds.  Federal column 3 carries only a single-line case number, so wraps in column 3 are rare.  Wraps in column 1 are the typical concern (e.g., multi-party plaintiff lists with "AND" + Oxford comma or long entity names).

### Rule

> Every paragraph in the caption table must fit on exactly one visual line, and all three columns must always have the same paragraph count.

When a block of content would wrap, the author / build script must:

1. **Pre-break the wrapping block across multiple paragraphs in its source column** — one paragraph per visual line, broken at a natural word boundary that produces single-line paragraphs.
2. **Add empty padding paragraphs to the other two columns at the matching positions**, so all three columns retain identical paragraph counts and visual rows align.
3. The case-number anchor in column 3 (Section 6) remains at the first `v.` row in column 1 — when column 1 acquires wrap padding above the first `v.`, the case number's row index shifts down accordingly.

### Effect on row count

The formula in Section 3 gives the **baseline** assuming no wraps.  With wraps:

- Wrap blocks in different columns at the **same** position contribute the maximum (not the sum) of their pre-break paragraphs to the total.
- Wrap blocks at **different** positions stack — both add to the total, and each column receives empty padding at the other column's wrap position.

The validator does not need to recompute the total.  It checks that all three columns have equal paragraph count, that every paragraph fits on one visual line, and that anchored content lands at the correct positions.

### Pre-break examples

| Original (one paragraph, wraps) | Pre-broken (two paragraphs) |
|---|---|
| `ACME HOLDCO, LTD. AND BETA HCMS LP,` | `ACME HOLDCO, LTD. AND BETA` / `HCMS LP,` |
| `OMEGA BANK, SSB AND DELTA HOLDINGS, INC.,` | `OMEGA BANK, SSB AND DELTA HOLDINGS,` / `INC.,` |
| `Defendant / Third-Party Plaintiff,` (long dual role) | `Defendant / Third-Party` / `Plaintiff,` |

### Trailing punctuation on pre-broken paragraphs

- Trailing comma stays on the last paragraph of the pre-broken block.
- All earlier pre-break paragraphs end with no terminal punctuation (mid-name or mid-label continuations).
- Same rule applies to role labels that wrap: only the last continuation carries the comma or period.

### When in doubt

If the build script cannot reliably predict whether a block wraps at 12 pt in a 3.0" column, fall back to the python-docx safety-net build (which renders deterministically and can be measured) and pre-break based on the rendered measurement.  The docx-js build then mirrors the safety-net's pre-break decisions.

### Note on Example C

Example C (`ACME HOLDCO, LTD. AND BETA HCMS LP,`) was previously documented as wrapping inside one paragraph.  Under this rule, it must be pre-broken into two paragraphs and the other columns padded accordingly.  The same applies to the defendant list in that example.  Final paragraph count: 11 per column (9 baseline + 1 plaintiff wrap + 1 defendant wrap).

---

## 4. Column 1 — Parties

All paragraphs: Century Schoolbook 12 pt, single-spaced, 0 pt before, 0 pt after, plain weight (no bold, no italic, no underline) unless otherwise noted.

### Pattern for one relationship (9 paragraphs)

| # | Content | Alignment | Indent |
|---|---|---|---|
| 1 | `[PLAINTIFF NAMES],` | Left | None |
| 2 | (empty) | Left | None |
| 3 | `[Plaintiff role label],` | Left | 0.5" |
| 4 | (empty) | Left | None |
| 5 | `v.` | Left | None |
| 6 | (empty) | Left | None |
| 7 | `[DEFENDANT NAMES],` | Left | None |
| 8 | (empty) | Left | None |
| 9 | `[Defendant role label].` | Left | 0.5" |

### Pattern for additional relationships

For each `v.` beyond the first, append 6 paragraphs to column 1:

| Offset | Content | Alignment | Indent |
|---|---|---|---|
| +1 | (empty) | Left | None |
| +2 | `v.` | Left | None |
| +3 | (empty) | Left | None |
| +4 | `[NEXT PARTY NAMES],` | Left | None |
| +5 | (empty) | Left | None |
| +6 | `[Next party role label],` (or `.` if last) | Left | 0.5" |

The last role label always ends with a period; all earlier role labels end with a comma.

### Same-side parties (multiple plaintiffs, defendants, etc.)

Same-side parties go on **one paragraph**, separated by commas, with **Oxford comma + ALL CAPS `AND`** before the last name.  The list still gets a trailing comma.

Examples:
- `JOHN DOE, JANE DOE, AND ACME CORP.,`
- `ACME CORP, LLC AND BETA HOLDINGS, INC.,` (two parties: comma + AND, no Oxford comma needed)
- `SECOND DEFENDANT,` (one party: just the trailing comma)

If the same-side list is long enough to wrap inside the 3.0" column, the **wrap-handling rule (Section 3a)** applies: pre-break the list across multiple paragraphs in column 1 and pad columns 2 and 3 with empty paragraphs at the matching positions to maintain identical paragraph counts.

### Role labels

- Singular when one party on that side: `Plaintiff,` / `Defendant.`
- Plural when multiple parties on that side: `Plaintiffs,` / `Defendants.`
- Dual roles (a party wearing two hats — e.g., a defendant who is also a third-party plaintiff): use a slash separator.  Example: `Defendant / Third-Party Plaintiff,`.  Wraps naturally if it exceeds the column width.

### Punctuation rules

- Party-name paragraphs: **trailing comma** always, even when the name itself ends in `INC.` or `LLC` (produces `INC.,` or `LLC,`).
- Role-label paragraphs: **comma** when followed by another role in the chain; **period** when the last role label in the chain.
- `v.` paragraphs: lowercase, plain weight, with trailing period.  **NOT italic.**  House-style choice — identical to TX state.

---

## 5. Column 2 — Section signs

- One `§` (Unicode U+00A7) per paragraph
- Centered within the column
- Single-spaced, 0 pt before, 0 pt after, Century Schoolbook 12 pt, plain weight
- Number of paragraphs = number of paragraphs in column 1

Identical to TX state.

---

## 6. Column 3 — Case number

All paragraphs: Century Schoolbook 12 pt, single-spaced, 0 pt before, 0 pt after, **right-aligned** (text flush against the right margin), plain weight (no bold, no italic, no underline).

### Single-line case-number block

The case number is a **single line** in the form:

```
Civil Action No. [NUMBER]
```

Examples:
- `Civil Action No. 3:00-CV-00000-X`
- `Civil Action No. 1:00-cv-00000-XX`
- `Civil Action No. 4:00-CV-00000-X`

The judge designation is encoded in the trailing letter(s) of the case number itself (assigned by the clerk).  Do not add a separate "Judge [Name]" line.

If the district uses "Case No." instead of "Civil Action No." (some non-civil postures, some local rules), substitute the prefix accordingly.  The default is "Civil Action No." for civil filings.

### Anchor row

The case number anchors to **the first `v.` row** in column 1.  The first `v.` is always row 5 by formula (because the pattern before the first `v.` is fixed at 4 paragraphs: name, blank, label, blank).

| Relationships | Anchor row |
|---|---|
| 1 | 5 |
| 2 | 5 |
| 3 | 5 |

All other paragraphs in column 3 are **empty**.  Single-line case number means rows 1–4 are blank, row 5 carries the case number, and rows 6 through last are blank.

### Why row 5 (and not row 1)

Row 5 (first `v.`) puts the case number in the visual center of the parties block, mirroring the TX state column-3 line-2 anchor.  This gives a predictable, balanced appearance regardless of relationship count.  Row 1 placement was considered and rejected — it visually competes with the centered court-name header above the table.

---

## 7. End of caption template

The caption template stops at the bottom of the caption table.  No empty paragraph, no title block, no body, no preamble.

---

## Worked examples

### Example A — Acme Corp (1 relationship, 1P + 1D, 9 rows)

```
                IN THE UNITED STATES DISTRICT COURT
                  FOR THE NORTHERN DISTRICT OF TEXAS
                            DALLAS DIVISION


ACME CORP, LLC,                §
                               §
   Plaintiff,                  §
                               §
v.                             §       Civil Action No. 3:00-CV-00000-X
                               §
SECOND DEFENDANT,                §
                               §
   Defendant.                  §
```

### Example B — Acme Corp with Third-Party Defendant (2 relationships, 15 rows)

```
                IN THE UNITED STATES DISTRICT COURT
                  FOR THE NORTHERN DISTRICT OF TEXAS
                            DALLAS DIVISION


ACME CORP, LLC,                §
                               §
   Plaintiff,                  §
                               §
v.                             §       Civil Action No. 3:00-CV-00000-X
                               §
SECOND DEFENDANT,                §
                               §
   Defendant / Third-Party     §
   Plaintiff,                  §
                               §
v.                             §
                               §
SAMPLE DEFENDANT,                §
                               §
   Third-Party Defendant.      §
```

Column 3 still anchors only at row 5 (first `v.`); rows 1–4 and 6–15 are all empty.

### Example C — Multi-plaintiff, single relationship (2P + 2D, 9 rows)

```
                IN THE UNITED STATES DISTRICT COURT
                  FOR THE NORTHERN DISTRICT OF TEXAS
                            DALLAS DIVISION


ACME HOLDCO, LTD. AND BETA   §
HCMS LP,                        §
                               §
   Plaintiffs,                  §
                               §
v.                             §       Civil Action No. 1:00-cv-00000
                               §
OMEGA BANK, SSB AND DELTA       §
HOLDINGS, INC.,                §
                               §
   Defendants.                 §
```

Two-party plaintiff list (`ACME HOLDCO, LTD. AND BETA HCMS LP,`) and two-party defendant list (`OMEGA BANK, SSB AND DELTA HOLDINGS, INC.,`) each exceed 3.0" at 12 pt.  Per the wrap-handling rule (Section 3a), pre-break each into two paragraphs and pad the other columns with empty paragraphs at the matching positions.  Final paragraph count: 11 per column (9 baseline + 1 plaintiff wrap + 1 defendant wrap).

---

## Validator checks (the only acceptable build is one that passes all of these)

These checks are owned by `validate_federal.py` and run before any heuristic checks.

1. Court name header paragraph exists immediately above the caption table, centered, bold, ALL CAPS, font = Century Schoolbook 12 pt.
2. Court name header contains `IN THE UNITED STATES DISTRICT COURT` on its first line.
3. Court name header has either two or three lines (separated by `<w:br/>`); three lines required when `divisionName` is non-empty.
4. Zero empty paragraphs between the court name header and the caption table.
5. Caption table: exactly 1 row, exactly 3 columns.
6. Column widths: 4320 / 720 / 4320 DXA (small tolerance for rounding).
7. Cell borders: all sides `nil` on table and on every cell.
8. Cell margins: 0 on all sides for every cell.
9. Each cell paragraph count: identical across columns and equal to `9 + 6 × (relationships − 1)`.
10. Column 2: every paragraph contains exactly one `§` character, centered.
11. Column 3: every paragraph right-aligned (jc=right).
12. Column 1 paragraph at position 5 (`v.`): plain weight, no italic, no bold, no underline.
13. Column 3 paragraph at position 5: contains `Civil Action No.` (or `Case No.` if explicitly configured) followed by a case number string.
14. Column 3 rows that should be empty (every row except row 5) contain no text.
15. Font everywhere in the caption: Century Schoolbook 12 pt.
16. No paragraph in the caption is double-spaced, has space-before, or has space-after (with the documented exception of the court-name-header paragraph's `spaceAfter=240`).

---

## Pipeline notes

The court-filing skill's pipeline is unchanged: docx-js build → unpack → patch → pack → validate.  The caption is generated inside step 1's docx-js script, translated line-for-line from the python-docx reference build at (internal build reference — not needed to use this skill).

If the validator's caption checks fail, the pipeline falls back to executing the python-docx reference script directly to rebuild the caption, then splices the byte-correct caption into the docx-js output and re-runs the validator.  The python-docx safety net is a known-good source for federal captions.

`patch_court_filing.py` does not touch the caption.  Its responsibilities (ListParagraph, signature line formatting) are all downstream of the caption.

---

## What is retired by this spec

- The 4140 / 360 / 4860 column widths in COURT-FEDERAL.md.  Replaced by 4320 / 720 / 4320.
- The vague "row corresponding to the first party name" anchor for the case number.  Replaced by row 5 (first `v.` row).
- The reliance on inferred examples for the court name header phrasing.  Replaced by the locked three-line format with explicit division-line handling.
- Caption-formatting logic inferred from any example .docx. No example ships with this skill; if you supply your own, it is a non-authoritative sanity reference for non-caption components only, never the source of truth for a federal caption decision.
- Any federal caption rule in `modules/COURT-FEDERAL.md` that contradicts this spec.  COURT-FEDERAL.md now defers to this spec for caption rules.

---

## Out of scope (covered elsewhere)

- Signature block (default container-table layout from core SKILL.md)
- Certificate of Conference (federal practice varies by judge — see COURT-FEDERAL.md)
- Certificate of Service (CM/ECF language — see COURT-FEDERAL.md)
- Title block, body, prayer, footer — all live in core SKILL.md
- Texas state captions — see `COURT_FILING_TX_STATE_CAPTION_SPEC.md`
- NY Supreme Court captions — separate locked spec to be written
- Texas Business Court captions — separate locked spec to be written
