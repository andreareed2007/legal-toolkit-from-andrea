---
name: court-filing-normalizer
description: >-
  Rename court filing PDFs in a folder to a consistent internal naming convention:
  YYYY.MM.DD [docket #] Title of Filing.pdf. Extracts filing date, docket number,
  and document title from PDF content (NYSCEF, ECF/PACER, eFileTexas) with filename
  fallback. USE THIS SKILL whenever the user mentions 'normalize filings', 'rename
  court filings', 'rename these PDFs', 'clean up filenames', 'normalize these court
  papers', 'rename the docket', 'fix filing names', 'NYSCEF rename', 'ECF rename',
  or has a folder of court filing PDFs with ugly filenames that need to be renamed to
  a consistent convention. Also triggers on 'rename these to the convention', 'put
  these in order', or any request to batch-rename PDFs that are court filings.
  Even casual triggers like 'these filenames are a mess' when the folder contains
  court filings should invoke this skill. NOT for renaming non-court documents,
  not for creating filings (use court-filing for that), and not for reading/analyzing
  filing content.
---

# Court Filing Normalizer

Renames court filing PDFs in a target folder to a consistent naming convention. Extracts metadata from PDF content where possible, falls back to filename parsing, and asks the user for anything it cannot determine.

The default convention is below. If the user's team uses a different pattern, apply theirs when building the rename table — extraction is unchanged; only the assembled filename differs.

## Target Convention

```
YYYY.MM.DD [docket #] Title of Filing.pdf
```

**Rules:**
- Date uses dots as separators (not dashes, not slashes)
- Docket number in square brackets, no zero-padding (`[1]` not `[0001]`)
- Title in title case
- `f/k/a` becomes `fka` in filenames (no slashes)
- Exhibits: `Exhibit 1 to Complaint`, `Exhibit A to Motion`, etc.
- Affirmations/affidavits of service: `Affirmation of Service - [Party Name].pdf`
- Declarations/affidavits by a person: `Declaration of [Name].pdf` or `Affidavit of [Name].pdf`
- Preserve amendment ordinals from the document itself (`First Amended Complaint`, not just `Amended Complaint`)
- Strip redundant party-name prefixes when the matter context makes them obvious, but preserve substantive qualifiers

**Examples:**
```
2026.04.22 [1] Summons.pdf
2026.04.22 [2] Complaint.pdf
2026.04.22 [3] Exhibit 1 to Complaint.pdf
2026.05.11 [17] Request for Judicial Intervention.pdf
2026.05.11 [18] Commercial Division Addendum to RJI.pdf
2026.05.11 [19] Affirmation of Service - Meridian Opportunities Fund LP.pdf
2026.05.11 [40] Affirmation of Service - ACME Widget Holdings Inc fka Widget Corp.pdf
```

## Workflow

### Step 1 — Identify Target Folder

The user either specifies a folder or the skill asks. If the shared profile (`~/.legal-skills/config.json`, key `matter_root`) defines where case files live, confirm the target folder is inside it; if it points elsewhere, or no profile exists, confirm the folder with the user before proceeding.

Ask: "Which folder should I normalize?" if not already specified.

### Step 2 — Scan and Extract Metadata

Run `scripts/extract_filing_metadata.py` against the target folder. The script:

1. Lists all PDFs in the folder
2. Skips files already matching the convention (`YYYY.MM.DD [#] *.pdf`)
3. For each remaining PDF:
   a. Tries pymupdf (fitz) to extract text from the first 3 pages
   b. If pymupdf fails, tries pypdf
   c. If both fail, extracts what it can from the filename
4. Parses three fields per file: **docket number**, **filing date**, **document title**
5. Outputs structured JSON to stdout

The extraction logic is detailed below and implemented in the script. The skill should NOT reimplement this logic inline — always run the script.

**Bash path translation:** (use THIS session's mount path — session names change every session; never copy a path from an old transcript) The script runs in the Linux sandbox. Translate the host folder path to the bash mount path before invoking. Example:
- Windows: `C:\Users\<you>\Documents\Case Files\Smith v Jones\Court Papers\`
- Bash: `/sessions/<current-session>/mnt/<mounted folder>/Case Files/Smith v Jones/Court Papers/`

Run command:
```bash
cd <this skill's scripts/ directory> && \
python extract_filing_metadata.py "/path/to/folder"
```

### Step 3 — Review Extraction Results

Read the JSON output. For each file, the script reports:
- `filename`: current filename
- `docket_number`: extracted docket number (integer or null)
- `filing_date`: extracted date as YYYY-MM-DD (or null)
- `title`: extracted document title (or null)
- `source`: how each field was determined (`pdf_content`, `filename`, or `unknown`)
- `confidence`: `high`, `medium`, or `low`
- `flags`: list of issues (e.g., `date_missing`, `title_ambiguous`, `unreadable_pdf`)

### Step 4 — Build Proposed Renames

For each file, construct the new filename from the extracted metadata. Apply these rules:

1. **Date formatting:** Convert `YYYY-MM-DD` to `YYYY.MM.DD`
2. **Docket number:** Wrap in square brackets: `[17]`
3. **Title cleanup:**
   - Title case (but preserve LLC, LP, LLP, Inc, LTD, etc.)
   - Replace `f/k/a` with `fka`
   - Strip leading "Defendant's" / "Plaintiff's" if the party is obvious from matter context
   - Truncate if full path would exceed 250 chars (leave buffer below Windows 260 limit)
4. **Missing fields:** If date or docket number is null, flag the file for user input — do NOT guess

Present the rename table in chat:

```
| # | Current Name | Proposed Name | Flags |
|---|---|---|---|
| 1 | 651234_2026_...pdf | 2026.04.22 [1] Summons.pdf | |
| 2 | ECF_0002_complaint.pdf | 2026.04.22 [2] Complaint.pdf | |
| 3 | some_weird_file.pdf | ??? | date_missing, title_ambiguous |
```

For flagged files, use AskUserQuestion to collect the missing information. Group all missing-info questions into a single prompt where possible.

### Step 5 — Rename on Approval

Only after the user confirms the rename table. Run `scripts/rename_filings.py` with the approved renames:

```bash
cd <this skill's scripts/ directory> && \
python rename_filings.py --renames '<JSON array of {old_path, new_name} objects>'
```

The script:
- Uses `os.rename()` for each file (not shell `mv`)
- Catches and reports per-file errors (`PermissionError`, `FileExistsError`, `OSError`)
- Outputs a summary: success count, failure count, details of any failures
- Has a `--dry-run` flag for testing without actual renames

Report results to the user. If any renames failed, explain why and offer to retry or skip.

### Step 6 — Post-Rename Verification

After renaming, list the folder contents and confirm all files now match the convention. Report any stragglers.

## Source Filename Patterns

The extraction script recognizes these e-filing system patterns. The pattern-matching is designed to be extensible — adding a new court system means adding a new regex block to the script's `PATTERNS` list.

### NYSCEF (New York State Courts Electronic Filing)
**Pattern:** `{index_number}_{year}_{PARTY_v_PARTY}__{DOC_TYPE}_{docket_number}.pdf`
**Example:** `651234_2026_ACME_WIDGET_LLC_v_ROADRUNNER_CORP__AFFIRMATION_AFFIDAV_20.pdf`
- Docket number: trailing integer before `.pdf`
- Document type: embedded but truncated — confirm from PDF content
- May be prefixed with `[docket#]`: `[17] 651234_2026_...`

### ECF / PACER (Federal Courts)
**Pattern:** `ECF {zero-padded docket#} {short title}.pdf`
**Example:** `ECF 0001 ACME Summons.pdf`
- Docket number: zero-padded integer after "ECF" (strip leading zeros)
- Title usually readable from filename
- Also: `{docket#}.pdf` or `{docket#}-{attachment#}.pdf` from raw PACER
- Also: `gov.uscourts.{district}.{case}.{docket}.{attachment}.pdf`

### eFileTexas
**Pattern:** varies, but commonly `{cause_number}_{filing_type}_{date}.pdf` or document IDs
- Less standardized than NYSCEF/ECF — rely more heavily on PDF content extraction
- Look for Texas-specific stamps and headers in PDF text

### Manual / Unknown
Files that don't match any known pattern. Extract metadata entirely from PDF content. If the PDF is unreadable, flag for user input.

## Date Extraction (Priority Order)

Implemented in `extract_filing_metadata.py`. The script tries these in order and stops at the first match:

1. **NYSCEF RECEIVED stamp:** `RECEIVED NYSCEF: MM/DD/YYYY` — this is the filing date for NY state court
2. **ECF header stamp:** `Filed MM/DD/YY` or `Filed MM/DD/YYYY` in the case header
3. **PACER docket text:** `Document Filed: MM/DD/YYYY`
4. **eFileTexas stamp:** `Filed:` or `FILED` followed by a date in various formats
5. **Generic "Filed" with date:** Catch-all for other courts
6. **Unknown:** Return null, flag as `date_missing`. Never use file modification timestamps — those reflect download time, not filing time.

## Title Extraction (Priority Order)

1. **Affirmation/affidavit of service:** Parse `Party served:` field for entity name
2. **Document heading:** Bold or centered text near the top of the first page (the legally operative title)
3. **ECF filename:** Strip "ECF XXXX" prefix and party abbreviations
4. **NYSCEF doc-type segment:** Use `__DOC_TYPE__` as a hint, but verify against PDF content
5. **Unknown:** Return null, flag as `title_ambiguous`

## Entity Name Cleanup

When extracting entity names (especially from affirmations of service):
- Normalize to title case but preserve corporate suffixes: LLC, LP, LLP, Inc, Inc., Ltd, LTD, Corp
- Replace `f/k/a` and `n/k/a` with `fka` and `nka` (no slashes in filenames)
- Strip trailing whitespace and double spaces
- If the full path would exceed 250 characters, truncate the entity name at a word boundary and append `...`

## Edge Cases

1. **Already-normalized files:** The script skips files matching `^\d{4}\.\d{2}\.\d{2} \[\d+\]`. Report how many were skipped.
2. **Non-PDF files:** Skip with a note. Do not rename Word docs, Excel files, etc.
3. **Duplicate docket numbers:** If two files claim the same docket number, flag both. Could mean federal + state filings coexist, or an exhibit was filed separately.
4. **Multi-exhibit PDFs:** If one PDF contains multiple exhibits, title as `Exhibits 1-14 to Complaint.pdf` (range).
5. **Encrypted/password-protected PDFs:** pymupdf and pypdf will both fail. Flag, extract from filename, ask user.
6. **Windows path length:** Check that the full new path stays under 250 chars. Truncate title if needed.
7. **Empty folder / no PDFs:** Report and stop.

## What This Skill Does NOT Do

- Move files between folders (renames in place only)
- Read docket sheets to fill in missing metadata (future enhancement)
- OCR scanned-only PDFs (future enhancement)
- Create or modify filing content (use `court-filing` skill for that)
- Generate an index or log file (could be added later)
