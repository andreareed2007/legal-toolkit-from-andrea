#!/usr/bin/env python3
"""
tanbook.py -- deterministic NY Law Reports Style Manual ("Tanbook") citation
formatter and validator.

Scope: the within-parentheses display style for CASE and STATUTE citations.
Rule source of truth: ../reference/TANBOOK_RULES.md (2022 ed.).

This module does the MECHANICAL transforms only. Judgment calls (which appellate
history is pertinent, county->department, assembling missing parallel cites) are
left to the calling model / enrich mode. Functions never raise on odd input;
validate() reports problems instead.

CLI:
    python3 tanbook.py convert  "<text>"     # Bluebook -> Tanbook
    python3 tanbook.py validate "<text>"     # report nonconformities (JSON)
    python3 tanbook.py selftest              # run built-in test set
"""
import re
import sys
import json

# ---------------------------------------------------------------------------
# 1. Reporter de-periodizing + spacing.
#    Ordered list: most specific FIRST. \s* absorbs Bluebook spacing.
#    "Closed" reporters emit NO space before the series (NY3d). "Spaced"
#    reporters keep a space (Misc 3d, F Supp 3d, App Div, App Term).
# ---------------------------------------------------------------------------
_REPORTER_SUBS = [
    # statutes that look like reporters -- do these BEFORE U.S. etc.
    (r"U\.\s*S\.\s*C\.", "USC"),
    (r"C\.\s*F\.\s*R\.", "CFR"),
    # NY official + unofficial
    (r"N\.\s*Y\.\s*S\.\s*(\d)d", r"NYS\1d"),
    (r"N\.\s*Y\.\s*S\.", "NYS"),
    (r"N\.\s*Y\.\s*(\d)d", r"NY\1d"),
    (r"N\.\s*Y\.", "NY"),
    (r"N\.\s*E\.\s*(\d)d", r"NE\1d"),
    (r"N\.\s*E\.", "NE"),
    (r"A\.\s*D\.\s*(\d)d", r"AD\1d"),
    (r"A\.\s*D\.", "AD"),
    (r"App\.\s*Div\.", "App Div"),
    (r"App\.\s*Term", "App Term"),
    (r"Misc\.\s*(\d)d", r"Misc \1d"),   # SPACE kept
    (r"Misc\.", "Misc"),
    # federal
    (r"F\.\s*Supp\.\s*(\d)d", r"F Supp \1d"),
    (r"F\.\s*Supp\.", "F Supp"),
    (r"F\.\s*4th", "F4th"),
    (r"F\.\s*(\d)d", r"F\1d"),
    (r"F\.\s*App'?x\.?", "Fed Appx"),
    (r"Fed\.\s*App'?x\.?", "Fed Appx"),
    (r"B\.\s*R\.", "BR"),
    (r"S\.\s*Ct\.", "S Ct"),
    (r"L\.\s*Ed\.\s*(\d)d", r"L Ed \1d"),
    (r"L\.\s*Ed\.", "L Ed"),
    (r"U\.\s*S\.", "US"),
    # regional reporters (do AFTER A.D. so A. doesn't eat A.D.)
    (r"N\.\s*W\.\s*(\d)d", r"NW\1d"),
    (r"N\.\s*W\.", "NW"),
    (r"S\.\s*W\.\s*(\d)d", r"SW\1d"),
    (r"S\.\s*W\.", "SW"),
    (r"So\.\s*(\d)d", r"So \1d"),        # SPACE kept (Southern)
    (r"So\.", "So"),
    (r"A\.\s*(\d)d", r"A\1d"),
    (r"P\.\s*(\d)d", r"P\1d"),
    (r"Cal\.\s*(\d)th", r"Cal \1th"),
]

# Close up a leftover space between a closed reporter and its series number,
# e.g. an input already lacking periods but written "NY 3d".
_CLOSE_SPACING = [
    (r"\b(NY|AD|NYS|NE|SW|NW|US|BR)\s+(\d+(?:d|th)\b)", r"\1\2"),
    (r"\b(F)\s+(\d+(?:d|th)\b)", r"\1\2"),
    (r"\b(A|P)\s+(\d+d\b)", r"\1\2"),
]


def fix_reporters(text):
    for pat, rep in _REPORTER_SUBS:
        text = re.sub(pat, rep, text)
    for pat, rep in _CLOSE_SPACING:
        text = re.sub(pat, rep, text)
    return text


# A single capitalized word with a trailing period sandwiched between numbers is
# an out-of-state official reporter (e.g. "333 Conn. 1" -> "333 Conn 1"). This
# safely leaves case-name abbreviations alone (Univ., Assn., Co.) because those
# are not bracketed by digits.
def fix_state_reporters(text):
    return re.sub(r"(\d+\s+)([A-Z][A-Za-z]+)\.(\s+\d)", r"\1\2\3", text)


# ---------------------------------------------------------------------------
# 2. "v." -> "v"
# ---------------------------------------------------------------------------
def fix_versus(text):
    return re.sub(r"\bv\.\s", "v ", text)


# ---------------------------------------------------------------------------
# 3. Date / court parenthetical -> square brackets.
#    Targets a (...) group with NO nested parens/brackets whose content ends in
#    a 4-digit year (optionally trailed by a decision-type word like "mem").
#    Leaves quoting/explanatory parentheticals alone.
# ---------------------------------------------------------------------------
_DATE_PAREN = re.compile(
    r"\(\s*([^()\[\]]*?(?:19|20)\d{2}[A-Za-z .&]*?)\s*\)"
)


def fix_date_brackets(text):
    return _DATE_PAREN.sub(lambda m: "[" + m.group(1).strip() + "]", text)


# ---------------------------------------------------------------------------
# 4. Page-range de-truncation: 316-17 -> 316-317. Skip 4-digit firsts (years).
# ---------------------------------------------------------------------------
def fix_page_ranges(text):
    def _expand(m):
        a, b = m.group(1), m.group(2)
        if len(a) >= 4:          # likely a year range, leave alone
            return m.group(0)
        if len(b) < len(a):
            b = a[: len(a) - len(b)] + b
        return f"{a}-{b}"
    return re.sub(r"\b(\d{1,3})-(\d{1,3})\b", _expand, text)


# ---------------------------------------------------------------------------
# convert(): full Bluebook -> Tanbook pass (mechanical only).
# ---------------------------------------------------------------------------
def convert(text):
    text = fix_reporters(text)
    text = fix_state_reporters(text)
    text = fix_versus(text)
    text = fix_date_brackets(text)
    text = fix_page_ranges(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# validate(): report nonconformities without changing the text.
# Returns list of {"issue","detail","suggestion"}.
# ---------------------------------------------------------------------------
_OFFICIAL_NY = re.compile(r"\b\d+\s*(?:NY[23]?d?|AD[23]?d?|Misc(?: [23]d)?)\b")
_NY_PARALLEL = re.compile(r"\b\d+\s*(?:NYS\d?d?|NE\d?d?)\b")
_SIGNAL_COMMA = re.compile(
    r"\b(see also|but see|but cf|see e\.g|but see e\.g|see generally|see|cf|accord|contra|compare|e\.g)\s*,",
    re.IGNORECASE,
)


def validate(text):
    issues = []

    if re.search(r"N\.\s*Y\.|A\.\s*D\.|Misc\.|N\.\s*Y\.\s*S\.|N\.\s*E\.", text):
        issues.append({
            "issue": "periods_in_ny_reporter",
            "detail": "NY reporter abbreviation contains periods.",
            "suggestion": "Use NY / NY2d / NY3d / AD3d / Misc 3d / NYS2d / NE2d (no periods).",
        })

    if re.search(r"\bv\.\s", text):
        issues.append({
            "issue": "versus_period",
            "detail": "'v.' should be 'v' (no period) in case names.",
            "suggestion": "Replace 'v.' with 'v'.",
        })

    if _DATE_PAREN.search(text):
        issues.append({
            "issue": "date_in_parens",
            "detail": "Court/jurisdiction/year appears in parentheses.",
            "suggestion": "Put court, jurisdiction and year in SQUARE BRACKETS, e.g. [1st Dept 2020].",
        })

    for m in re.finditer(r"\b(\d{1,3})-(\d{1,3})\b", text):
        a, b = m.group(1), m.group(2)
        if len(a) < 4 and len(b) < len(a):
            issues.append({
                "issue": "truncated_page_range",
                "detail": f"Page range '{m.group(0)}' is truncated.",
                "suggestion": f"Do not truncate: write {a}-{a[:len(a)-len(b)]+b}.",
            })

    if _SIGNAL_COMMA.search(text):
        issues.append({
            "issue": "comma_after_signal",
            "detail": "Comma placed between an introductory signal and the citation.",
            "suggestion": "Remove the comma after the signal (e.g. 'see Smith', not 'see, Smith').",
        })

    # Officially reported NY case carrying an improper unofficial parallel.
    if _OFFICIAL_NY.search(text) and _NY_PARALLEL.search(text):
        issues.append({
            "issue": "improper_ny_parallel",
            "detail": "An officially reported NY case appears with an NYS2d/NE2d parallel cite.",
            "suggestion": "Parallel unofficial cites are NOT used for officially reported NY cases (2.2 [b] [1]). Cite the official report only.",
        })

    if re.search(r"\bsupra\b", text) and re.search(r"\d+\s*(NY|AD|Misc)", text):
        issues.append({
            "issue": "supra_shortform",
            "detail": "Possible use of 'supra' to shorten a case citation.",
            "suggestion": "Do not use supra to shorten a cite (1.3 [b] [2]); use a short-form name or id.",
        })

    return issues


# ---------------------------------------------------------------------------
# Built-in test set (known-good Tanbook strings from the manual).
# ---------------------------------------------------------------------------
_TESTS = [
    # (bluebook_input, expected_tanbook)
    ("People v. Wilkins, 37 N.Y.3d 371 (2021)",
     "People v Wilkins, 37 NY3d 371 [2021]"),
    ("Matter of Cornell Univ. v. Beer, 16 A.D.3d 890 (3d Dept 2005)",
     "Matter of Cornell Univ. v Beer, 16 AD3d 890 [3d Dept 2005]"),
    ("Matter of DeOca, 75 Misc. 3d 449 (Sur Ct, Erie County 2022)",
     "Matter of DeOca, 75 Misc 3d 449 [Sur Ct, Erie County 2022]"),
    ("People v. Ramos, 90 N.Y.2d 490, 495 (1997)",
     "People v Ramos, 90 NY2d 490, 495 [1997]"),
    ("Matter of Sayeh R., 91 N.Y.2d 306, 316-17 (1997)",
     "Matter of Sayeh R., 91 NY2d 306, 316-317 [1997]"),
    ("Chrysafis v. Marks, 15 F.4th 208 (2d Cir 2021)",
     "Chrysafis v Marks, 15 F4th 208 [2d Cir 2021]"),
    ("Metcalf v. Fitzgerald, 333 Conn. 1, 214 A.3d 361 (2019)",
     "Metcalf v Fitzgerald, 333 Conn 1, 214 A3d 361 [2019]"),
    ("Ohralick v. Ohio State Bar Assn., 436 U.S. 447 (1978)",
     "Ohralick v Ohio State Bar Assn., 436 US 447 [1978]"),
    ("Mavrovich v. Vanderpool, 427 F. Supp. 2d 1084 (D Kan 2006)",
     "Mavrovich v Vanderpool, 427 F Supp 2d 1084 [D Kan 2006]"),
]


def selftest():
    passed = failed = 0
    for src, expected in _TESTS:
        got = convert(src)
        ok = got == expected
        passed += ok
        failed += (not ok)
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] {src}")
        if not ok:
            print(f"       expected: {expected}")
            print(f"       got:      {got}")
    print(f"\n{passed}/{passed+failed} passed")
    return failed == 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "selftest":
        return 0 if selftest() else 1
    if len(argv) < 3:
        print("error: missing text argument", file=sys.stderr)
        return 1
    text = argv[2]
    if cmd == "convert":
        print(convert(text))
    elif cmd == "validate":
        print(json.dumps(validate(text), indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
