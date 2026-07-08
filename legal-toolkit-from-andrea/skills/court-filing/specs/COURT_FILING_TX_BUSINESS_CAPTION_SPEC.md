# Texas Business Court Caption — Locked Spec

**Status:** Authoritative. The court-filing skill builds Texas Business Court captions from this spec, not from inferred examples or module-level heuristics.
**Last updated:** 2026-04-28
**Companion handoff:** (internal build reference — not needed to use this skill) (to be drafted with implementation work)
**Reference build script (python-docx safety net):** (internal build reference — not needed to use this skill) (to be drafted from `outputs/build_bc_caption_test_v2.py`)
**Reference build (1P + 1D, Voltas, with wrap handling):** (internal build reference — not needed to use this skill)
**Sibling specs:** `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md`, `specs/COURT_FILING_FEDERAL_CAPTION_SPEC.md` — this spec mirrors their structure.
**Companion module (non-caption BC content):** `specs/COURT_FILING_TX_BUSINESS_MODULE.md` — covers font override, heading style, signature block, certificates.

This spec covers Texas Business Court captions only. Texas state, federal, and NY Supreme Court captions are out of scope and have their own specs.

---

## Scope

This spec governs the entire caption block, top to bottom. The Business Court caption begins at the top of the caption table — there is no court name header and no cause number paragraph above the table. The caption template ends at the bottom of the table.

Out of scope (handled by `COURT_FILING_TX_BUSINESS_MODULE.md` or core SKILL.md):
- Title block (e.g., `DEFENDANT'S MOTION TO DISMISS`)
- Preamble line — never include "TO THE HONORABLE JUDGE OF SAID COURT:" in any of these filings. Hard rule, identical to state and federal.
- Body, headings, prayer, signature block, certificates, footer
- 14 pt font override (lives in the BC module)
- Heading 1 centering, signature block firm-name treatment, Certificate of Compliance, eFileTexas service language

---

## Page setup

- Paper: US Letter, portrait
- Margins: 1.0" top, bottom, left, right (1440 DXA)
- Content width: 6.5" (9360 DXA)

## Font (entire caption)

- **Century Schoolbook, 14 pt** — Business Court override. Sibling specs use 12 pt; the BC override applies to the entire filing and is documented in `COURT_FILING_TX_BUSINESS_MODULE.md`. The caption inherits the same 14 pt size.
- Caption block: single-spaced
- 0 pt before, 0 pt after on every paragraph in the caption

## Pre-table content

**None.** Unlike federal court (which has a centered court name header above the caption table) and TX state (which has a centered cause number paragraph above the caption table), the Business Court caption has zero paragraphs before the table. The table is the first body element after `<w:body>` opens (or after any prior decorative elements like header references; in practice, the body's first child is the caption table).

---

## 1. Caption table — structural properties

| Property | Value |
|---|---|
| Layout | 1 row, 3 columns |
| Total width | 6.5" (9360 DXA) |
| Column 1 (Parties) | 3.0" (4320 DXA) |
| Column 2 (Section signs) | 0.5" (720 DXA) |
| Column 3 (Court / division / cause) | 3.0" (4320 DXA) |
| Borders | All sides `nil` on table AND every cell |
| Cell vertical alignment | top |
| Cell margins (every cell) | 0 left, 0 right, 0 top, 0 bottom |
| Paragraphs per cell | Identical across all three columns — see row-count formula and wrap-handling rule below |

These widths align with the TX state and federal specs. The earlier Business Court widths of 4320 / 360 / 4410 (from the retired `(retired supplement)`) are **retired**. Do not re-introduce them.

The 0.5" middle column sits at the page midpoint (3.0" + 0.25" = 3.25", which is the midpoint of the 6.5" content width). Intentional and aesthetic.

Line count is identical across all three columns. Hard requirement so visual rows align horizontally regardless of font metric quirks.

---

## 2. Row-count formula (baseline, no wraps)

```
rows = 9 + 6 × (relationships − 1)
```

A "relationship" is a `v.` Each additional `v.` adds 6 paragraphs to every column.

| Relationships | Example | Baseline rows per cell |
|---|---|---|
| 1 | P v. D | 9 |
| 2 | P v. D v. 3PD | 15 |
| 3 | P v. D v. 3PD v. 4PD | 21 |

Multiple parties on the same side of a `v.` do not add rows. They go on a single paragraph, comma-separated.

**This formula assumes no party names, court designators, or division names wrap.** When any block wraps in any column, the wrap-handling rule (Section 3) adds paragraphs to all three columns to keep visual rows aligned.

---

## 3. Wrap handling (BC-specific concern at 14 pt)

At 14 pt in a 3.0" column, party names of 30+ characters and the BC court name `THE BUSINESS COURT OF TEXAS` (28 characters with spaces) commonly wrap. Without wrap handling, columns drift out of visual alignment — section signs end early, anchored content lands on the wrong row, role labels collide with continuations.

### Rule

Every paragraph in the caption table must fit on exactly one visual line. If a block of content would wrap, the author / build script must:

1. **Pre-break the wrapping block across multiple paragraphs in its source column** — one paragraph per visual line. The pre-break should occur at a natural word boundary that produces single-line paragraphs.
2. **Add the same number of empty padding paragraphs to the other two columns at the matching positions**, so all three columns retain identical paragraph counts.
3. Anchored content in column 3 (court name, division, cause number) shifts downward by the number of pre-break paragraphs that occur in column 1 above that anchor — see Section 6 for the explicit anchoring rule.

### Effect on row count

The row-count formula in Section 2 gives the baseline assuming no wraps. With wraps, the rule is:

> **All three columns must always have the same paragraph count, and every paragraph must fit on exactly one visual line.**

The row count emerges from compliance with that rule, not from a closed-form formula. A practical computation:

1. Identify each wrap block per column (plaintiff name, defendant name, role label, court name, etc.) and the number of pre-break paragraphs it requires.
2. Two wrap blocks in different columns that occupy the same anchor position (for example, the plaintiff name and the court name both at row 1) **overlap**: they share rows and contribute the maximum (not the sum) of their pre-break paragraphs to the total row count.
3. Two wrap blocks at non-overlapping positions (for example, the court name at row 1 and a defendant role-label wrap mid-table) **stack**: their pre-break paragraphs both add to the total row count, and each column receives empty padding at the other column's wrap position.

For the Voltas reference build (1P + 1D, plaintiff name and defendant name wrap in col 1, court name wraps in col 3):
- Baseline: 9 paragraphs per column.
- Plaintiff name wrap (col 1, row 1) and court name wrap (col 3, row 1) **overlap** — both occupy rows 1–2. Contributes +1 to total.
- Defendant name wrap (col 1, mid-table) **does not overlap** with anything in col 3. Contributes +1 to total.
- Total: 9 + 1 + 1 = 11 paragraphs per column. Col 3 receives empty padding at the defendant-name-wrap row.

The validator does not need to recompute this number. It checks that all three columns have equal paragraph count, that every paragraph fits on one visual line, and that anchored content lands at the correct positions.

### Pre-break examples

| Original (one paragraph, wraps) | Pre-broken (two paragraphs) |
|---|---|
| `VOLTAS INDUSTRIAL HOLDINGS, LP,` | `VOLTAS INDUSTRIAL` / `HOLDINGS, LP,` |
| `LARKFIELD TECHNOLOGIES, INC.,` | `LARKFIELD TECHNOLOGIES,` / `INC.,` |
| `THE BUSINESS COURT OF TEXAS` | `THE BUSINESS COURT OF` / `TEXAS` |
| `JOHN DOE, JANE DOE, AND ACME CORP.,` | `JOHN DOE, JANE DOE, AND ACME` / `CORP.,` |

### Trailing punctuation on pre-broken paragraphs

- Trailing comma stays on the **last** paragraph of the pre-broken block.
- All earlier pre-break paragraphs end with no terminal punctuation (they are mid-name continuations).
- Same applies to role labels that wrap (rare): only the last continuation carries the comma or period.

### When in doubt

If a build script cannot reliably predict whether a block wraps at 14 pt in a 3.0" column, fall back to the python-docx safety-net build (which renders deterministically and can be measured), and pre-break based on the rendered measurement. The docx-js build then mirrors the safety-net's pre-break decisions.

---

## 4. Column 1 — Parties

All paragraphs: Century Schoolbook 14 pt, single-spaced, 0 pt before, 0 pt after, plain weight (no bold, no italic, no underline) unless otherwise noted.

### Pattern for one relationship (9 baseline paragraphs)

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

Where any party-name paragraph would wrap at 14 pt in 3.0", apply the wrap rule from Section 3 — pre-break the name across consecutive paragraphs and pad columns 2 and 3 accordingly.

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

### Same-side parties

Same-side parties go on **one paragraph** (subject to wrap pre-break per Section 3), separated by commas, with **Oxford comma + ALL CAPS `AND`** before the last name. The list still gets a trailing comma. Identical to state and federal.

### Role labels

- Singular when one party on that side: `Plaintiff,` / `Defendant.`
- Plural when multiple parties on that side: `Plaintiffs,` / `Defendants.`
- Dual roles (e.g., a defendant who is also a third-party plaintiff): use a slash separator. Example: `Defendant / Third-Party Plaintiff,`.

### Punctuation rules

- Party-name paragraphs: **trailing comma** on the last continuation paragraph (when pre-broken) or on the only paragraph (when not pre-broken). Always present, even when the name itself ends in `INC.` or `LLC` (produces `INC.,` or `LLC,`).
- Role-label paragraphs: comma when followed by another role in the chain; period when the last role label in the chain.
- `v.` paragraphs: lowercase, plain weight, with trailing period. **NOT italic.** House-style choice — identical to state and federal.

---

## 5. Column 2 — Section signs

- One `§` (Unicode U+00A7) per paragraph
- Centered within the column
- Single-spaced, 0 pt before, 0 pt after, Century Schoolbook 14 pt, plain weight
- Number of paragraphs equals the actual row count after wrap padding (see Section 3)

Identical to state and federal except for the 14 pt size.

---

## 6. Column 3 — Court name, division, cause number

All paragraphs: Century Schoolbook 14 pt, single-spaced, 0 pt before, 0 pt after, **center-aligned**, plain weight (no bold, no italic, no underline).

The `jc=center` alignment is the BC-specific deviation from state (`jc=right`) and federal (`jc=right`). Verified against published Business Court filings.

### Three anchored items

| Content | Anchored to | Formatting |
|---|---|---|
| `THE BUSINESS COURT OF TEXAS` | Row 1 (top of cell) | ALL CAPS, no "IN" prefix, center-aligned, plain weight. **Pre-break to `THE BUSINESS COURT OF` / `TEXAS` when it wraps at 14 pt in 3.0".** |
| `[DIVISION NAME]` | One blank row below the last court-name continuation paragraph | ALL CAPS, center-aligned, plain weight. Examples: `FIRST DIVISION`, `THIRD DIVISION`, `EIGHTH DIVISION`, `ELEVENTH DIVISION`. |
| `Cause No. [YY-BCDDP-NNNN]` | The first `v.` row in column 1 (after wrap shifts) | Sentence case prefix `Cause No.`, then the BC cause number. Center-aligned, plain weight. |

All other rows in column 3 are empty.

### No "IN" prefix on the court name

The court name is `THE BUSINESS COURT OF TEXAS`, never `IN THE BUSINESS COURT OF TEXAS`. This differs from federal (`IN THE UNITED STATES DISTRICT COURT`) and matches every published BC filing reviewed.

### Anchoring with wrap handling

The anchor positions follow column 1 — they shift downward as column 1 acquires wrap padding paragraphs above each anchor. Concretely:

| Anchor | Position rule |
|---|---|
| Court name (first paragraph of the court-name block) | Always row 1. |
| Court name continuations (rows 2, 3, … if pre-broken) | Immediately follow the row 1 paragraph. |
| Division name | Two rows after the court name's last continuation row, leaving exactly one blank padding row between them. |
| Cause number | Same row as the first `v.` paragraph in column 1 (whatever row that ends up being after column 1's wrap padding). |

### Cause number format

Business Court cause numbers follow the pattern `YY-BCDDP-NNNN`:
- `YY` = 2-digit filing year
- `BC` = literal "BC"
- `DD` = 2-digit division number (01, 03, 04, 08, 11, etc.)
- `P` = panel letter (A, B, etc.)
- `NNNN` = 4-digit sequence number

Examples from published filings: `25-BC00X-0000`, `25-BC00X-0000`, `24-BC11A-0007`, `24-BC03A-0004`, `25-BC00X-0000`.

Regex (validator): `^\d{2}-BC\d{2}[A-Z]-\d{4}$`.

---

## 7. End of caption template

The caption template stops at the bottom of the caption table. No empty paragraph, no title block, no body, no preamble.

---

## Worked examples

### Example A — Voltas (1 relationship, 1P + 1D, party names wrap, court name wraps, 11 rows)

```
VOLTAS INDUSTRIAL              §            THE BUSINESS COURT OF
HOLDINGS, LP,                  §            TEXAS
                               §
   Plaintiff,                  §            FIRST DIVISION
                               §
v.                             §            Cause No. 25-BC00X-0000
                               §
LARKFIELD TECHNOLOGIES,        §
INC.,                          §
                               §
   Defendant.                  §
```

Per-cell paragraph count: 11 (= 9 baseline + 1 plaintiff-wrap + 1 defendant-wrap; the court-name wrap is absorbed by the plaintiff-wrap row 2 padding without adding to the total). Reference build: `2026.04.28 Texas Business Court Caption Spec Test - Voltas - v2.docx`.

### Example B — 1P + 1D, court name wraps but party names fit (10 rows)

Short party names that do not wrap, but the BC court name still wraps at 14 pt in 3.0".

```
ACME CORP.,                    §            THE BUSINESS COURT OF
                               §            TEXAS
                               §
   Plaintiff,                  §            EIGHTH DIVISION
                               §
v.                             §            Cause No. 25-BC00X-0000
                               §
WIDGET LLC,                    §
                               §
   Defendant.                  §
```

Wrap accounting: court-name wrap (col 3, row 1) does not overlap with any wrap in col 1, so it contributes +1. Total = 9 + 1 = 10 paragraphs per column. Col 1 gets one empty padding paragraph at row 3 (between the implicit row-2 blank after `ACME CORP.,` and the row-4 `Plaintiff,` role label) so that `Plaintiff,` aligns with `EIGHTH DIVISION` at row 4 and `v.` aligns with the cause number at row 6.

### Example C — 2P + 2D, court name wraps, party names fit (10 rows)

```
JOHN DOE AND JANE DOE,         §            THE BUSINESS COURT OF
                               §            TEXAS
                               §
   Plaintiffs,                 §            FIRST DIVISION
                               §
v.                             §            Cause No. 25-BC00X-0000
                               §
ACME LLC AND WIDGET INC.,      §
                               §
   Defendants.                 §
```

Same shape as Example B. Per-cell paragraph count: 10.

### Example D — 3 relationships, court name wraps, dual-role label wraps (17 rows)

P v. D v. 3PD with `Defendant / Third-Party Plaintiff,` as the dual role for the middle defendant. Both that role label and the court name wrap.

```
ACME CORP.,                    §            THE BUSINESS COURT OF
                               §            TEXAS
                               §
   Plaintiff,                  §            THIRD DIVISION
                               §
v.                             §            Cause No. 25-BC00X-0000
                               §
WIDGET LLC,                    §
                               §
   Defendant / Third-Party     §
   Plaintiff,                  §
                               §
v.                             §
                               §
GADGET INC.,                   §
                               §
   Third-Party Defendant.      §
```

Wrap accounting:
- Court-name wrap (col 3, row 1) — non-overlapping. Contributes +1.
- Dual-role wrap (col 1, mid-table at the D role-label position) — non-overlapping with anything in col 3. Contributes +1.
- Total: 15 (baseline) + 1 + 1 = 17 paragraphs per column.

Col 1 receives one empty padding paragraph near the top (so `Plaintiff,` aligns with `THIRD DIVISION` and `v.` aligns with the cause number). Col 3 receives one empty padding paragraph at the dual-role-wrap row.

---

## Validator checks (the only acceptable build is one that passes all of these)

These checks are owned by `validate_court_filing.py` when invoked with `--court-type business`, and run before any heuristic checks.

1. **No paragraphs before the caption table.** The body's first non-section element is the caption table itself.
2. **No centered court name header above the caption table** (BC differs from federal).
3. **No cause number paragraph above the caption table** (BC differs from TX state).
4. Caption table: exactly 1 row, exactly 3 columns.
5. Column widths: 4320 / 720 / 4320 DXA (small tolerance for rounding).
6. Cell borders: all sides `nil` on table and on every cell.
7. Cell margins: 0 on all sides for every cell.
8. Each cell paragraph count: identical across columns and at least equal to `9 + 6 × (relationships − 1)` (extra paragraphs allowed for wrap padding).
9. Column 2: every paragraph contains exactly one `§` character, centered.
10. **Column 3: every paragraph center-aligned** (jc=center) — BC-specific deviation from state and federal right-alignment.
11. **Column 3, row 1: starts with `THE BUSINESS COURT OF`** (allows wrap continuation into row 2 ending in `TEXAS`). No "IN" prefix.
12. **Column 3 contains exactly one division-name paragraph**, ALL CAPS, ending in `DIVISION`, anchored two rows below the last court-name continuation.
13. **Column 3 contains exactly one cause-number paragraph** matching `^Cause No\. \d{2}-BC\d{2}[A-Z]-\d{4}$`, anchored to the same row as the first `v.` in column 1.
14. Column 3, all other rows: empty.
15. Column 1 paragraph at the first `v.` position: plain weight, no italic, no bold, no underline.
16. **Font everywhere in the caption: Century Schoolbook 14 pt** (BC override of state and federal 12 pt).
17. No paragraph in the caption is double-spaced, has space-before, or has space-after.
18. Build inputs: `courtType="business"` present; `courtDivision` present and matches the division text in column 3; `causeNumber` present and matches the BC regex.
19. **Wrap-handling check:** every paragraph in the caption table fits on one visual line at 14 pt in its column. If any paragraph wraps, the build is rejected. (Implemented via a layout pass that measures each paragraph against its column width.)

---

## Pipeline notes

The court-filing skill's pipeline is unchanged: docx-js build → unpack → patch → pack → validate. The caption is generated inside step 1's docx-js script, translated line-for-line from the python-docx reference build at (internal build reference — not needed to use this skill).

If the validator's caption checks fail, the pipeline falls back to executing the python-docx reference script directly to rebuild the caption, then splices the byte-correct caption into the docx-js output and re-runs the validator. The python-docx safety net is a known-good source — the v2 reference build has been confirmed to render correctly.

`patch_court_filing.py` does not touch the caption. Its responsibilities (ListParagraph, signature line formatting) are all downstream of the caption.

---

## What is retired by this spec

- The caption section of (internal build reference — not needed to use this skill) (column widths 4320 / 360 / 4410, "approximate" anchor positions, no row-count formula, no wrap-handling rule). The supplement file is being preserved with a `RETIRED` banner pointing to this spec and to `COURT_FILING_TX_BUSINESS_MODULE.md`.
- The standalone (internal build reference — not needed to use this skill) (already retired in 2026-03-24 consolidation; mentioned here for completeness).
- Any caption-formatting logic inferred from an example .docx. No example ships with this skill; a sample you supply is a non-authoritative sanity reference for non-caption components only.

---

## Source authority

This caption format is derived from:
1. A published Texas Business Court brief on jurisdiction (confirm caption geometry against a current published Business Court filing before relying on this spec).
2. A published Texas Business Court motion to dismiss / plea to the jurisdiction (confirm caption geometry against a current published filing).
3. **Published Business Court opinions** from CreateAI v. Bot Auto TX (11th Div.), C Ten 31 v. Tarbox (3rd Div.), Energy Transfer v. Culberson (1st Div.), Crain v. Northern (8th Div.).

---

## Out of scope (handled in companion module)

- 14 pt font override (applies to the entire filing, not just the caption — see `COURT_FILING_TX_BUSINESS_MODULE.md`).
- Heading 1 centering and indent rules.
- Signature block firm-name treatment (bold + small caps), "ATTORNEYS FOR" line bold.
- Certificate of Compliance (required for all motions, responses, and replies; word-count language).
- Certificate of Service (eFileTexas language).
- Certificate signature underline+tab technique (shared across court types; see core SKILL.md and module).
