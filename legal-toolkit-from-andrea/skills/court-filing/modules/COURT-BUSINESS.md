# Court-Filing Module — Texas Business Court

## Court Type: `business`

This module defines the caption layout, certificate language, font overrides, and court-specific rules for **Texas Business Court** filings. Read the core `SKILL.md` first — this file only contains Business Court-specific overrides and additions.

**Validator/patch flag:** `--court-type business`

---

## When This Applies

- The user says "Business Court" or "TBC"
- The court is identified as "The Business Court of Texas"
- The cause number follows the Business Court format: `YY-BCDDP-NNNN`
- The matter context file identifies the venue as the Texas Business Court

---

## Additional Question

When Business Court is selected, also ask:
> "Which division?" (e.g., First Division, Eighth Division)

### New Parameters

| Parameter | Required | Notes |
|-----------|----------|-------|
| `courtDivision` | Yes | e.g., `"First Division"`, `"Eighth Division"`, `"Eleventh Division"` |

### Cause Number Format

```
YY-BCDDP-NNNN
```
Where:
- `YY` = 2-digit filing year
- `BC` = literal "BC" (Business Court)
- `DD` = 2-digit division number (01, 03, 04, 08, 11, etc.)
- `P` = panel letter (A, B, etc.)
- `NNNN` = 4-digit sequence number

Examples: `25-BC00X-0000`, `24-BC11A-0007`, `25-BC00X-0000`

---

## Font Override

**14pt (sz=28) Century Schoolbook**, overriding the core's 12pt (sz=24). This applies to all body text, caption text, headings, certificates, and signature blocks. Footer remains 10pt.

---

## Caption Layout: Business Court

### Document Assembly Order

```
 1.  Caption Table (3-column borderless — NO court name header above, NO cause number above)
 2.  Document Title (DocumentTitle style)
 3.  I. Summary (Heading 1, body = BodyTextIndent)
 4.  II+ Remaining Sections (Heading 1/2/3, body = ListParagraph)
 5.  Signature Block
 6.  [If includeCOConf] Certificate of Conference
 7.  [If includeCOC] Certificate of Compliance (REQUIRED for BC)
 8.  [If includeCOS] Certificate of Service
```

### No Court Name Header Above Table

Unlike federal court, there is **no centered court name header above the caption table**.

### No Cause Number Above Table

Unlike Texas state court, there is **no cause number as a centered paragraph above the table**.

### Caption Table Structure

```
Column widths:  Left: 4320  |  Center: 360  |  Right: 4680
Rows:           EXACTLY 1 (multiple paragraphs per cell)
Total:          9360 DXA
```

**Left column:** Party names with designations. Party names are **NOT bold** -- plain text only. When a party name is too long to fit on one line at 14pt in 4320 DXA (~3 inches), **pre-break** it into separate paragraphs at a natural word boundary (e.g., "ROADRUNNER" / "ENTERPRISES, LLC,") to control where the line breaks.

**Center column:** § symbols, one per paragraph row. The center column must have the **same paragraph count** as the left and right columns. The last § must align with the last content line (typically "Defendant."). **No trailing blank paragraphs** -- the § column ends where the content ends.

**Right column:** **CENTER-aligned** (jc=center — NOT right-aligned like state/federal).

| Content | Position | Formatting |
|---------|----------|------------|
| `THE BUSINESS COURT OF TEXAS` | Row 1 (top) | ALL CAPS, center-aligned |
| `[DIVISION NAME]` | Row 3 (one blank row below court name) | ALL CAPS, center-aligned |
| `Cause No. [YY-BCDDP-NNNN]` | Row 6 (below "v." area) | Center-aligned |

Remaining rows blank (padding to match left column paragraph count). **No trailing blank paragraphs** in any column -- the last paragraph row is the last line with visible content in the left column (typically "Defendant.").

**Important:**
- Court name is `THE BUSINESS COURT OF TEXAS` — NO "IN" prefix.
- Court name at the **top** of the right column (row 1).
- All right-column paragraphs use `jc=center`.

### Caption Spacing

All paragraphs inside the caption table: **explicitly single-spaced** with `spacing: { after: 0, line: 240, lineRule: "auto" }`. Do NOT rely on inherited double spacing.

---

## Applied Example

```
VOLTAS INDUSTRIAL                          §    THE BUSINESS COURT OF TEXAS
HOLDINGS, LP,                              §
                                           §    FIRST DIVISION
     Plaintiff,                            §
                                           §
v.                                         §    Cause No. 25-BC00X-0000
                                           §
LARKFIELD TECHNOLOGIES,                    §
INC.,                                      §
                                           §
     Defendant.                            §
```

---

## Signature Block Formatting

In addition to the core signature block spec:

- **Firm name** (from the toolkit profile `firm.name_lines`): **Bold + Small Caps**
- **"ATTORNEYS FOR [CLIENT]"** line: **Bold**
- All attorney names remain bold per core

---

## Certificate of Conference

Same Texas language as STATE-TX.md. Set `includeCOConf = true` for motions.

---

## Certificate of Compliance (REQUIRED)

Set `includeCOC = true` for **all** Business Court motions, responses, and replies. Discovery motions capped at 3,000 words.

```
CERTIFICATE OF COMPLIANCE

    I certify that this [document type] complies with the word limits in the
Texas Business Court Local Rules. This [document type] contains [WORD COUNT]
words, excluding the parts exempted by the Texas Business Court Local Rules.

                              /s/ [Signing Attorney Name]_______
                              [Signing Attorney Name]
```

Appears after Certificate of Conference and before Certificate of Service.

---

## Certificate of Service

**Language (eFileTexas):**
> "I hereby certify that on [date], I electronically filed the foregoing with the Clerk of the Business Court of Texas by using the eFileTexas system which will send a notice of electronic filing to all counsel of record."

---

## Validator: Business Court Checks

In addition to the 55+ core checks:
- Font size 14pt (sz=28) on body text
- Heading spaceAfter = 240 DXA (12pt)
- Heading 1: center-aligned, no indent
- Caption: single-row, center-aligned right column
- Court name "THE BUSINESS COURT OF TEXAS" (no "IN")
- BC cause number format (YY-BCDDP-NNNN)
- Caption paragraphs single-spaced
- Signature block: firm name bold+smallCaps, "ATTORNEYS FOR" bold

---

## Source Authority

Based on published Business Court filings:
1. Published Texas Business Court opinions and filings (use current published decisions and any sample Business Court filing you have on hand to confirm caption geometry before relying on this module).
2. Published opinions: CreateAI v. Bot Auto TX (11th Div.), C Ten 31 v. Tarbox (3rd Div.), Energy Transfer v. Culberson (1st Div.), Crain v. Northern (8th Div.)
