# Court-Filing Module — New York Supreme Court

## Court Type: `ny-state`

This module defines the caption layout, certificate language, heading style, signature block structure, and court-specific rules for **New York Supreme Court** (state trial court) filings. Read the core `SKILL.md` first — this file only contains NY-specific overrides and additions.

**Validator/patch flag:** `--court-type ny-state`

---

## When This Applies

- The user says "state court" and the state is New York
- The court is the New York Supreme Court (the state trial court)
- The filing uses an "Index No." (not "Cause No.")
- The matter context identifies a New York state court venue

---

## Additional Questions

When NY state is selected, ask:
> "Which county?" (e.g., New York County, Kings County, Westchester County)

For filings with local counsel:
> "Who is local counsel?" (name, firm, address, bar info)

### NY-Specific Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `county` | Yes | e.g., `"New York"`, `"Kings"`, `"Westchester"` |
| `indexNumber` | Yes | NY uses "Index No." not "Cause No." — format varies by county |
| `localCounsel` | Conditional | Required when your firm's attorneys are appearing pro hac vice |

---

## Font

Century Schoolbook 12pt (sz=24) — same as Texas state. No font override.

---

## Caption Layout: New York Supreme Court

### Document Assembly Order

```
 1.  Court Name (centered paragraph ABOVE table — "SUPREME COURT OF THE STATE OF NEW YORK" + line break + "COUNTY OF [COUNTY]")
 2.  Caption Table (3-column borderless — document title INSIDE right column)
 3.  [Body begins — NO standalone DocumentTitle paragraph below table]
 4.  Introductory paragraph (Normal, firstLine=720)
 5.  Section headings + ListParagraph body
 6.  Prayer for Relief (WHEREFORE + lettered sub-items)
 7.  DATED line
 8.  Signature Block (two-firm structure with "-and-")
 9.  [If includeCOS] Certificate of Service
```

**Key differences from Texas:**
- Court name is ABOVE the table (like federal), not inside it
- Document title goes INSIDE the right column of the caption table (not below it)
- No Certificate of Conference by default
- "DATED:" line before signature block
- Two-firm signature block structure

---

## Court Name Header (ABOVE table)

A single paragraph above the caption table. The CourtName style uses ALL CAPS (`w:caps`), centered. The two lines are separated by a `<w:br/>` line break within a single paragraph.

```javascript
new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 240, line: 240, lineRule: "auto" },
  contextualSpacing: true,
  children: [
    new TextRun({
      text: "SUPREME COURT OF THE STATE OF NEW YORK",
      font: "Century Schoolbook", size: 24, allCaps: true,
    }),
    new TextRun({ break: 1, font: "Century Schoolbook", size: 24 }),
    new TextRun({
      text: "COUNTY OF NEW YORK",  // ← substitute actual county
      font: "Century Schoolbook", size: 24, allCaps: true,
    }),
  ],
})
```

---

## Caption Table Structure

```
Column widths:  Left: 5400  |  Center: 144  |  Right: 4716
Rows:           EXACTLY 1 (multiple paragraphs per cell)
Total:          10260 DXA
Table layout:   Fixed
```

**Table cell margins:**
```
Left cell:    top=0, left=0, bottom=29, right=144
Center cell:  top=0, left=0, bottom=29, right=0
Right cell:   default
```

### Left Cell (Parties)

Uses dedicated NY caption styles (not inline formatting):

| Style | Purpose | Key properties |
|-------|---------|----------------|
| `Parties` | Party names | Left-aligned, single-spaced, spaceAfter=240 |
| `PartyType` | "Plaintiffs," / "Defendants." | Left-aligned, indent left=1440, single-spaced |
| `versus` | "v." | Left-aligned, indent left=720, single-spaced, spaceAfter=240 |
| `CaseCaptionBottom` | Empty closing paragraph | Left-aligned, indent left=-86 |

**Layout:**
```
[NAME(s)],                    ← Parties style
     Plaintiffs,              ← PartyType style (indent 1440)
v.                            ← versus style (indent 720)
[NAMES],                      ← Parties style
     Defendants.              ← PartyType style (indent 1440)
[empty]                       ← CaseCaptionBottom style
```

### Center Cell (Divider)

Width: 144 DXA. Contains a single empty paragraph with `border` style. This is a narrow spacing cell — it does NOT contain § symbols (unlike Texas). No visible content.

### Right Cell (Index Number + Document Title)

Contains the index number line and the document title, using dedicated NY caption styles:

| Style | Purpose | Key properties |
|-------|---------|----------------|
| `CaseNo` | "INDEX NO.: ________" | Left-aligned, ALL CAPS (w:caps), indent left=144, single-spaced, spaceAfter=240 |
| `PLDCaption2` | Spacer paragraphs | Left-aligned, indent left=144, single-spaced |
| `PleadingTitle` | Document title (e.g., "COMPLAINT") | Left-aligned, bold, ALL CAPS (w:caps), indent left=144 |

**Layout:**
```
INDEX NO.:  ________          ← CaseNo style (ALL CAPS, with underscore fill line)
                              ← PLDCaption2 (blank spacer)
                              ← PLDCaption2 (blank spacer)
COMPLAINT                     ← PleadingTitle style (bold, ALL CAPS)
```

**Important:** The document title lives INSIDE the right column. There is NO separate DocumentTitle paragraph below the table.

---

## Applied Example

```
SUPREME COURT OF THE STATE OF NEW YORK
COUNTY OF NEW YORK

[NAME(s)],                          INDEX NO.:  ________
     Plaintiffs,
                                    COMPLAINT
v.

[NAMES],
     Defendants.
```

---

## Section Headings — NOT Word Heading Styles

**This is a major structural difference from Texas/Federal/Business Court.**

NY Supreme Court filings use **inline-formatted Normal paragraphs** for section headings — NOT Word `Heading1`/`Heading2`/`Heading3` styles with auto-numbering. There is no Roman numeral auto-numbering on sections.

### Standard Section Heading
```
Style:          Normal (with inline run formatting)
Alignment:      Center (jc=center)
Spacing:        before=240
Run formatting: Bold + Small Caps + Underline
```

Used for: "Parties", "Jurisdiction and Venue", "Statement of Facts", "Prayer for Relief"

```javascript
new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 240 },
  children: [
    new TextRun({
      text: "Parties",
      bold: true, smallCaps: true,
      underline: { type: UnderlineType.SINGLE },
      font: "Century Schoolbook", size: 24,
    }),
  ],
})
```

### Count Heading (Two-Line Pattern)

Causes of action use a two-paragraph heading:

**Line 1 — Count number:** Bold + Small Caps, **no underline**
```
                         Count I
```

**Line 2 — Cause of action:** Bold + Small Caps + Underline
```
                    Breach of Contract
```

```javascript
// Line 1: Count number (no underline)
new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { before: 240 },
  children: [
    new TextRun({
      text: "Count I",
      bold: true, smallCaps: true,
      font: "Century Schoolbook", size: 24,
    }),
  ],
}),
// Line 2: Cause of action (underlined)
new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [
    new TextRun({
      text: "Breach of Contract",
      bold: true, smallCaps: true,
      underline: { type: UnderlineType.SINGLE },
      font: "Century Schoolbook", size: 24,
    }),
  ],
})
```

### Heading Style Override

Set `headingStyle = "unnumbered"` for NY filings. ListParagraph numbered body paragraphs still apply — only the section headings are unnumbered.

---

## Introductory Paragraph

NY complaints begin with an introductory paragraph before the first section heading:

```
     Plaintiff [Name] ("Plaintiff"), by and through its undersigned
attorneys, as and for its Complaint against Defendants, alleges as follows:
```

Style: Normal, firstLine=720 indent, justified. This appears immediately after the caption table.

---

## Prayer for Relief

NY prayers use lettered sub-paragraphs with a bold WHEREFORE clause:

```
     WHEREFORE, Plaintiff respectfully requests that the Court enter
judgment in favor of Plaintiff and against Defendants as follows:

          (a) Awarding Plaintiff compensatory damages in an amount
to be proven at trial;

          (b) Awarding Plaintiff pre-judgment and post-judgment interest;

          (c) Awarding Plaintiff its reasonable attorneys' fees and costs; and

          (d) Awarding Plaintiff such other and further relief as the
Court deems just and proper.
```

- WHEREFORE paragraph: Normal style, firstLine=720, bold on "WHEREFORE" run
- Sub-items: Normal style, left indent=1440 DXA, no numbering engine (manual letters)

---

## DATED Line

NY filings include a "DATED:" line before "Respectfully submitted":

```
DATED: New York, New York
     [Date]                   ← indent left=1008

Respectfully submitted,
```

Spacing: single-spaced (line=240).

---

## Signature Block — Two-Firm Structure

NY filings with out-of-state counsel use a **two-firm signature block** with an "-and-" separator. Local counsel signs first, then your firm's attorneys appear individually with *pro hac vice* notation.

### Layout

```
[Local Firm Name]                     ← Small Caps
By:  /s/ DRAFT_____________          ← underline+tab technique (flush left — NO 3600 indent)
[Local Attorney Name]                 ← Bold
[Address]
[City, State ZIP]
Telephone: [number]
Facsimile: [number]
Email: [email]

                    -and-             ← centered, spaced before=240, after=240

Jane Q. Public                        ← Bold      (placeholder — use profile)
[bar_label] 000000
jpublic@example.com
pro hac vice application forthcoming  ← Italic (out-of-state counsel only)

John A. Roe                           ← Bold
[bar_label] 000001
jroe@example.com
pro hac vice application forthcoming  ← Italic

[Additional attorney]                 ← Bold (if on the filing)
[bar_label] 000002
Email: third@example.com
pro hac vice application forthcoming  ← Italic

[Firm Name Line 1]                    ← Bold + Small Caps
   [Firm Name Line 2, e.g. ", L.L.P."] ← Bold + Small Caps
[Firm Address Line 1]
[Firm Address Line 2]
[Firm Phone] – Telephone
[Firm Fax] – Facsimile

Attorneys for Plaintiff [Client Name] ← Bold + Small Caps
```

### Key Differences from Default Layout
- **No left indent on signature block** — flush left (no SIG_BLOCK_LEFT=3600)
- **No ATTY_INFO_LEFT indent** — attorney info is flush left
- **Two firms** separated by centered "-and-"
- **Local counsel signs** on the "By:" line
- **Pro hac vice notation** in italics under each out-of-state attorney
- **Firm name** (your firm): Bold + Small Caps
- **"Attorneys for [Client]"**: Bold + Small Caps (note lowercase "Attorneys" unlike Texas "ATTORNEYS")
- All lines single-spaced (line=240)

### When No Local Counsel

If your firm's attorneys are admitted in NY (or it's a filing that doesn't require admission), use a single-firm block similar to the Texas layout but still flush-left with the NY formatting conventions (firm name bold+small caps, "Attorneys for" bold+small caps).

---

## Certificate of Conference

**Not included by default.** NY practice does not have the same mandatory conference certification as Texas. Set `includeCOConf = false`.

Individual judges may require good-faith meet-and-confer certification — if so, draft custom language per the judge's individual rules.

---

## Certificate of Service

**Language (CPLR § 2103):**
> "I hereby certify that on [date], a true and correct copy of the foregoing document was served upon all parties entitled to service by [method of service, e.g., electronic filing / first class mail / hand delivery] in accordance with CPLR § 2103."

The method of service is a fill-in — ask the user which method applies.

Heading: bold, small caps, underlined, centered. `keepNext` + `keepLines` on the heading paragraph. SpaceBefore=480, spaceAfter=240.

Body: single-spaced (line=240), firstLine=720 indent.

Certificate signature: standard underline+tab technique at left=4680.

---

## NY-Specific Caption Styles to Define

These styles must be defined in `styles.xml` for NY filings. They do NOT exist in the Texas/Federal/Business Court templates.

```javascript
// Parties — party names in caption
{
  id: "Parties", name: "Parties",
  paragraph: {
    spacing: { after: 240, line: 240, lineRule: "auto" },
    alignment: AlignmentType.LEFT,
    suppressAutoHyphens: true,
  },
}

// PartyType — "Plaintiffs," / "Defendants."
{
  id: "PartyType", name: "Party Type",
  paragraph: {
    spacing: { line: 240, lineRule: "auto" },
    indent: { left: 1440 },
    alignment: AlignmentType.LEFT,
    suppressAutoHyphens: true,
  },
}

// versus — "v."
{
  id: "versus", name: "versus",
  paragraph: {
    spacing: { after: 240, line: 240, lineRule: "auto" },
    indent: { left: 720 },
    alignment: AlignmentType.LEFT,
  },
}

// CaseNo — "INDEX NO.: ________"
{
  id: "CaseNo", name: "CaseNo",
  paragraph: {
    spacing: { after: 240, line: 240, lineRule: "auto" },
    indent: { left: 144 },
    alignment: AlignmentType.LEFT,
    suppressAutoHyphens: true,
  },
  run: { allCaps: true },
}

// PleadingTitle — document title in right column
{
  id: "PleadingTitle", name: "Pleading Title",
  paragraph: {
    spacing: { line: 240, lineRule: "auto" },
    indent: { left: 144 },
    alignment: AlignmentType.LEFT,
    suppressAutoHyphens: true,
  },
  run: { bold: true, allCaps: true },
}

// PLDCaption2 — spacer paragraphs in right column
{
  id: "PLDCaption2", name: "PLD Caption 2",
  paragraph: {
    spacing: { line: 240, lineRule: "auto" },
    indent: { left: 144 },
    alignment: AlignmentType.LEFT,
    contextualSpacing: true,
  },
}

// CaseCaptionBottom — closing paragraph in left column
{
  id: "CaseCaptionBottom", name: "Case Caption Bottom",
  paragraph: {
    spacing: { line: 240, lineRule: "auto" },
    indent: { left: -86 },
    alignment: AlignmentType.LEFT,
  },
}

// border — empty paragraph in center divider cell
{
  id: "border", name: "border",
  paragraph: {
    spacing: { line: 240, lineRule: "auto" },
    alignment: AlignmentType.LEFT,
    suppressAutoHyphens: true,
  },
}
```

---

## Validator: NY State Checks

In addition to the 55+ core checks, the NY state validator confirms:
- Court name header "SUPREME COURT OF THE STATE OF NEW YORK" above table
- County line present (e.g., "COUNTY OF NEW YORK")
- "INDEX NO." found in right column (not "CAUSE NO.")
- Document title (PleadingTitle) inside the right column of the caption table
- No standalone DocumentTitle paragraph below the table
- Section headings are NOT Word heading styles (Normal paragraphs with inline formatting)
- No § symbols in center column (center cell is an empty divider)
- Font size 12pt (sz=24)
- "DATED:" line present before signature block
- Pro hac vice notation present on your firm's attorneys (when local counsel is on the filing)
- COS language references CPLR § 2103
- No Certificate of Conference (unless judge requires it)

---

## Source Authority

Based on:
1. A NY Supreme Court complaint/pleading template (supply your own sample NY filing to confirm caption geometry before relying on this module)
2. CPLR §§ 2103, 503, 509 (service and venue rules)
3. Uniform Rules for Trial Courts, 22 NYCRR Part 202
