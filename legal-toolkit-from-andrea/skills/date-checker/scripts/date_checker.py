#!/usr/bin/env python3
"""date_checker.py — verify every date in a draft against the real calendar.

Usage:
    python3 date_checker.py draft.txt
    cat draft.txt | python3 date_checker.py

Exit code 0 = PASS, 1 = FAIL, 2 = usage/input error.

Checks (per the date-checker SKILL.md):
  INVALID DATE       — impossible dates (Feb 30, month 13, non-leap Feb 29)
  DOW MISMATCH       — day-of-week label does not match the calendar
  INCONSISTENCY      — same calendar date given two different day names
  RELATIVE MISMATCH  — "today"/"tomorrow"/"yesterday" paired with a date that
                       does not match the system clock

Pure standard library. One space after periods in all output.
"""

import calendar
import re
import sys
from datetime import date, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
            "fri": 4, "sat": 5, "sun": 6}
for i, d in enumerate(DAYS):
    DAY_ABBR[d] = i

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

DOW_PAT = r"(?P<dow>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)"
MONTH_PAT = r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
DAY_NUM = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
YEAR = r"(?P<year>\d{4})"
YR2 = r"(?P<year>\d{2}|\d{4})"

# Each pattern yields groups: optional dow, and either (month name, day, year?) or numeric m/d/y, or dotted y.m.d.
PATTERNS = [
    # Tuesday, March 17, 2026 / Tuesday March 17 / March 17th, 2026 / Mar 17
    re.compile(rf"(?:{DOW_PAT},?\s+)?{MONTH_PAT}\.?\s+{DAY_NUM}(?:,?\s+{YEAR})?", re.IGNORECASE),
    # 3/17/2026 (Tuesday) / Tuesday 3/17/26 / 3-17-2026 / 3/17 (Tuesday)
    re.compile(rf"(?:{DOW_PAT},?\s+)?(?P<mnum>\d{{1,2}})[/-](?P<day>\d{{1,2}})(?:[/-]{YR2})?(?:\s*\(\s*(?P<dow2>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)\s*\))?", re.IGNORECASE),
    # 2026.03.17 file-naming convention
    re.compile(r"(?P<year>\d{4})\.(?P<mnum>\d{1,2})\.(?P<day>\d{1,2})"),
]

RELATIVE_PAT = re.compile(
    rf"(?P<rel>today|tomorrow|yesterday)[,:\s]+(?:{DOW_PAT},?\s+)?{MONTH_PAT}\.?\s+{DAY_NUM}(?:,?\s+{YEAR})?",
    re.IGNORECASE)


def context(text, start, end, width=40):
    s = max(0, start - width)
    e = min(len(text), end + width)
    snippet = text[s:e].replace("\n", " ")
    return "..." + snippet.strip() + "..."


def normalize_year(y, today):
    if y is None:
        return today.year, True  # assumed
    y = int(y)
    if y < 100:
        y += 2000
    return y, False


def parse_match(m, today):
    gd = m.groupdict()
    day = gd.get("day")
    if day is None:
        return None
    if gd.get("month"):
        month = MONTHS.get(gd["month"].lower().rstrip("."))
    elif gd.get("mnum"):
        month = int(gd["mnum"])
    else:
        return None
    year, assumed = normalize_year(gd.get("year"), today)
    dow = gd.get("dow") or gd.get("dow2")
    return {"month": month, "day": int(day), "year": year,
            "year_assumed": assumed, "dow": dow}


def check_text(text):
    today = date.today()
    errors = []
    notes = []
    seen = {}  # (y,m,d) -> set of day names claimed
    found = 0
    consumed = []  # spans already matched, to avoid double-reporting

    def overlaps(a, b):
        return not (a[1] <= b[0] or b[1] <= a[0])

    for pat in PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if any(overlaps(span, c) for c in consumed):
                continue
            parsed = parse_match(m, today)
            if parsed is None:
                continue
            consumed.append(span)
            found += 1
            ctx = context(text, *span)
            mo, dy, yr = parsed["month"], parsed["day"], parsed["year"]
            # Validity
            try:
                d = date(yr, mo, dy)
            except ValueError:
                errors.append(f"INVALID DATE: '{m.group(0).strip()}' is not a real calendar date. {ctx}")
                continue
            # DOW check
            if parsed["dow"]:
                claimed = DAY_ABBR.get(parsed["dow"].lower().rstrip("."))
                actual = d.weekday()
                if claimed is not None and claimed != actual:
                    correct = calendar.day_name[actual]
                    err = (f"DOW MISMATCH: '{m.group(0).strip()}' — {d.strftime('%B %d, %Y')} "
                           f"is a {correct}, not {calendar.day_name[claimed]}. {ctx}")
                    if parsed["year_assumed"]:
                        err += f" [year {yr} assumed — verify if the document concerns a different year]"
                    errors.append(err)
                else:
                    key = (yr, mo, dy)
                    seen.setdefault(key, set()).add(calendar.day_name[actual])
            # Cross-consistency bookkeeping (record claimed names even when correct)
            if parsed["dow"]:
                key = (yr, mo, dy)
                claimed = DAY_ABBR.get(parsed["dow"].lower().rstrip("."))
                if claimed is not None:
                    seen.setdefault(key, set()).add(calendar.day_name[claimed])
            if parsed["year_assumed"]:
                notes.append(f"NOTE: '{m.group(0).strip()}' has no year — assumed {yr}.")

    # Cross-consistency: same date claimed with 2+ different day names
    for (yr, mo, dy), names in seen.items():
        if len(names) > 1:
            actual = calendar.day_name[date(yr, mo, dy).weekday()]
            errors.append(f"INCONSISTENCY: {calendar.month_name[mo]} {dy}, {yr} is labeled "
                          f"{' and '.join(sorted(names))} in different places. Correct: {actual}.")

    # Relative dates paired with explicit dates
    for m in RELATIVE_PAT.finditer(text):
        parsed = parse_match(m, today)
        if parsed is None:
            continue
        rel = m.group("rel").lower()
        target = {"today": today, "tomorrow": today + timedelta(days=1),
                  "yesterday": today - timedelta(days=1)}[rel]
        try:
            stated = date(parsed["year"], parsed["month"], parsed["day"])
        except ValueError:
            continue  # already reported as invalid
        if stated != target:
            errors.append(f"RELATIVE MISMATCH: '{m.group(0).strip()}' — '{rel}' is "
                          f"{target.strftime('%A, %B %d, %Y')} by the system clock. "
                          f"{context(text, m.start(), m.end())}")

    return found, errors, notes


def main():
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print(f"ERROR: cannot read {sys.argv[1]}: {e}")
            return 2
    else:
        text = sys.stdin.read()
    if not text.strip():
        print("ERROR: no input text.")
        return 2

    found, errors, notes = check_text(text)
    print(f"Dates found: {found}")
    print(f"System clock: {date.today().strftime('%A, %B %d, %Y')}")
    for n in notes:
        print(n)
    if errors:
        print(f"\nFAIL — {len(errors)} error(s):")
        for e in errors:
            print(f"  {e}")
        return 1
    print("\nPASS — all dates verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
