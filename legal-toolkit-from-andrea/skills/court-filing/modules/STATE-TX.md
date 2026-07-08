# Court-Filing Module — Texas State Court

## Court Type: `tx-state`

This module defines the caption layout, certificate language, and court-specific rules for **Texas state district court** filings. Read the core `SKILL.md` first — this file only contains Texas-specific overrides and additions.

**Validator/patch flag:** `--court-type tx-state`

---

## When This Applies

- The user says "state court" and the state is Texas (or unspecified — Texas is the default)
- The court is a Texas district court (e.g., "68th Judicial District Court", "116th District Court")
- The cause number follows Texas state format (e.g., `DC-00-00000`)
- The matter context identifies a Texas state court venue

---

## Caption Layout: Texas State Court

**Authoritative source:** `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md` (bundled with this skill).

The caption is built from that spec — not from inferred examples, not from the heuristics previously listed in this module. **Read the spec doc before generating any TX state caption.** The summary below is for orientation only; the spec governs in any conflict.

### Document Assembly Order

```
 1.  Cause Number (centered paragraph ABOVE table) ─┐
 2.  Caption Table (3-col, 1 row, borderless)       ├── governed by TX_STATE_CAPTION_SPEC.md
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
| Cause number | Centered, ALL CAPS, plain weight, above table |
| Empty paragraph between cause number and table | Yes, exactly one |
| Table layout | 1 row, 3 columns |
| Column widths | 4320 / 720 / 4320 DXA (3.0" / 0.5" / 3.0") |
| Borders | All sides `nil` on table AND every cell |
| Cell margins | 0 on all sides for every cell |
| Cell vertical alignment | top |
| Paragraphs per cell | `9 + 6 × (relationships − 1)` — identical across all three columns |
| Font | Century Schoolbook 12 pt everywhere |
| Spacing | Single, 0 pt before, 0 pt after on every paragraph |
| `v.` styling | Plain weight — **NOT italic**. Lowercase, trailing period. |
| Same-side parties | Single paragraph, comma-separated, Oxford comma + ALL CAPS `AND` before last name |
| Dual-role labels | Slash separator (e.g., `Defendant / Third-Party Plaintiff,`) |
| Column 3 anchor rows | Row 1 (court type), first `v.` row (district), last row (county) |

### Build implementation

The caption is generated inside the docx-js build script (step 1 of the pipeline), following the geometry in `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md` line-for-line.

If `validate_tx_state.py`'s caption checks fail, the pipeline auto-falls back to running the python-docx reference script and splicing the byte-correct caption into the docx-js output. See "Pipeline notes" in the spec doc.

The earlier column widths (4140 / 360 / 4860) and the "MIRROR vertically" anchoring heuristic are **retired**. Do not re-introduce them.

No example .docx ships with this skill. Never pattern-match a TX state caption against an example file — the bundled `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md` is authoritative.

---

## Signature Block

Uses the **default container table layout** from the core SKILL.md:
- Container table technique (two-column borderless table, left cell empty, right cell holds all sig content)
- Single-firm block (firm block from the toolkit profile)
- "ATTORNEYS FOR [CLIENT]" — bold, short collective designation
- No "DATED:" line before "Respectfully submitted,"

---

## Certificate of Conference (default ON for motions)

Set `includeCOConf = true` for all motions. Off for non-motions (declarations, exhibits).

### Default (all Texas state courts)

> "I hereby certify that I conferred with counsel for [nonmovant(s)] on [month and day], [year], who is [opposed/unopposed] to this Motion."

Fill in the nonmovant party description, conference date, and opposition status. Followed by the standard certificate signature (underline+tab technique, left=4680).

### Dallas County Variant

Dallas County district courts expect a more formal conference certification. Use this language when 