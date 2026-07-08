---
name: court-filing
description: "Use this skill to CREATE or EDIT Word documents filed in court. Triggers: 'motion', 'brief', 'pleading', 'answer', 'declaration', 'court filing', 'proposed order', or any document with a case caption and signature block. Also triggers on specific motions (motion to compel, MSJ, motion to exclude), responses, replies. ALSO TRIGGERS on requests to FIX court filing formatting — 'fix the list paragraphs', 'fix the numbering', ListParagraph, numPr, or auto-numbering issues. Supports Texas state, New York Supreme Court, federal, Texas Business Court, and AAA arbitration. If it has a case caption and signature block, use this skill, not docx. Even casual requests like 'draft a motion' or 'file something in the Smith case' trigger this. NOT for internal memos, demand letters, or non-court documents."
---

> **Version:** v1.0.0 (shared edition) · Part of the legal-filing-toolkit plugin.
> Identity, firm block, filing font, matter paths, and home jurisdictions come
> from the toolkit profile written by `environment-setup`. Bundled caption specs
> live in `specs/`. Supports TX state, NY Supreme, federal, TX Business Court,
> and AAA arbitration out of the box; California is a scaffold you populate from
> your own sample pleadings (`modules/STATE-CA.md`).


# Court Filing Skill — Core

> **First run:** this skill reads signer identity, firm block, filing font, and
> matter paths from the toolkit profile (`~/.legal-skills/config.json`, written
> by the `environment-setup` skill). If no profile exists, run `environment-setup`
> first. Where this file shows identity in brackets (e.g. `[Signing Attorney
> Name]`, `[Firm Name Line 1]`), substitute the configured values; the example
> names below are placeholders, not defaults to ship.

## Overview

This skill generates Word (.docx) court filings using `docx-js`, post-processes with `patch_court_filing.py`, and validates with `validate_court_filing.py`. Every document contains a case caption, document title, body content, signature block, and certificates with a footer on every page. When a proposed order is needed, generate a second, separate .docx.

### Modular Architecture

This file contains shared rules for ALL court types. Court-type-specific rules live in separate modules:

```
SKILL.md              ← THIS FILE: core rules
modules/
  STATE-TX.md         ← Texas state court
  STATE-NY.md         ← New York state court (Supreme Court)
  COURT-FEDERAL.md    ← Federal court
  COURT-BUSINESS.md   ← Texas Business Court
  AAA-ARB.md           ← AAA arbitration
  STATE-CA.md          ← California state court (populate from user samples)
```

**Always read this core file first, then read the applicable module.**

### Dependencies

- **DOCX skill** — Read `docx/SKILL.md` first for docx-js patterns, XML unpacking/repacking, and validation utilities.
- **`scripts/patch_court_filing.py`** — Post-processes ListParagraph, inline properties, and signature lines. Dispatches to court-type-specific patch modules.
- **`scripts/validate_court_filing.py`** — 55+ core checks plus court-type-specific checks.
- **Reference example (optional, not bundled)** — no sample filing ships with this skill (to keep it free of anyone's client data). If you want a non-authoritative sanity reference for non-caption components (title block, signature block, certificates), drop one of your own prior filings at `assets/example.docx`. Captions are always governed by the bundled `specs/` files, never by an example.

---

## Workflow — Required Questions

**⚠ MANDATORY: Do NOT proceed to drafting until ALL four questions are answered.** Ask these at the start of every invocation unless already known from context. Skipping these questions — especially line spacing — produces wrong output that must be rebuilt from scratch.

**Court type:** "Will this document be filed in state court, federal court, the Texas Business Court, or an AAA arbitration?" If state: "Which state?" (Texas, New York, and California supported; California requires one-time setup from your own sample pleadings.) This determines which module to read:
- State + Texas → `modules/STATE-TX.md`, `--court-type tx-state`
- State + New York → `modules/STATE-NY.md`, `--court-type ny-state`
- Federal → `modules/COURT-FEDERAL.md`, `--court-type federal`
- Business Court → `modules/COURT-BUSINESS.md`, `--court-type business`
- Arbitration (AAA) → `modules/AAA-ARB.md`, `--court-type aaa-arb`
- State + California → `modules/STATE-CA.md`, `--court-type ca-state` (see the module: it must be populated from the user's own sample California pleadings before first use)

**Line spacing:** "Single-spaced or double-spaced?" There is no default -- this varies by document type and attorney preference.

**Certificate body text is ALWAYS single-spaced** (`line=240`) in ALL court types, regardless of the document's body spacing setting. This applies to: certificate headings (centered, bold, underlined), certificate body paragraphs, and certificate signature paragraphs. The line spacing parameter only controls body content and numbered paragraphs.

**Proposed order:** "Does this filing need a proposed order?" If yes, produce a second .docx.

**Additional attorneys:** "Will any attorneys beyond those in your configured signer profile appear on this document?" For out-of-state filings, ask about local counsel details per the module.

---

## Input Parameters

### Case Information

| Parameter | Required | Notes |
|-----------|----------|-------|
| `courtType` | Yes — ask | `"tx-state"`, `"ny-state"`, `"federal"`, `"business"`, or `"aaa-arb"` |
| `courtName` | Yes | Per module (e.g., "68th Judicial District Court") |
| `courtLocation` | Yes | Per module (e.g., "Dallas County, Texas") |
| `causeNumber` | Yes | Format varies by court type — see module |
| `parties` | Yes | Array: `{ name, designation }` — names in ALL CAPS |
| `clientDescription` | Yes | For "ATTORNEYS FOR ___" line |
| `documentTitle` | Yes | Rendered per module spec |
| `footerDocumentName` | Yes | Bold + small caps at 10pt in footer |
| `lineSpacing` | Yes — ask | `"single"` or `"double"` |

### Structural Options

| Parameter | Default | Notes |
|-----------|---------|-------|
| `headingStyle` | `"numbered"` | `"numbered"` (I./A./1.) or `"unnumbered"`. Some modules override. |
| `includeCOConf` | Per module | Texas: true for motions. NY: false. |
| `includeCOS` | `true` | Off for declarations/exhibits |
| `includeCOC` | Per module | Business Court: true. Others: false. |
| `needsProposedOrder` | `false` — ask | Second .docx file |

### Attorney / Firm Identity — from the toolkit profile

Signer identity is NOT hardcoded. Read it from `~/.legal-skills/config.json`
(`attorneys[]`, `firm{}`, `default_signer`, `bar_label`, `filing_font`), written
by `environment-setup`. Shape:

```javascript
// attorneys: [{ name, bar_label, bar, email }]   // all names BOLD in sig block
// firm: { name_lines[], address_lines[], phone, fax, tokens[] }
// bar_label e.g. "State Bar No." / "Texas Bar No." / "SBN"
```

If the profile is missing, run `environment-setup`. As a secondary source,
scan the matter folder for a prior filing (see "Signature Block Reuse" below).
The placeholder names shown elsewhere in this file (e.g. `Jane Q. Public`) are
illustrations only — never ship them as real signers.

### Certificate Defaults

| Parameter | Default |
|-----------|---------|
| `cosDate` | Current date |
| `cosMethod` | Per module — TX: Rule 21a, Federal: CM/ECF, Business: eFileTexas, NY: CPLR § 2103 |
| `cosSigner` | `default_signer` from the profile |
| `confSigner` | `default_signer` from the profile |

---

## Build Pipeline

Every document — new or edited — must go through all five steps. No step is optional.

```
1. node build_filing.js            → raw.docx
2. python <docx-skill>/scripts/unpack.py raw.docx dir/
3. python <this-skill>/scripts/patch_court_filing.py dir/ --court-type [type]
4. python <docx-skill>/scripts/pack.py dir/ output.docx
5. python <this-skill>/scripts/validate_court_filing.py output.docx --spacing [single|double] --court-type [type]
```

Valid `--court-type` values: `tx-state`, `ny-state`, `federal`, `business`, `aaa-arb`

**For edits to existing documents:** Same pipeline. Use `--original existing.docx` on the pack step. Steps 3 and 5 are mandatory regardless of editing tool — no code path produces correct ListParagraph formatting without the patch, and "I already handled it in my script" is not an exception.

---

## Page Setup

```
Page:    US Letter — 12240 × 15840 DXA
Margins: 1440 DXA (1") all sides
Header:  720 DXA distance, empty
Footer:  720 DXA distance, same on every page
```

---

## Style Definitions

### Normal (Document Default)

```
Font:          Configured filing font (profile `filing_font`; default Century Schoolbook)
Size:          12pt (sz=24) — Business Court overrides to 14pt (sz=28)
Color:         Auto/black
Alignment:     Justified (jc=both)
Spacing after: 0
Line spacing:  Per lineSpacing parameter
```

### DocumentTitle

Bold, ALL CAPS (`w:caps` + text typed uppercase), underline, centered. `before=240, after=240, line=240 single`. `keepNext: true`. Not a Heading — does not participate in TOC or outline numbering. In NY filings, the title goes inside the caption table's right column (PleadingTitle style) — see STATE-NY.md.

### Heading 1 — Section Headers (I. II. III.)

```
Bold, Small Caps (w:smallCaps — NOT all caps), Underline
Alignment:  Center — no tabs or indents (left=0, hanging=0)
Spacing:    line=240 single, after=240 (12pt)
keepNext, keepLines, contextualSpacing=false
Outline level: 0
Numbering suffix: "space" (not "tab" — tab breaks centering)
```

NY filings use inline-formatted Normal paragraphs instead of Word heading styles. See STATE-NY.md.

### Heading 2 — Subsection Headers (A. B. C.)

```
Bold, sentence case (no small caps), no underline
Alignment:  Left
Indent:     left=720, hanging=720
Spacing:    line=240, after=240
keepNext, keepLines, contextualSpacing=false
Outline level: 1
```

### Heading 3 — Sub-subsection Headers (1. 2. 3.)

```
Bold, left-aligned
Spacing:    line=240, after=240
keepNext, keepLines
Outline level: 2
```

### Heading Numbering Config

```javascript
Level 0: UPPER_ROMAN  "I."   — left=0, hanging=0, suffix="space"
Level 1: UPPER_LETTER "A."   — left=720, hanging=720, suffix="tab"
Level 2: DECIMAL      "1."   — left=2160, hanging=360, suffix="tab"
```

### ListParagraph — Numbered Body Paragraphs

The default style for all substantive paragraphs after the Summary section. Numbering runs continuously (1, 2, 3...) across all Heading 1 sections. Every numbered body paragraph uses this style with `<w:numPr>` for auto-numbering.

**Indent pattern: `left=0, firstLine=720`.** The number appears at the first-line indent; wrapped lines return flush to the left margin. This is NOT a hanging indent.

**docx-js cannot generate this correctly.** Generate with `numbering: { reference: "listParagraph", level: 0 }`, then `patch_court_filing.py` replaces the style definition and adds inline properties to every ListParagraph paragraph.

**Style definition (after patching):**
```xml
<w:style w:type="paragraph" w:styleId="ListParagraph">
  <w:name w:val="List Paragraph"/>
  <w:qFormat/>
  <w:pPr>
    <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>
    <w:ind w:left="0" w:firstLine="720"/>
    <w:jc w:val="both"/>
  </w:pPr>
</w:style>
```

**Inline properties on each paragraph (after patching):**
```xml
<w:pPr>
  <w:pStyle w:val="ListParagraph"/>
  <w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>
  <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>
  <w:ind w:left="0" w:firstLine="720"/>
  <w:contextualSpacing w:val="0"/>
  <w:jc w:val="both"/>
</w:pPr>
```

**Numbering config:**
```javascript
{
  reference: "listParagraph",
  levels: [{
    level: 0,
    format: LevelFormat.DECIMAL,
    text: "%1.",
    alignment: AlignmentType.LEFT,
    suffix: "tab",
    style: {
      paragraph: {
        indent: { left: 900, hanging: 360 },  // overridden by patch
        spacing: { line: 480, lineRule: "auto", before: 0, after: 0 },
        alignment: AlignmentType.JUSTIFIED,
        contextualSpacing: false,
        tabStops: [{ type: TabStopType.LEFT, position: 900 }],
      },
      run: {
        bold: false, italics: false,
        font: "Century Schoolbook", size: 24,
      },
    },
  }],
}
```

### Summary Paragraphs

Used only in the Summary section (Heading I) for Texas/Business/Federal filings. Normal paragraphs with `firstLine=720`, no numbering. Spacing after=0, line spacing per document setting, justified.

### Block Quote

```
Left/right indent: 720 DXA (0.5")
Line spacing:      ALWAYS single (line=240) regardless of document setting
Spacing after:     0
Alignment:         Justified
No first-line indent, no quotation marks by default
```

### Footer

```
Font:       Century Schoolbook, 10pt (sz=20)
Doc name:   Bold + Small Caps (title case source text — NEVER all caps)
Separator:  " – Page " (Small Caps, NOT bold)
Page number: PAGE field (Small Caps, NOT bold)
Alignment:  Left, same on every page
```

**CRITICAL: Footer document name must use title case source text** (e.g., "Notice of Rule 11 Agreement"), NOT all caps. The `smallCaps` formatting renders title-case text as proper small caps. All-caps source text with smallCaps produces all full-height letters, defeating the purpose.

The document name run is bold + smallCaps. The separator and page number runs are smallCaps only — NOT bold.

---

## Caption Table — Core Rules

The caption table is always exactly **1 row × 3 cells**. Each cell contains multiple paragraphs (one per line). Never create a multi-row table — multi-row tables create horizontal grid-line artifacts in Word and break vertical alignment.

**Table-level borders must be `none`.** docx-js adds default `single` borders even when cell-level borders are `none`. Set borders explicitly on the Table object:

```javascript
const noBorder = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
// Apply to: top, bottom, left, right, insideHorizontal, insideVertical
// Apply same noBorder to each TableCell's borders
```

**Cell margins must also be zero on every cell** for caption tables. docx-js does not zero cell margins by default — set `margins: { top: 0, bottom: 0, left: 0, right: 0 }` explicitly on every TableCell in the caption.

Column widths, content, alignment, paragraph counts, and punctuation rules vary by court type — see the applicable module and its referenced spec doc:

- **TX state:** Authoritative spec at `specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md` (bundled). Module: `modules/STATE-TX.md`. Do not infer caption rules from any example .docx — use the bundled spec.
- **TX Business Court:** Authoritative caption spec at `specs/COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md` (bundled); non-caption rules (14pt font override, Heading 1 centering, signature block, Certificate of Compliance, eFileTexas COS) at `specs/COURT_FILING_TX_BUSINESS_MODULE.md`. Module: `modules/COURT-BUSINESS.md`.
- **Federal:** Module: `modules/COURT-FEDERAL.md`. (Locked spec pending.)
- **NY Supreme:** Module: `modules/STATE-NY.md`. (Locked spec pending.)

---

## Signature Technique — Underline + Tab to Margin

All signature lines in a court filing use the same technique: Word underline formatting on a tab character that extends to a tab stop. **Never use underscore characters (`___`).**

### Constants

```
CERT_SIG_LEFT    = 4320 DXA    // certificate signature left indent
CERT_SIG_FIRST   = 360 DXA    // certificate signature firstLine indent (total = 4680)
CERT_NAME_LEFT   = 4680 DXA   // certificate printed name indent
TAB_RIGHT_MARGIN = 9360 DXA   // tab stop position (right margin)
SIG_TAB_IN_CELL  = 4396 DXA   // tab stop within sig block container cell
```

### Signature Block — Container Table Technique (Default Layout)

The signature block is wrapped in a **borderless 2-column table** (4675 / 4685 DXA). The left cell is empty. The right cell holds everything from "Respectfully submitted," through "ATTORNEYS FOR ___." All paragraphs are flush-left within the right cell.

**Why a container table, not paragraph indents:** Paragraph indents (e.g., left=3600) push content toward the center of the page. The container table positions the entire block in the right half, matching standard filing conventions.

```javascript
// Container table — borderless, 2 columns
new Table({
  width: { size: 9360, type: WidthType.DXA },
  columnWidths: [4675, 4685],
  borders: noTableBorders,  // all borders NONE
  rows: [new TableRow({ children: [
    new TableCell({ borders: noCellBorders, width: { size: 4675, type: WidthType.DXA },
      children: [blankLine()] }),  // left cell empty
    new TableCell({ borders: noCellBorders, width: { size: 4685, type: WidthType.DXA },
      children: [/* all sig block content below */] }),
  ] })],
})
```

### "/s/" Line Within Container Cell

The `/s/` line uses a RIGHT tab at position 4396 (near the right edge of the ~4685-wide cell). The underline on the tab character extends from the name to the tab stop.

```javascript
new Paragraph({
  spacing: { line: 240, before: 0, after: 0 },
  tabStops: [{ type: TabStopType.RIGHT, position: 4396 }],
  children: [
    new TextRun({ text: "/s/ ", font: "Century Schoolbook", size: 24,
      underline: { type: UnderlineType.SINGLE } }),
    new TextRun({ text: "[Signing Attorney Name]", font: "Century Schoolbook", size: 24,
      italics: true, underline: { type: UnderlineType.SINGLE } }),
    new TextRun({ children: [new Tab()], font: "Century Schoolbook", size: 24,
      underline: { type: UnderlineType.SINGLE } }),
  ],
})
```

When a "By:" prefix is needed (e.g., when multiple attorneys sign), add `new TextRun({ text: "By:  " ... })` before the `/s/` run.

### Signature Block Reuse — Scan Matter Folder First

**Before generating a signature block from scratch, always check the matter's work folder for an existing filing** that already has the correct signature block for that matter's team. Different matters have different teams (some add local counsel or a second associate), different phone formats, and different client descriptions.

**Step 0 (before building the sig block):**

1. Identify the matter folder under your configured `matter_root` (profile) or from project instructions.
2. Scan for any `.docx` file in that folder (prefer the most recent, or a file named "template" or "Template").
3. If a file is found, extract its signature block by locating the container table that holds "Respectfully submitted" — read the cell contents to get: attorney list (names, bar numbers, emails), firm name formatting, phone/fax format, and client description.
4. Use the extracted sig block as the source of truth for this filing. Only fall back to defaults if no prior filing exists.

This ensures team-specific attorney lists, firm formatting, and client descriptions are carried forward automatically without re-asking the user every time.

---

### Full Signature Block Ordering (Within Container Cell)

The right cell of the container table holds all signature content in this exact order. All paragraphs are flush-left within the cell. Every paragraph is single-spaced (`line=240`).

```
Respectfully submitted,                    ← plain
(blank)
/s/ [Signing Attorney Name]                ← underline+tab technique (see /s/ Line section)
(blank)
[Attorney 1 Name]                          ← BOLD
  Texas Bar No. [number]                   ← plain, two literal spaces before text
  Email: [email]                           ← plain, two literal spaces before text
[Attorney 2 Name]                          ← BOLD
  Texas Bar No. [number]                   ← plain, two spaces
  Email: [email]                           ← plain, two spaces
[Attorney 3 Name, if applicable]           ← BOLD
  Texas Bar No. [number]                   ← plain, two spaces
  Email: [email]                           ← plain, two spaces
[Firm Name Line 1]                         ← BOLD + smallCaps
  [Firm Name Line 2, e.g. ", L.L.P."]      ← BOLD + smallCaps (any trailing ", L.L.P." = BOLD only, no smallCaps)
[Firm Address Line 1]                       ← plain
[Firm Address Line 2]                       ← plain
[Firm Phone] (Telephone)                    ← plain
[Firm Fax] (Facsimile)                      ← plain
(blank)
ATTORNEYS FOR [CLIENT DESCRIPTION]         ← BOLD (actual all caps, not smallCaps)
[CLIENT NAME]                              ← BOLD (actual all caps, not smallCaps)
```

**Key ordering rule:** Attorneys come BEFORE the firm name. The firm name block sits between the attorneys and the address. Never put the firm name before the /s/ line.

**Firm name line 2:** When the firm name wraps to a second line, that line starts with **two literal space characters** for visual indent. The firm-name words are bold + smallCaps; any trailing ", L.L.P." (or "P.C.", "LLP") is bold only (no smallCaps — smallCaps on already-uppercase text produces oversized letters).

**Phone/fax:** Always on separate lines with labels: `[Firm Phone] (Telephone)` and `[Firm Fax] (Facsimile)`. Never combine on one line with a slash.

### Attorney Info Block (Within Container Cell)

All content is flush-left within the right cell. Bar No. and Email lines use **two literal space characters** before the text for visual subordination — no DXA indents.

```
Jane Q. Public                  ← BOLD, flush left     (placeholder — use profile)
  State Bar No. 000000          ← two spaces before text ([bar_label] + number)
  Email: jpublic@example.com    ← two spaces before text
John A. Roe                     ← BOLD, flush left
  State Bar No. 000001          ← two spaces before text
  Email: jroe@example.com       ← two spaces before text
```

These are placeholders — the real values come from the profile (`attorneys[]`). If the matter folder contains a prior filing, use its attorney list instead — some teams include additional attorneys or local counsel. The bar-number label follows the profile `bar_label` (e.g. "State Bar No.", "Texas Bar No.", "SBN").

---

### Certificate Spacing — Hard Rule (All Court Types)

**Certificate body text is ALWAYS single-spaced** (`line=240`) in ALL court types, regardless of the document's body spacing setting. This is a hard rule with no exceptions.

Single-spaced elements in certificates:
- Certificate headings (centered, bold, underlined)
- Certificate body paragraphs (the certification language)
- "Certified to" / dateline paragraphs
- Certificate signature paragraphs (/s/ line + printed name)
- All blank lines within certificates

The `lineSpacing` parameter (single/double) from the Required Questions only controls: body content paragraphs, numbered ListParagraph paragraphs, and Summary paragraphs. It never controls certificates.

### Certificate of Service Signature

Certificate signatures use paragraph indents (not the container table technique). The /s/ line and printed name each get `left=4680 DXA`:

```
/s/ [Signing Attorney Name]     ← indent left=4680, underline+tab to right margin
[Signing Attorney Name]         ← indent left=4680, plain
```

### Certificate of Conference Signature

Same indent technique as Certificate of Service: `left=4680 DXA` for both /s/ line and printed name.