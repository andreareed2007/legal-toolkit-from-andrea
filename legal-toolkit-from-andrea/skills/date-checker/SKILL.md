---
name: date-checker
description: >
  Verify all dates in Claude's output before returning finished work. USE THIS
  SKILL on every response containing calendar dates, days of the week, deadlines,
  date ranges, timelines, or scheduling — in prose, filings, memos, trackers,
  spreadsheets, HTML, or any format. Triggers on dates in any format: "March 17,
  2026", "Tuesday, March 17", "3/17/26", "2026.03.17", "today", "tomorrow",
  or day-of-week names paired with dates. Also triggers on date arithmetic
  ("30 days after", "the following Monday", "next business day"). Claude's token
  prediction hallucinates day-of-week labels, produces impossible dates (Feb 30),
  drifts during date math, and contradicts itself across long documents. The fix
  is a Python script using the system clock and calendar module as ground truth.
  Even if the output seems obvious, run the checker — Claude is confidently wrong
  about dates.
---

# Date Checker

## Why This Skill Exists

Claude generates dates by token prediction, not calendar computation. This causes
four categories of errors that are invisible to Claude without external verification:

1. **Day-of-week hallucination** — Writing "Monday, March 17, 2026" when March 17
   is actually a Tuesday. Claude does this with full confidence.
2. **Date arithmetic drift** — Errors in computed deadlines, "30 days from" dates,
   "the Monday after" calculations, etc.
3. **Internal inconsistency** — The same date labeled with different day names in
   different parts of a document.
4. **Invalid dates** — February 30, non-leap-year Feb 29, month 13, etc.

The only reliable fix is to verify every date against Python's `datetime` and
`calendar` modules, which use the real calendar.

---

## When to Run

Run the date checker on **every** response that contains dates before returning
finished work to the user. This includes but is not limited to:

- Court filings, motions, proposed orders (deadlines, hearing dates, service dates)
- Timelines, chronologies, schedules
- Memos, letters, reports with date references
- Tracker updates, spreadsheets with date columns
- HTML command centers, trial notebooks
- Prose responses that mention specific dates or days of the week
- Any output where Claude performed date arithmetic

**Do NOT skip the check because the date "looks right."** The entire purpose of
this skill is that Claude cannot tell when its dates are wrong.

---

## How to Run

### Step 1: Draft the output

Complete the full draft of whatever you are producing (filing, memo, prose answer,
etc.) as you normally would. Do not alter your drafting process.

### Step 2: Pipe the draft through the checker

Save the draft text to a temp file and run the script:

```bash
cat /home/claude/draft_output.txt | python /path/to/date-checker/scripts/date_checker.py
```

Or pass the file directly:

```bash
python /path/to/date-checker/scripts/date_checker.py /home/claude/draft_output.txt
```

**For non-text outputs** (e.g., DOCX, HTML, XLSX): extract the text content first,
then pipe it through the checker. Examples:

```bash
# DOCX — extract text via pandoc
pandoc /home/claude/output.docx -t plain -o /home/claude/extracted.txt
python /path/to/date-checker/scripts/date_checker.py /home/claude/extracted.txt

# HTML — strip tags
cat /home/claude/output.html | sed 's/<[^>]*>//g' | python /path/to/date-checker/scripts/date_checker.py

# For prose responses: echo the response text to the checker before presenting it
```

### Step 3: Read the report

The script outputs one of two statuses:

- **PASS** — All dates verified correct. Proceed to deliver the output.
- **FAIL** — One or more errors found. Each error is labeled with its type
  (DOW MISMATCH, INVALID DATE, INCONSISTENCY, RELATIVE MISMATCH) and the
  correct value.

### Step 4: Fix and re-verify

If the report shows FAIL:

1. Read each flagged error.
2. Correct the date in your output. The report tells you what the correct
   day-of-week or valid date should be.
3. Re-run the checker on the corrected output.
4. Repeat until PASS.

Only deliver the output to the user after a clean PASS.

---

## What the Script Checks

| Check | What it catches |
|-------|-----------------|
| **Date validity** | Feb 30, Feb 29 in non-leap years, month > 12, day > 31, etc. |
| **Day-of-week match** | "Monday, March 17, 2026" when March 17 is actually Tuesday |
| **Cross-consistency** | Same calendar date paired with two different day names in the document |
| **Relative dates** | "today" or "tomorrow" not matching the system clock |
| **Format coverage** | Handles: Month DD YYYY, MM/DD/YYYY, MM/DD/YY, YYYY.MM.DD, DayOfWeek + any of the above, ordinal suffixes (17th), abbreviated months (Mar) and days (Tue) |

---

## Supported Date Formats

The script recognizes all of the following:

- `Tuesday, March 17, 2026` — full day-of-week + month name + day + year
- `March 17, 2026` — month name + day + year
- `March 17` — month name + day (assumes current year)
- `Mar 17, 2026` — abbreviated month
- `3/17/2026` or `3-17-2026` — numeric with 4-digit year
- `3/17/26` — numeric with 2-digit year
- `2026.03.17` — file-naming convention (YYYY.MM.DD)
- `Tuesday 3/17/2026` — day-of-week + numeric date
- `Tuesday 3/17` — day-of-week + numeric date, no year (assumes current year)
- `03/17/2026 (Tuesday)` — numeric date + parenthetical day-of-week
- `3/17 (Tuesday)` — numeric date without year + parenthetical day-of-week
- `March 17th, 2026` — ordinal suffixes (st, nd, rd, th)
- `today`, `tomorrow`, `yesterday` — relative dates checked against system clock

---

## Date Arithmetic Guidance

When performing date arithmetic (e.g., "30 days after March 1, 2026"), do NOT
rely on token prediction. Instead, compute the result using Python:

```bash
python3 -c "
from datetime import datetime, timedelta
base = datetime(2026, 3, 1)
result = base + timedelta(days=30)
print(f'{result.strftime(\"%A, %B %d, %Y\")}')"
```

Then use that computed result in your output, and the date checker will verify
the day-of-week label is correct.

For business-day calculations, use:

```bash
python3 -c "
from datetime import datetime, timedelta
base = datetime(2026, 3, 1)
days_needed = 20  # business days
current = base
count = 0
while count < days_needed:
    current += timedelta(days=1)
    if current.weekday() < 5:  # Mon-Fri
        count += 1
print(f'{current.strftime(\"%A, %B %d, %Y\")}')"
```

---

## Script Location

The verification script is at:

```
date-checker/scripts/date_checker.py
```

It is pure Python standard library (`re`, `datetime`, `calendar`, `sys`).
No pip installs required. Runs in under a second on any document length.

---

## Edge Cases and Limitations

- **Dates without years**: Default to current year. If the document discusses
  a different year, this could produce a false positive. When working on
  documents about a specific past/future year, note this in your review.
- **Ambiguous MM/DD vs DD/MM**: The script assumes US format (MM/DD). This is
  correct for Texas court filings and US legal work.
- **Dates inside structured data**: For XLSX or JSON outputs, extract the
  relevant text before piping through the checker.
- **Time zones**: The script uses the container's local time for "today."
  In practice this is fine for Texas-based legal work.
