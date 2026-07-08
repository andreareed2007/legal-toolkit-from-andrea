#!/usr/bin/env python3
"""
Federal court-specific validation checks.

Spec-conformance checks for the locked federal caption spec
(specs/COURT_FILING_FEDERAL_CAPTION_SPEC.md, bundled with this skill)
run BEFORE any heuristic checks.  Spec-conformance checks have IDs starting
with "FED_SPEC_".  Legacy heuristic checks retain "FEDERAL_CAPTION" IDs.
"""

import re
from validate_core import (
    find, find1, get_attr, extract_text, CheckResult, parse_docx
)


# Spec constants (from COURT_FILING_FEDERAL_CAPTION_SPEC.md)
EXPECTED_COL_WIDTHS = (4320, 720, 4320)
WIDTH_TOLERANCE = 30
EXPECTED_FONT = "Century Schoolbook"
EXPECTED_SIZE_HALF_PT = 24
SECTION_SIGN = "§"
CASE_NUMBER_PREFIXES = ("Civil Action No.", "Case No.")
CASE_NUMBER_PATTERN = re.compile(r"\d+:\d+-[Cc][Vv]-\d+")


# ─────────────── helpers ───────────────

def _get_run_props(run):
    return find1(run, "w:rPr")


def _has_bold(run):
    rpr = _get_run_props(run)
    if rpr is None:
        return False
    b = find1(rpr, "w:b")
    if b is None:
        return False
    val = get_attr(b, "val")
    return val is None or val not in ("0", "false")


def _has_italic(run):
    rpr = _get_run_props(run)
    if rpr is None:
        return False
    i = find1(rpr, "w:i")
    if i is None:
        return False
    val = get_attr(i, "val")
    return val is None or val not in ("0", "false")


def _has_underline(run):
    rpr = _get_run_props(run)
    if rpr is None:
        return False
    u = find1(rpr, "w:u")
    if u is None:
        return False
    val = get_attr(u, "val")
    return val not in (None, "none", "0", "false")


def _font_name(run):
    rpr = _get_run_props(run)
    if rpr is None:
        return None
    f = find1(rpr, "w:rFonts")
    if f is None:
        return None
    return get_attr(f, "ascii") or get_attr(f, "hAnsi")


def _font_size(run):
    rpr = _get_run_props(run)
    if rpr is None:
        return None
    s = find1(rpr, "w:sz")
    if s is None:
        return None
    try:
        return int(get_attr(s, "val"))
    except (TypeError, ValueError):
        return None


def _para_alignment(p):
    ppr = find1(p, "w:pPr")
    if ppr is None:
        return None
    jc = find1(ppr, "w:jc")
    if jc is None:
        return None
    return get_attr(jc, "val")


def _para_spacing(p):
    ppr = find1(p, "w:pPr")
    if ppr is None:
        return (None, None, None, None)
    sp = find1(ppr, "w:spacing")
    if sp is None:
        return (None, None, None, None)
    return (
        get_attr(sp, "before"),
        get_attr(sp, "after"),
        get_attr(sp, "line"),
        get_attr(sp, "lineRule"),
    )


def _cell_borders_all_nil(cell):
    tcpr = find1(cell, "w:tcPr")
    if tcpr is None:
        return False
    borders = find1(tcpr, "w:tcBorders")
    if borders is None:
        return False
    for side in ("top", "left", "bottom", "right"):
        b = find1(borders, f"w:{side}")
        if b is None:
            return False
        if get_attr(b, "val") != "nil":
            return False
    return True


def _table_borders_all_nil(table):
    tblpr = find1(table, "w:tblPr")
    if tblpr is None:
        return False
    borders = find1(tblpr, "w:tblBorders")
    if borders is None:
        return False
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = find1(borders, f"w:{side}")
        if b is None:
            return False
        if get_attr(b, "val") != "nil":
            return False
    return True


def _cell_margins_all_zero(cell):
    """True if every cell margin is 0 DXA."""
    tcpr = find1(cell, "w:tcPr")
    if tcpr is None:
        return False
    tcmar = find1(tcpr, "w:tcMar")
    if tcmar is None:
        return False
    for side in ("top", "left", "bottom", "right"):
        m = find1(tcmar, f"w:{side}")
        if m is None:
            return False
        try:
            if int(get_attr(m, "w")) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _cell_width_dxa(cell):
    """Return cell width in DXA, or None."""
    tcpr = find1(cell, "w:tcPr")
    if tcpr is None:
        return None
    tcw = find1(tcpr, "w:tcW")
    if tcw is None:
        return None
    try:
        return int(get_attr(tcw, "w"))
    except (TypeError, ValueError):
        return None


def _cell_vertical_alignment(cell):
    """Return 'top'/'center'/'bottom' or None."""
    tcpr = find1(cell, "w:tcPr")
    if tcpr is None:
        return None
    valign = find1(tcpr, "w:vAlign")
    if valign is None:
        return None
    return get_attr(valign, "val")


# ─────────────── Spec-conformance checks (FED_SPEC_*) ───────────────

def check_court_header(files):
    """FED_SPEC_001 -- Court name header above caption table."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results
    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    first_paras = find(body, "w:p")[:5]
    found_header = False
    for p in first_paras:
        text = extract_text(p).upper()
        if "UNITED STATES DISTRICT COURT" in text:
            found_header = True
            jc = _para_alignment(p)
            if jc == "center":
                results.append(CheckResult(
                    "FED_SPEC_001", "PASS",
                    "Court name header found and centered"))
            else:
                results.append(CheckResult(
                    "FED_SPEC_001", "FAIL",
                    f"Court name header alignment is '{jc}' (should be 'center')"))
            runs = find(p, ".//w:r")
            any_bold = any(_has_bold(r) for r in runs)
            if not any_bold:
                results.append(CheckResult(
                    "FED_SPEC_001", "WARN",
                    "Court name header is not bold (spec requires bold)"))
            break

    if not found_header:
        results.append(CheckResult(
            "FED_SPEC_001", "FAIL",
            "MISSING: 'IN THE UNITED STATES DISTRICT COURT' header above caption table"))
    return results


def check_caption_table_structure(files):
    """FED_SPEC_002 / 003 / 004 / 005 / 006 / 007 / 008 / 009 / 010."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    tables = find(doc_xml, ".//w:tbl")
    if not tables:
        results.append(CheckResult(
            "FED_SPEC_002", "FAIL", "No caption table found in document"))
        return results

    caption = tables[0]
    rows = find(caption, "w:tr")
    if len(rows) != 1:
        results.append(CheckResult(
            "FED_SPEC_002", "FAIL",
            f"Caption table has {len(rows)} rows; spec requires exactly 1"))
        return results

    cells = find(rows[0], "w:tc")
    if len(cells) != 3:
        results.append(CheckResult(
            "FED_SPEC_002", "FAIL",
            f"Caption table has {len(cells)} columns; spec requires exactly 3"))
        return results

    results.append(CheckResult(
        "FED_SPEC_002", "PASS", "Caption table is 1 row x 3 columns"))

    # FED_SPEC_003: column widths
    actual_widths = tuple(_cell_width_dxa(c) for c in cells)
    width_ok = all(
        a is not None and abs(a - e) <= WIDTH_TOLERANCE
        for a, e in zip(actual_widths, EXPECTED_COL_WIDTHS)
    )
    if width_ok:
        results.append(CheckResult(
            "FED_SPEC_003", "PASS",
            f"Column widths {actual_widths} match spec"))
    else:
        results.append(CheckResult(
            "FED_SPEC_003", "FAIL",
            f"Column widths {actual_widths} do not match spec {EXPECTED_COL_WIDTHS} "
            f"(tolerance {WIDTH_TOLERANCE} DXA)"))

    # FED_SPEC_004: table borders
    if _table_borders_all_nil(caption):
        results.append(CheckResult(
            "FED_SPEC_004", "PASS", "All caption table borders are nil"))
    else:
        results.append(CheckResult(
            "FED_SPEC_004", "FAIL",
            "Caption table borders are not all nil -- caption table must be borderless"))

    # FED_SPEC_005: cell borders
    bad_borders = [i for i, c in enumerate(cells, 1) if not _cell_borders_all_nil(c)]
    if not bad_borders:
        results.append(CheckResult(
            "FED_SPEC_005", "PASS", "All caption cell borders are nil"))
    else:
        results.append(CheckResult(
            "FED_SPEC_005", "FAIL", f"Cells {bad_borders} have non-nil borders"))

    # FED_SPEC_006: cell margins
    bad_margins = [i for i, c in enumerate(cells, 1) if not _cell_margins_all_zero(c)]
    if not bad_margins:
        results.append(CheckResult(
            "FED_SPEC_006", "PASS", "All caption cell margins are zero"))
    else:
        results.append(CheckResult(
            "FED_SPEC_006", "FAIL", f"Cells {bad_margins} have non-zero margins"))

    # FED_SPEC_007: vertical alignment
    bad_valign = [
        i for i, c in enumerate(cells, 1)
        if _cell_vertical_alignment(c) not in (None, "top")
    ]
    if not bad_valign:
        results.append(CheckResult(
            "FED_SPEC_007", "PASS", "All caption cells have top vertical alignment"))
    else:
        results.append(CheckResult(
            "FED_SPEC_007", "WARN", f"Cells {bad_valign} are not top-aligned"))

    # FED_SPEC_008: section sign in center column
    center_text = extract_text(cells[1])
    if SECTION_SIGN in center_text:
        results.append(CheckResult(
            "FED_SPEC_008", "PASS",
            "Center column contains section-sign symbols"))
    else:
        results.append(CheckResult(
            "FED_SPEC_008", "FAIL",
            "Center column missing section-sign symbols (federal caption requires section signs)"))

    # FED_SPEC_009: case number
    right_text = extract_text(cells[2])
    has_prefix = any(p in right_text for p in CASE_NUMBER_PREFIXES)
    has_pattern = bool(CASE_NUMBER_PATTERN.search(right_text))
    if has_prefix and has_pattern:
        results.append(CheckResult(
            "FED_SPEC_009", "PASS",
            "Right column has case number with valid prefix and federal format"))
    elif has_prefix and not has_pattern:
        results.append(CheckResult(
            "FED_SPEC_009", "WARN",
            "Right column has 'Civil Action No.' / 'Case No.' prefix but no matching "
            "federal case number pattern (e.g., 3:24-cv-00123)"))
    elif has_pattern and not has_prefix:
        results.append(CheckResult(
            "FED_SPEC_009", "WARN",
            "Right column has federal case number format but missing prefix"))
    else:
        results.append(CheckResult(
            "FED_SPEC_009", "FAIL",
            "Right column missing case number -- expected 'Civil Action No. <num>' "
            "or 'Case No. <num>' in federal format"))

    # FED_SPEC_010: caption font
    bad_font = bad_size = total = 0
    for cell in cells:
        for run in find(cell, ".//w:r"):
            if not find(run, "w:t"):
                continue
            total += 1
            f = _font_name(run)
            if f and f != EXPECTED_FONT:
                bad_font += 1
            sz = _font_size(run)
            if sz is not None and sz != EXPECTED_SIZE_HALF_PT:
                bad_size += 1
    if total == 0:
        results.append(CheckResult(
            "FED_SPEC_010", "WARN", "No caption runs to check font on"))
    elif bad_font == 0 and bad_size == 0:
        results.append(CheckResult(
            "FED_SPEC_010", "PASS",
            f"Caption font is {EXPECTED_FONT} {EXPECTED_SIZE_HALF_PT//2}pt throughout"))
    else:
        results.append(CheckResult(
            "FED_SPEC_010", "FAIL",
            f"Caption font/size deviations: {bad_font} non-{EXPECTED_FONT} runs, "
            f"{bad_size} non-{EXPECTED_SIZE_HALF_PT//2}pt runs (of {total} total)"))

    return results


def check_no_judge_preamble(files):
    """FEDERAL_CAPTION heuristic: no 'TO THE HONORABLE JUDGE' preamble."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results
    for p in find(doc_xml, ".//w:p"):
        text = extract_text(p).upper()
        if "TO THE HONORABLE" in text and "JUDGE" in text:
            results.append(CheckResult(
                "FEDERAL_CAPTION", "FAIL",
                "Document contains 'TO THE HONORABLE JUDGE' preamble -- "
                "this filing style never includes this"))
            return results
    results.append(CheckResult(
        "FEDERAL_CAPTION", "PASS", "No 'TO THE HONORABLE JUDGE' preamble"))
    return results


def run_federal_checks(doc_path):
    """Run federal court-specific validation checks.

    Args:
        doc_path: Path to .docx file.

    Returns:
        List of CheckResult (FED_SPEC_* spec checks then heuristics).
    """
    files = parse_docx(doc_path)
    results = []
    results.extend(check_court_header(files))
    results.extend(check_caption_table_structure(files))
    results.extend(check_no_judge_preamble(files))
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validate_federal.py <path_to_docx>")
        sys.exit(1)
    results = run_federal_checks(sys.argv[1])
    for r in results:
        icon = {"PASS": "OK", "FAIL": "X", "WARN": "!"}[r.status]
        print(f"[{icon}] {r.status}: {r.check} -- {r.message}")
