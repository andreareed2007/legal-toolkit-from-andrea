#!/usr/bin/env python3
"""
Texas Business Court-specific validation checks.

This module handles checks unique to Texas Business Court filings (font 14pt,
caption rules per the locked caption spec, signature block firm-name treatment).
"""

import re
from validate_core import (
    find, find1, get_attr, extract_text, CheckResult
)

# Firm-name tokens the signature-block check looks for are read from the user
# profile (~/.legal-skills/config.json, written by environment-setup). When no
# profile/tokens exist, the sig-block firm check degrades to WARN via the
# generic "ATTORNEYS" anchor -- it never hard-fails a document.
try:
    from config_helper import firm_tokens as _load_firm_tokens
    _FIRM_TOKENS = _load_firm_tokens()
except Exception:
    _FIRM_TOKENS = []


def _has_firm_token(s):
    u = (s or "").upper()
    return any(tok in u for tok in _FIRM_TOKENS)


def check_bc_font_size(files):
    """BC override: expect 14pt (sz=28), not 12pt."""
    results = []
    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        return results

    doc_defaults = find1(styles_xml, ".//w:docDefaults")
    if doc_defaults is not None:
        rpr_default = find1(doc_defaults, ".//w:rPrDefault//w:rPr")
        if rpr_default is not None:
            sz = find1(rpr_default, "w:sz")
            if sz is not None:
                val = get_attr(sz, "val")
                if val == "28":
                    results.append(CheckResult("BC_FONT_SIZE", "PASS",
                                  "Default font size: 14pt (28 half-pts) -- correct for Business Court"))
                elif val == "24":
                    results.append(CheckResult("BC_FONT_SIZE", "FAIL",
                                "Default font size is 12pt (24) -- Business Court requires 14pt (28)"))
                else:
                    results.append(CheckResult("BC_FONT_SIZE", "FAIL",
                                f"Default font size is {int(val)//2}pt ({val}) -- Business Court requires 14pt (28)"))

    return results


def check_bc_heading_spacing(files):
    """Check heading spaceAfter = 240 (12pt) for BC."""
    results = []
    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        return results

    for style in find(styles_xml, ".//w:style"):
        style_id = get_attr(style, "styleId")
        if style_id and style_id.startswith("Heading"):
            ppr = find1(style, ".//w:pPr")
            if ppr is not None:
                spacing = find1(ppr, "w:spacing")
                if spacing is not None:
                    after_val = get_attr(spacing, "after")
                    if after_val == "240":
                        results.append(CheckResult("BC_HEADING_SPACING", "PASS",
                                      f"{style_id} spaceAfter=240 (12pt) -- correct"))
                    elif after_val == "120":
                        results.append(CheckResult("BC_HEADING_SPACING", "FAIL",
                                    f"{style_id} spaceAfter=120 (6pt) -- should be 240 (12pt)"))
                    else:
                        results.append(CheckResult("BC_HEADING_SPACING", "WARN",
                                    f"{style_id} spaceAfter={after_val} -- expected 240 (12pt)"))

    return results


def check_bc_heading1_center(files):
    """Check Heading 1 is centered with no indent for BC."""
    results = []
    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        return results

    for style in find(styles_xml, ".//w:style"):
        style_id = get_attr(style, "styleId")
        if style_id == "Heading1":
            ppr = find1(style, ".//w:pPr")
            if ppr is not None:
                jc = find1(ppr, "w:jc")
                if jc is not None and get_attr(jc, "val") == "center":
                    results.append(CheckResult("BC_HEADING1_ALIGN", "PASS", "Heading1 is center-aligned"))
                else:
                    val = get_attr(jc, "val") if jc is not None else "not set"
                    results.append(CheckResult("BC_HEADING1_ALIGN", "FAIL",
                                f"Heading1 alignment is '{val}' -- should be 'center'"))

                ind = find1(ppr, "w:ind")
                if ind is not None:
                    left = get_attr(ind, "left") or "0"
                    hanging = get_attr(ind, "hanging") or "0"
                    if left == "0" and hanging == "0":
                        results.append(CheckResult("BC_HEADING1_INDENT", "PASS",
                                      "Heading1 has no indent (left=0, hanging=0)"))
                    else:
                        results.append(CheckResult("BC_HEADING1_INDENT", "FAIL",
                                    f"Heading1 has indent left={left}, hanging={hanging} -- should be 0/0"))
                else:
                    results.append(CheckResult("BC_HEADING1_INDENT", "PASS",
                                  "Heading1 has no indent element (inherits 0)"))

    return results


def check_bc_caption(files):
    """Check BC caption per the locked caption spec."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is not None:
        first_real_child = None
        for child in body:
            tag = child.tag.split("}")[-1]
            if tag in ("p", "tbl"):
                first_real_child = tag
                break
        if first_real_child == "tbl":
            results.append(CheckResult("BC_CAPTION", "PASS",
                          "No paragraphs precede the caption table (BC requires zero pre-table content)"))
        elif first_real_child == "p":
            results.append(CheckResult("BC_CAPTION", "FAIL",
                        "Body has a paragraph before the caption table -- BC has no cause number or "
                        "court-name header above the table; remove pre-table paragraphs"))

    tables = find(doc_xml, ".//w:tbl")
    if not tables:
        results.append(CheckResult("BC_CAPTION", "FAIL", "No tables found in document"))
        return results

    caption = tables[0]
    rows = find(caption, ".//w:tr")

    if len(rows) == 1:
        results.append(CheckResult("BC_CAPTION", "PASS", "Caption is a single-row table (correct)"))
    else:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    f"Caption has {len(rows)} rows -- should be 1 (single row with stacked paragraphs)"))

    if not rows:
        return results

    cells = find(rows[0], "w:tc")
    if len(cells) != 3:
        results.append(CheckResult("BC_CAPTION", "FAIL", f"Caption has {len(cells)} columns -- should be 3"))
        return results

    results.append(CheckResult("BC_CAPTION", "PASS", "Caption has 3 columns"))

    # Column widths check.
    EXPECTED_WIDTHS = (4320, 360, 4680)
    WIDTH_TOLERANCE = 50
    tbl_grid = find1(caption, "w:tblGrid")
    if tbl_grid is not None:
        grid_cols = find(tbl_grid, "w:gridCol")
        actual_widths = [int(get_attr(g, "w") or "0") for g in grid_cols]
        if len(actual_widths) == 3 and all(
            abs(actual_widths[i] - EXPECTED_WIDTHS[i]) <= WIDTH_TOLERANCE
            for i in range(3)
        ):
            results.append(CheckResult("BC_CAPTION", "PASS",
                          f"Column widths {actual_widths} match locked spec (4320/360/4680)"))
        else:
            results.append(CheckResult("BC_CAPTION", "FAIL",
                        f"Column widths {actual_widths} -- expected (4320, 720, 4320). "
                        "The earlier BC widths (4320/720/4320 and 4320/360/4410) are retired."))

    # Equal paragraph counts across all three columns (wrap-handling rule).
    para_counts = [len(find(c, "w:p")) for c in cells]
    if para_counts[0] == para_counts[1] == para_counts[2]:
        results.append(CheckResult("BC_CAPTION", "PASS",
                      f"All three columns have {para_counts[0]} paragraphs (equal counts required)"))
    else:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    f"Column paragraph counts {para_counts} differ -- wrap-handling rule requires equal "
                    "counts across columns 1, 2, and 3"))

    # Cell borders nil check.
    cells_with_borders = []
    for ci, cell in enumerate(cells):
        tc_pr = find1(cell, "w:tcPr")
        if tc_pr is not None:
            tc_borders = find1(tc_pr, "w:tcBorders")
            if tc_borders is not None:
                non_nil = [b for b in tc_borders if get_attr(b, "val") not in ("nil", "none")]
                if non_nil:
                    cells_with_borders.append(ci + 1)
    if not cells_with_borders:
        results.append(CheckResult("BC_CAPTION", "PASS",
                      "All caption cells have nil borders"))
    else:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    f"Cells {cells_with_borders} have non-nil borders -- all caption cell borders must be nil"))

    # Right column all paragraphs center-aligned.
    right_cell = cells[2]
    right_paras = find(right_cell, "w:p")
    center_count = 0
    total_with_jc = 0
    for p in right_paras:
        ppr = find1(p, "w:pPr")
        if ppr is not None:
            jc = find1(ppr, "w:jc")
            if jc is not None:
                total_with_jc += 1
                if get_attr(jc, "val") == "center":
                    center_count += 1

    if total_with_jc > 0 and center_count == total_with_jc:
        results.append(CheckResult("BC_CAPTION", "PASS",
                      f"Right column: all {center_count} paragraphs are center-aligned"))
    elif center_count > 0:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    f"Right column: {center_count}/{total_with_jc} paragraphs center-aligned -- ALL should be center"))
    else:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    "Right column: no center-aligned paragraphs found -- should be jc=center"))

    # Court name check -- wrap-aware.
    right_text_concat = extract_text(right_cell)
    no_in_prefix = "IN THE BUSINESS COURT" not in right_text_concat
    if right_paras:
        first_para_text = extract_text(right_paras[0]).strip()
        if first_para_text == "THE BUSINESS COURT OF TEXAS":
            if no_in_prefix:
                results.append(CheckResult("BC_CAPTION", "PASS",
                              'Court name "THE BUSINESS COURT OF TEXAS" at top of right column (no wrap)'))
            else:
                results.append(CheckResult("BC_CAPTION", "FAIL",
                            'Court name has "IN" prefix -- should be "THE BUSINESS COURT OF TEXAS"'))
        elif first_para_text == "THE BUSINESS COURT OF":
            second_para_text = extract_text(right_paras[1]).strip() if len(right_paras) > 1 else ""
            if second_para_text == "TEXAS" and no_in_prefix:
                results.append(CheckResult("BC_CAPTION", "PASS",
                              'Court name pre-broken across rows 1-2 ("THE BUSINESS COURT OF" / "TEXAS")'))
            else:
                results.append(CheckResult("BC_CAPTION", "FAIL",
                            f'Right-column row 2 is "{second_para_text}" -- when court name is pre-broken, '
                            'row 2 must be exactly "TEXAS"'))
        else:
            results.append(CheckResult("BC_CAPTION", "FAIL",
                        f'First right-column paragraph is "{first_para_text}" -- must start with '
                        '"THE BUSINESS COURT OF" (with or without wrap to "TEXAS" on row 2)'))

    # Cause number format (YY-BCDDP-NNNN).
    bc_cause_pattern = r'\d{2}-BC\d{2}[A-Z]-\d{4}'
    if re.search(bc_cause_pattern, right_text_concat):
        results.append(CheckResult("BC_CAPTION", "PASS", "Cause number matches BC format (YY-BCDDP-NNNN)"))
    else:
        results.append(CheckResult("BC_CAPTION", "WARN",
                    "Cause number does not match standard BC format (YY-BCDDP-NNNN)"))

    # Caption paragraph line spacing (must be single).
    caption_double_spaced = False
    for cell in cells:
        for p in find(cell, "w:p"):
            ppr = find1(p, "w:pPr")
            if ppr is not None:
                spacing = find1(ppr, "w:spacing")
                if spacing is not None:
                    line_val = get_attr(spacing, "line")
                    if line_val and int(line_val) > 240:
                        caption_double_spaced = True

    if not caption_double_spaced:
        results.append(CheckResult("BC_CAPTION", "PASS",
                      "Caption paragraphs are single-spaced (line <= 240)"))
    else:
        results.append(CheckResult("BC_CAPTION", "FAIL",
                    "Caption paragraphs have double spacing -- must be explicitly single-spaced (line=240)"))

    return results


def check_bc_sig_block(files):
    """Check BC sig block: firm name bold+smallCaps, ATTORNEYS FOR bold."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")

    firm_name_correct = False
    attorneys_for_correct = False

    for para in paragraphs:
        text = extract_text(para)
        if _has_firm_token(text) or "ATTORNEYS" in text.upper():
            runs = find(para, "w:r")
            for run in runs:
                run_text = ""
                for t_elem in find(run, "w:t"):
                    if t_elem.text:
                        run_text += t_elem.text

                if _has_firm_token(run_text):
                    rpr = find1(run, "w:rPr")
                    if rpr is not None:
                        bold = find1(rpr, "w:b")
                        small_caps = find1(rpr, "w:smallCaps")
                        if bold is not None and small_caps is not None:
                            firm_name_correct = True

                if "ATTORNEYS FOR" in run_text.upper():
                    rpr = find1(run, "w:rPr")
                    if rpr is not None:
                        bold = find1(rpr, "w:b")
                        if bold is not None:
                            attorneys_for_correct = True

    if firm_name_correct:
        results.append(CheckResult("BC_SIG_BLOCK", "PASS", "Firm name is bold+smallCaps"))
    else:
        results.append(CheckResult("BC_SIG_BLOCK", "WARN", "Firm name is not bold+smallCaps (should be for BC)"))

    if attorneys_for_correct:
        results.append(CheckResult("BC_SIG_BLOCK", "PASS", "'ATTORNEYS FOR' is bold"))
    else:
        results.append(CheckResult("BC_SIG_BLOCK", "WARN", "'ATTORNEYS FOR' is not bold (should be for BC)"))

    return results


def run_business_checks(doc_path):
    """Run Business Court-specific checks."""
    from validate_core import parse_docx

    files = parse_docx(doc_path)
    results = []

    results.extend(check_bc_font_size(files))
    results.extend(check_bc_heading_spacing(files))
    results.extend(check_bc_heading1_center(files))
    results.extend(check_bc_caption(files))
    results.extend(check_bc_sig_block(files))

    return results
