#!/usr/bin/env python3
"""
Texas state court-specific validation checks.

Spec-conformance checks for the locked TX state caption spec
(specs/COURT_FILING_TX_STATE_CAPTION_SPEC.md, bundled with this skill)
run BEFORE any heuristic checks.  Spec-conformance checks have IDs starting
with "TX_SPEC_".  Legacy heuristic checks retain "TX_CAPTION" IDs.
"""

import re
from validate_core import (
    find, find1, get_attr, extract_text, CheckResult, parse_docx, NSMAP
)


# ────────────── Spec constants (from COURT_FILING_TX_STATE_CAPTION_SPEC.md)
EXPECTED_COL_WIDTHS = (4320, 720, 4320)   # DXA: 3.0" / 0.5" / 3.0"
WIDTH_TOLERANCE = 30                       # DXA -- small rounding tolerance

EXPECTED_FONT = "Century Schoolbook"
EXPECTED_SIZE_HALF_PT = 24                # 12 pt = 24 half-points

SECTION_SIGN = "§"


# ────────────── Helpers ──────────────

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
    tcpr = find1(cell, "w:tcPr")
    if tcpr is None:
        return None
    valign = find1(tcpr, "w:vAlign")
    if valign is None:
        return None
    return get_attr(valign, "val")


# ────────────── Spec-conformance checks (TX_SPEC_*) ──────────────

def check_cause_number_above_table(files):
    """TX_SPEC_001 -- Cause number paragraph above caption table (centered)."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results
    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    children = list(body)
    table_idx = None
    for i, child in enumerate(children):
        tag = child.tag.split("}", 1)[-1]
        if tag == "tbl":
            table_idx = i
            break

    if table_idx is None:
        results.append(CheckResult(
            "TX_SPEC_001", "FAIL", "No caption table found in document"))
        return results

    pre_paras = [c for c in children[:table_idx]
                 if c.tag.split("}", 1)[-1] == "p"]
    if not pre_paras:
        results.append(CheckResult(
            "TX_SPEC_001", "FAIL",
            "No paragraphs above caption table -- cause number paragraph required"))
        return results

    cause_re = re.compile(r"\bCAUSE\b|\bNO\.\s*\S+", re.IGNORECASE)
    found = False
    for p in pre_paras:
        text = extract_text(p)
        if not text.strip():
            continue
        if cause_re.search(text):
            found = True
            jc = _para_alignment(p)
            if jc == "center":
                results.append(CheckResult(
                    "TX_SPEC_001", "PASS",
                    "Cause number paragraph found above table and centered"))
            else:
                results.append(CheckResult(
                    "TX_SPEC_001", "FAIL",
                    f"Cause number paragraph above table is not centered (jc='{jc}')"))
            if text != text.upper():
                results.append(CheckResult(
                    "TX_SPEC_001", "WARN",
                    "Cause number paragraph is not in ALL CAPS (spec recommends ALL CAPS)"))
            break

    if not found:
        results.append(CheckResult(
            "TX_SPEC_001", "FAIL",
            "MISSING: Cause number paragraph above caption table "
            "(expected centered, ALL CAPS, e.g., 'CAUSE NO. DC-00-00000')"))
    return results


def check_caption_table_structure(files):
    """TX_SPEC_002 / 003 / 004 / 005 / 006 / 007 / 008 / 009."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    tables = find(doc_xml, ".//w:tbl")
    if not tables:
        results.append(CheckResult(
            "TX_SPEC_002", "FAIL", "No caption table found in document"))
        return results

    caption = tables[0]
    rows = find(caption, "w:tr")
    if len(rows) != 1:
        results.append(CheckResult(
            "TX_SPEC_002", "FAIL",
            f"Caption table has {len(rows)} rows; spec requires exactly 1"))
        return results

    cells = find(rows[0], "w:tc")
    if len(cells) != 3:
        results.append(CheckResult(
            "TX_SPEC_002", "FAIL",
            f"Caption table has {len(cells)} columns; spec requires exactly 3"))
        return results

    results.append(CheckResult(
        "TX_SPEC_002", "PASS", "Caption table is 1 row x 3 columns"))

    # TX_SPEC_003: column widths
    actual_widths = tuple(_cell_width_dxa(c) for c in cells)
    width_ok = all(
        a is not None and abs(a - e) <= WIDTH_TOLERANCE
        for a, e in zip(actual_widths, EXPECTED_COL_WIDTHS)
    )
    if width_ok:
        results.append(CheckResult(
            "TX_SPEC_003", "PASS",
            f"Column widths {actual_widths} match spec"))
    else:
        results.append(CheckResult(
            "TX_SPEC_003", "FAIL",
            f"Column widths {actual_widths} do not match spec {EXPECTED_COL_WIDTHS} "
            f"(tolerance {WIDTH_TOLERANCE} DXA)"))

    # TX_SPEC_004: table borders
    if _table_borders_all_nil(caption):
        results.append(CheckResult(
            "TX_SPEC_004", "PASS", "All caption table borders are nil"))
    else:
        results.append(CheckResult(
            "TX_SPEC_004", "FAIL",
            "Caption table borders are not all nil -- caption table must be borderless"))

    # TX_SPEC_005: cell borders
    bad_borders = [i for i, c in enumerate(cells, 1) if not _cell_borders_all_nil(c)]
    if not bad_borders:
        results.append(CheckResult(
            "TX_SPEC_005", "PASS", "All caption cell borders are nil"))
    else:
        results.append(CheckResult(
            "TX_SPEC_005", "FAIL", f"Cells {bad_borders} have non-nil borders"))

    # TX_SPEC_006: cell margins
    bad_margins = [i for i, c in enumerate(cells, 1) if not _cell_margins_all_zero(c)]
    if not bad_margins:
        results.append(CheckResult(
            "TX_SPEC_006", "PASS", "All caption cell margins are zero"))
    else:
        results.append(CheckResult(
            "TX_SPEC_006", "FAIL", f"Cells {bad_margins} have non-zero margins"))

    # TX_SPEC_007: vertical alignment
    bad_valign = [
        i for i, c in enumerate(cells, 1)
        if _cell_vertical_alignment(c) not in (None, "top")
    ]
    if not bad_valign:
        results.append(CheckResult(
            "TX_SPEC_007", "PASS", "All caption cells have top vertical alignment"))
    else:
        results.append(CheckResult(
            "TX_SPEC_007", "WARN", f"Cells {bad_valign} are not top-aligned"))

    # TX_SPEC_008: section sign in center column
    center_text = extract_text(cells[1])
    if SECTION_SIGN in center_text:
        results.append(CheckResult(
            "TX_SPEC_008", "PASS",
            "Center column contains section-sign symbols"))
    else:
        results.append(CheckResult(
            "TX_SPEC_008", "FAIL",
            "Center column missing section-sign symbols (TX state caption requires section signs)"))

    # TX_SPEC_009: caption font
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
            "TX_SPEC_009", "WARN", "No caption runs to check font on"))
    elif bad_font == 0 and bad_size == 0:
        results.append(CheckResult(
            "TX_SPEC_009", "PASS",
            f"Caption font is {EXPECTED_FONT} {EXPECTED_SIZE_HALF_PT//2}pt throughout"))
    else:
        results.append(CheckResult(
            "TX_SPEC_009", "FAIL",
            f"Caption font/size deviations: {bad_font} non-{EXPECTED_FONT} runs, "
            f"{bad_size} non-{EXPECTED_SIZE_HALF_PT//2}pt runs (of {total} total)"))

    return results


def check_no_judge_preamble(files):
    """TX_CAPTION heuristic: no 'TO THE HONORABLE JUDGE' preamble."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results
    for p in find(doc_xml, ".//w:p"):
        text = extract_text(p).upper()
        if "TO THE HONORABLE" in text and "JUDGE" in text:
            results.append(CheckResult(
                "TX_CAPTION", "FAIL",
                "Document contains 'TO THE HONORABLE JUDGE' preamble -- "
                "this filing style never includes this"))
            return results
    results.append(CheckResult(
        "TX_CAPTION", "PASS", "No 'TO THE HONORABLE JUDGE' preamble"))
    return results


def run_tx_state_checks(doc_path):
    """Run Texas state court-specific validation checks."""
    files = parse_docx(doc_path)
    results = []
    results.extend(check_cause_number_above_table(files))
    results.extend(check_caption_table_structure(files))
    results.extend(check_no_judge_preamble(files))
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validate_tx_state.py <path_to_docx>")
        sys.exit(1)
    results = run_tx_state_checks(sys.argv[1])
    for r in results:
        icon = {"PASS": "OK", "FAIL": "X", "WARN": "!"}[r.status]
        print(f"[{icon}] {r.status}: {r.check} -- {r.message}")
