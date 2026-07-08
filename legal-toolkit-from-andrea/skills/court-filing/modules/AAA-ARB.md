# Court-Filing Module — AAA Arbitration

## Court Type: `aaa-arb`

This module defines the caption layout, certificate language, and forum-specific rules for **American Arbitration Association** filings. Read the core `SKILL.md` first — this file only contains AAA-specific overrides and additions.

**Validator/patch flag:** `--court-type aaa-arb`

---

## When This Applies

- The user says "arbitration" or "AAA"
- The case number follows AAA format (e.g., `01-25-0001-5229`)
- The matter context identifies an AAA proceeding
- The filing references AAA Commercial Arbitration Rules

---

## Forum Header

AAA filings use a **forum header** above the caption table (analogous to the federal court name header). This is a single centered paragraph:

```
BEFORE THE AMERICAN ARBITRATION ASSOCIATION
```

**Formatting:**
- Centered (`jc=center`)
- Plain weight — **NOT bold** (differs from federal court header, which is bold)
- ALL CAPS (text typed uppercase)
- Century Schoolbook, 12pt
- `spaceAfter=240` (12pt gap before the caption table)
- `spaceBefore=0`
- Single line spacing

**No empty paragraph** between the forum header and the caption table. The `spaceAfter=240` provides the gap (same pattern as federal).

---

## Caption Layout: AAA Arbitration

### Document Assembly Order

```
 1.  Forum Header (centered paragraph ABOVE table)
 2.  Caption Table (3-col, 1 row, borderless)
 3.  Document Title (DocumentTitle style — bold, all caps, underline, centered)
 4.  [If includeSummary] I. Summary (Heading 1, body = BodyTextIndent, NOT ListParagraph)
 5.  [Remaining Sections] (Heading 1/2/3, body = ListParagraph, continuous numbering)
 6.  Signature Block
 7.  [If includeCOS] Certificate of Service
```

**No Certificate of Conference.** `includeCOConf = false` — always. Arbitrations do not operate under local rules requiring a conference certificate.

**No Certificate of Compliance.** `includeCOC = false` — always.

**Summary section:** No default. Ask at invocation: "Does this filing need a Summary section?" Some arbitration motions warrant one; procedural filings typically do not.

### Caption Table

| Property | Value |
|---|---|
| Table layout | 1 row, 3 columns |
| Column widths | 4320 / 720 / 4320 DXA (3.0" / 0.5" / 3.0") |
| Borders | All sides `nil` on table AND every cell |
| Cell margins | 0 on all sides for every cell |
| Cell vertical alignment | top |
| Paragraphs per cell | `9 + 6 × (relationships − 1)` — identical across all three columns |
| Font | Century Schoolbook 12 pt everywhere |
| Spacing | Single, 0 pt before, 0 pt after on every paragraph |
| `v.` styling | Plain weight — **NOT italic**. Lowercase, trailing period. |

### Column 1 — Parties

Same layout rules as TX state and federal. Key difference: **party designations use arbitration terminology**.

| Court filing term | AAA equivalent |
|---|---|
| Plaintiff | Claimant |
| Defendant | Respondent |

- Same-side parties: single paragraph, comma-separated, Oxford comma + ALL CAPS `AND` before last name.
- Role labels use the arbitration terms: "Claimant," / "Claimants," / "Respondent." / "Respondents."
- Dual-role labels: slash separator (e.g., `Respondent / Cross-Claimant,`).
- The final role label ends with a period; all others end with a comma.

### Column 2 — Section Symbols

`§` symbols, centered. Count mirrors the paragraph count in columns 1 and 3 (same as TX state pattern).

### Column 3 — Case Number

Single anchor at the first `v.` row (row index 4). All other column-3 rows are blank.

```
Row 0:  (blank)
Row 1:  (blank)
Row 2:  (blank)
Row 3:  (blank)
Row 4:  AAA Case No. [NUMBER]    ← right-aligned, at v. row
Row 5:  (blank)
...
Row N:  (blank)
```

**Case number format:** `AAA Case No. [NUMBER]` — right-aligned, plain weight, Century Schoolbook 12pt.

---

## Signature Block

Uses the **default container table layout** from the core SKILL.md:
- Container table technique (two-column borderless table, left cell empty, right cell holds all sig content)
- Attorney team from the toolkit profile (`attorneys[]`) unless a matter folder scan finds a different team
- Single-firm block (firm block from the toolkit profile)
- "ATTORNEYS FOR [CLIENT]" — bold, using arbitration designation (e.g., "ATTORNEYS FOR RESPONDENTS")
- No "DATED:" line before "Respectfully submitted,"

**Matter folder scan still applies.** Before generating a sig block from scratch, check the matter's work folder for an existing filing with the correct team. Some AAA matters have larger teams than the profile default. The scan protocol in the core SKILL.md governs.

---

## Certificate of Service

### Default COS Language (AAA)

> "I hereby certify that a true and correct copy of the foregoing document has been served on all parties of record through the AAA web filing portal on [month] [day], [year]."

### COS Signature

Same technique as all court types: paragraph indent at `left=4680 DXA`, underline+tab to right margin.

```
/s/ [Signing Attorney Name]     ← indent left=4680, underline+tab to right margin
[Signing Attorney Name]         ← indent left=4680, plain
```

---

## Legal Authority Framework

AAA filings may cite a blend of sources:

1. **AAA Rules** — Commercial Arbitration Rules (e.g., R-35(d) for subpoenas, R-21 for interim measures)
2. **Texas Arbitration Act** — Tex. Civ. Prac. & Rem. Code Chapter 171 (e.g., §171.051 for subpoena power)
3. **Texas Rules of Civil Procedure** — apply where incorporated by the arbitration agreement or the TAA (e.g., TRCP 176, 199, 205 for discovery)
4. **Federal Arbitration Act** — 9 U.S.C. §§ 1-16 (when applicable)

The applicable authority depends on the arbitration agreement and the specific filing. Do not assume all four apply to every document.

---

## Page Setup

Inherits from core SKILL.md — no overrides:

```
Page:    US Letter — 12240 × 15840 DXA
Margins: 1440 DXA (1") all sides
Header:  720 DXA distance, empty
Footer:  720 DXA distance, same on every page
```

---

## Footer

Inherits from core SKILL.md — no overrides:

```
Font:       Century Schoolbook, 10pt (sz=20)
Doc name:   Bold + Small Caps (title case source text)
Separator:  " – Page " (Small Caps, NOT bold)
Page number: PAGE field (Small Caps, NOT bold)
Alignment:  Left, same on every page
```

---

## Future Work — Subpoena-Specific Elements

The following elements appear in AAA arbitration subpoenas but are **not** part of the general AAA module. They are document-type-specific and will be built as a separate template layer when needed:

1. **"To:" address block** — Bold + small caps label ("To:"), followed by witness name and address. Uses first-line indent (457200 EMU / 720 DXA).

2. **Witness/Date/Purpose table** — 3 rows × 2 columns. Left column: labels (Witness, Date and Location, Purpose). Right column: details. Date and location are bold. No borders.

3. **Arbitrator issuance line** — "Issued the ____ day of [Month], [Year], by ___________________________ for the Panel." Uses blank underlines for the arbitrator's manuscript signature.

4. **Rule 176.8(a) notice** — Bold + italic block quoting the contempt warning from the Texas Rules of Civil Procedure. Required on all Texas-issued subpoenas (court or arbitration).

These elements are noted here for reference so they are not lost. When the subpoena template is built, it should layer on top of this AAA module — the caption, signature block, COS, and footer rules from this module still apply to subpoenas.
