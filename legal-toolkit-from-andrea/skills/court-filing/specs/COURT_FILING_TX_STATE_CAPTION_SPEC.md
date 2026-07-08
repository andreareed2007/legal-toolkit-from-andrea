# Texas State Court Caption — Locked Spec

**Status:** Authoritative. The court-filing skill builds Texas state district court captions from this spec, not from inferred examples.
**Last updated:** 2026-04-27
**Companion handoff:** (internal build reference — not needed to use this skill)
**Reference build (1P + 1D, Acme):** (internal build reference — not needed to use this skill)
**Reference build script (python-docx, safety-net):** (internal build reference — not needed to use this skill)

This spec covers Texas state district court captions only. Federal, NY Supreme Court, and Texas Business Court captions are out of scope and have their own modules/specs.

---

## Scope

This spec governs everything from the cause number through the bottom of the caption table. The caption template ends at the bottom of the table.

Out of scope (handled by other parts of the skill, not this spec):
- Title block (e.g., `DEFENDANT'S MOTION TO COMPEL ARBITRATION`)
- Preamble line — **never include "TO THE HONORABLE JUDGE OF SAID COURT:" in any of these filings.** Hard rule.
- Body, headings, prayer, signature block, certificates, footer

---

## Page setup

- Paper: US Letter, portrait
- Margins: 1.0" top, bottom, left, right (1440 DXA)
- Content width: 6.5" (9360 DXA)

## Font (entire caption)

- **Century Schoolbook, 12 pt** — never Times New Roman
- Caption block: single-spaced
- 0 pt before, 0 pt after on every paragraph in the caption

---

## 1. Cause number block

- One paragraph
- Centered, ALL CAPS, plain weight (not bold, no underline, no italic)
- Single-spaced, 0 pt before, 0 pt after, Century Schoolbook 12 pt
- Text: `CAUSE NO. [number]` (e.g., `CAUSE NO. DC-00-00000`)
- Followed by **one empty paragraph** (single-spaced, 12 pt, Century Schoolbook) before the caption table

---

## 2. Caption table — structural properties

| Property | Value |
|---|---|
| Layout | 1 row, 3 columns |
| Total width | 6.5" (9360 DXA) |
| Column 1 (Parties) | 3.0" (4320 DXA) |
| Column 2 (Section signs) | 0.5" (720 DXA) |
| Column 3 (Court) | 3.0" (4320 DXA) |
| Borders | All sides `nil` on table AND every cell |
| Cell vertical alignment | top |
| Cell margins (every cell) | 0 left, 0 right, 0 top, 0 bottom |
| Paragraphs per cell | Identical across all three columns — see row-count formula below |

The 0.5" middle column sits at exact page midpoint (3.0" + 0.25" = 3.25", which is the midpoint of the 6.5" content width). Intentional and aesthetic.

Line count is identical across all three columns. Hard requirement so visual rows align horizontally regardless of font metric quirks.

---

## 3. Row-count formula

```
rows = 9 + 6 × (relationships − 1)
```

A "relationship" is a `v.` Each additional `v.` adds 6 paragraphs to every column.

| Relationships | Example | Rows per cell |
|---|---|---|
| 1 | P v. D | 9 |
| 2 | P v. D v. 3PD | 15 |
| 3 | P v. D v. 3PD v. 4PD | 21 |

Multiple parties on the same side of a `v.` do NOT add rows. They go on a single line, comma-separated.

The formula assumes no party names or court designators wrap. When any block wraps in any column, the wrap-handling rule (Section 3a) adds paragraphs to all three columns to keep visual rows aligned. The same rule appears in `COURT_FILING_FEDERAL_CAPTION_SPEC.md` and `COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md`.

---

## 3a. Wrap handling

For most TX state filings at 12 pt in a 3.0" column, party names and court designators fit on one line and the row-count formula in Section 3 holds. Long party names (e.g., multi-party plaintiffs joined with "AND" + Oxford comma, or LLCs with long entity names) can wrap at 12 pt and produce visual misalignment between columns: section signs end early, and column-3 anchors land on the wrong row.

### Rule

> Every paragraph in the caption table must fit on exactly one visual line, and all three columns must always have the same paragraph count.

When a block of content would wrap, the author / build script must:

1. **Pre-break the wrapping block across multiple paragraphs in its source column** — one paragraph per visual line, broken at a natural word boundary that produces single-line paragraphs.
2. **Add empty padding paragraphs to the other two columns at the matching positions**, so all three columns retain identical paragraph counts and visual rows align.
3. The column-3 court-designation anchors (Section 6) shift by the cumulative pre-break paragraphs that occur in column 1 above each anchor. The validator computes the actual anchor rows based on the wrap-padded layout.

### Effect on row count

The formula in Section 3 gives the **baseline** assuming no wraps. With wraps:

- Wrap blocks in different columns that occupy the **same** anchor position (e.g., a multi-party plaintiff list at row 1 wrapping in col 1 — there is no overlapping content in col 3 because col-3 line-1 `IN THE DISTRICT COURT` is short — so this scenario contributes only the col 1 wrap to the total).
- Wrap blocks in **different** positions stack — both add to the total row count, and each column receives empty padding at the other column's wrap position.

The validator does not need to recompute the total. It checks that all three columns have equal paragraph count, that every paragraph fits on one visual line, and that anchored content lands at the correct positions.

### Pre-break examples

| Original (one paragraph, wraps) | Pre-broken (two paragraphs) |
|---|---|
| `JOHN DOE, JANE DOE, AND ACME CORP.,` | `JOHN DOE, JANE DOE, AND ACME` / `CORP.,` |
| `ACME AUTOMOTIVE GROUP, LLC AND BETA MOTORS, INC.,` | `ACME AUTOMOTIVE GROUP, LLC AND BETA` / `MOTORS, INC.,` |
| `Defendant / Third-Party Plaintiff,` (long dual role) | `Defendant / Third-Party` / `Plaintiff,` |

### Trailing punctuation on pre-broken paragraphs

- Trailing comma stays on the last paragraph of the pre-broken block.
- All earlier pre-break paragraphs end with no terminal punctuation (mid-name or mid-label continuations).
- Same rule applies to role labels that wrap: only the last continuation carries the comma or period.

### When in doubt

If the build script cannot reliably predict whether a block wraps at 12 pt in a 3.0" column, fall back to the python-docx safety-net build (which renders deterministically and can be measured) and pre-break based on the rendered measurement. The docx-js build then mirrors the safety-net's pre-break decisions.

### Note on Example C and Acme

The Acme reference build (Example A above) does not exercise the wrap rule because all party names fit on one line at 12 pt. Example B's `JOHN DOE, JANE DOE, AND ACME CORP.,` does wrap and previously was documented as wrapping inside one paragraph; under this rule, it must be pre-broken into two paragraphs and the other columns padded accordingly.

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

Same-side parties go on **one paragraph**, separated by commas, with **Oxford comma + ALL CAPS `AND`** before the last name. The list still gets a trailing comma.

Examples:
- `JOHN DOE, JANE DOE, AND ACME CORP.,`
- `ACME AUTOMOTIVE GROUP, LLC AND BETA MOTORS, INC.,` (two parties: comma + AND, no Oxford comma needed)
- `JANE DOE,` (one party: just the trailing comma)

If the same-side list is long enough to wrap inside the 3.0" column, the **wrap-handling rule (Section 3a)** applies: pre-break the list across multiple paragraphs in column 1 and pad columns 2 and 3 with empty paragraphs at the matching positions to maintain identical paragraph counts.

### Role labels

- Singular when one party on that side: `Plaintiff,` / `Defendant.`
- Plural when multiple parties on that side: `Plaintiffs,` / `Defendants.`
- Dual roles when a party wears two hats (dual-role middle defendant): use a slash separator. Example: `Defendant / Third-Party Plaintiff,`. Wraps naturally if it exceeds the column width.

### Punctuation rules

- Party-name paragraphs: **trailing comma** always, even when the name itself ends in `INC.` or `LLC` (produces `INC.,` or `LLC,`).
- Role-label paragraphs: **comma** when followed by another role in the chain; **period** when the last role label in the chain.
- `v.` paragraphs: lowercase, plain weight, with trailing period. **NOT italic.** This is a deliberate departure from some Texas conventions — the configured house style is plain `v.`

---

## 5. Column 2 — Section signs

- One `§` (Unicode U+00A7) per paragraph
- Centered within the column
- Single-spaced, 0 pt before, 0 pt after, Century Schoolbook 12 pt, plain weight
- Number of paragraphs = number of paragraphs in column 1

---

## 6. Column 3 — Court designation

All paragraphs: Century Schoolbook 12 pt, single-spaced, 0 pt before, 0 pt after, **right-aligned** (text flush against the right margin), ALL CAPS, plain weight.

### Three-line court designation

The court designation always breaks into three lines:

1. `IN THE [COURT TYPE]` — for state district court, this is `IN THE DISTRICT COURT`
2. `[NTH] JUDICIAL DISTRICT` — e.g., `44TH JUDICIAL DISTRICT`, `68TH JUDICIAL DISTRICT`, `162ND JUDICIAL DISTRICT`
3. `[COUNTY] COUNTY, TEXAS` — e.g., `DALLAS COUNTY, TEXAS`

### Anchoring rules

The three court lines anchor to specific rows in the cell. **All other paragraphs in column 3 are empty.**

| Court line | Anchored to row |
|---|---|
| Line 1 (`IN THE DISTRICT COURT`) | Row 1 (top) |
| Line 2 (`[NTH] JUDICIAL DISTRICT`) | The **first** `v.` row in column 1 |
| Line 3 (`[COUNTY] COUNTY, TEXAS`) | Last row of the cell |

This rule is the same regardless of relationship count. The first `v.` is always row 5 (because the pattern before any `v.` is fixed at 4 paragraphs: name, blank, label, blank). So:

| Relationships | Line 1 row | Line 2 row | Line 3 row |
|---|---|---|---|
| 1 | 1 | 5 | 9 |
| 2 | 1 | 5 | 15 |
| 3 | 1 | 5 | 21 |

---

## 7. End of caption template

The caption template stops at the bottom of the caption table. No empty paragraph, no title block, no body, no preamble.

---

## Worked examples

### Example A — Acme (1 relationship, 1P + 1D, 9 rows)

```
                          CAUSE NO. DC-00-00000

ACME PARTNERS, INC.,                §            IN THE DISTRICT COURT
                                    §
   Plaintiff,                       §
                                    §
v.                                  §           44TH JUDICIAL DISTRICT
                                    §
SAMPLE DEFENDANT,                     §
                                    §
   Defendant.                       §            DALLAS COUNTY, TEXAS
```

Reference build: `2026.04.24 Texas State Court Caption Spec Test - Acme - v2.docx`. This has been confirmed to render correctly.

### Example B — Multi-plaintiff, single relationship (3P + 1D, 9 rows)

```
                          CAUSE NO. DC-26-XXXXX

JOHN DOE, JANE DOE, AND ACME       §            IN THE DISTRICT COURT
CORP.,                              §
                                    §
   Plaintiffs,                      §
                                    §
v.                                  §           44TH JUDICIAL DISTRICT
                                    §
SAMPLE DEFENDANT,                     §
                                    §
   Defendant.                       §            DALLAS COUNTY, TEXAS
```

Note: the plaintiff list `JOHN DOE, JANE DOE, AND ACME CORP.,` exceeds 3.0" at 12 pt. Per the wrap-handling rule (Section 3a), pre-break into two paragraphs (`JOHN DOE, JANE DOE, AND ACME` / `CORP.,`) and pad columns 2 and 3 with one empty paragraph each at the matching position. Final paragraph count: 10 per column (9 baseline + 1 wrap).

### Example C — dual-role (P v. D v. 3PD, 3 relationships, 15 rows)

```
                          CAUSE NO. DC-26-XXXXX

ACME MOTORS DIRECT, LLC,            §            IN THE DISTRICT COURT
                                    §
   Plaintiff,                       §
                                    §
v.                                  §           XXTH JUDICIAL DISTRICT
                                    §
SAMPLE MOTORS, INC.,                §
                                    §
   Defendant / Third-Party          §
   Plaintiff,                       §
                                    §
v.                                  §
                                    §
ACME FINANCE CO.,                   §
                                    §
   Third-Party Defendant.           §            DALLAS COUNTY, TEXAS
```

Column 3 anchors at row 1, row 5 (first `v.`), and row 15 (last row).

---

## Validator checks (the only acceptable build is one that passes all of these)

These checks are owned by `validate_tx_state.py` and run before any heuristic checks.

1. Cause number paragraph: centered, ALL CAPS, font = Century Schoolbook 12 pt, no bold/italic/underline.
2. One empty paragraph between the cause number and the caption table.
3. Caption table: exactly 1 row, exactly 3 columns.
4. Column widths: 4320 / 720 / 4320 DXA (small tolerance for rounding).
5. Cell borders: all sides `nil` on table and on every cell.
6. Cell margins: 0 on all sides for every cell.
7. Each cell paragraph count: identical across columns and equal to `9 + 6 × (relationships − 1)`.
8. Column 2: every paragraph contains exactly one `§` character, centered.
9. Column 3: every paragraph right-aligned (jc=right).
10. Column 1 paragraph at position 5 (`v.`): plain weight, no italic, no bold, no underline.
11. Font everywhere in the caption: Century Schoolbook 12 pt.
12. No paragraph in the caption is double-spaced, has space-before, or has space-after.
13. Column 3 rows that should be empty (every row except 1, 5, and last) contain no text.

---

## Pipeline notes

The court-filing skill's pipeline is unchanged: docx-js build → unpack → patch → pack → validate. The caption is generated inside step 1's docx-js script, translated line-for-line from the python-docx reference build at (internal build reference — not needed to use this skill).

If the validator's caption checks fail, the pipeline falls back to executing the python-docx reference script directly to rebuild the caption, then splices the byte-correct caption into the docx-js output and re-runs the validator. The python-docx safety net is a known-good source — we have confirmed its output renders correctly.

`patch_court_filing.py` does not touch the caption. Its responsibilities (ListParagraph, signature line formatting) are all downstream of the caption.

---

## What is retired by this spec

- Caption-formatting logic inferred from any example .docx. No example ships with this skill; a sample you supply is a non-authoritative sanity reference for non-caption components only, never the source of truth for a TX state caption decision.
- Any TX state caption rule in `modules/STATE-TX.md` that contradicts this spec. STATE-TX.md now defers to this spec for caption rules.

---

## Out of scope (separate locked specs to be written)

- Federal court captions
- Texas Business Court captions (current authority: (internal build reference — not needed to use this skill))
- New York Supreme Court captions
- Title block, body, signature block, certificates, footer (all live in core SKILL.md and the applicable module)
