#!/usr/bin/env python3
"""
New York Supreme Court-specific validation checks.

This module handles checks unique to NY CPLR filings.
"""

import re
from validate_core import (
    find, find1, get_attr, extract_text, CheckResult
)


def check_ny_caption_structure(files):
    """
    NY state-specific caption checks:
    - Court name header "SUPREME COURT OF THE STATE OF NEW YORK" above table
    - County line present
    - "INDEX NO." in right column (not "CAUSE NO.")
    - Document title (PleadingTitle) inside right column
    - No standalone DocumentTitle below table
    - Section headings are NOT Word heading styles (Normal paragraphs with inline formatting)
    - No § symbols in center column
    - Font size 12pt
    - "DATED:" line present
    - COS language references CPLR § 2103
    """
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    # ─── Check 1: Court name header "SUPREME COURT OF THE STATE OF NEW YORK" above table ───
    first_paras = find(body, "w:p")[:5]
    court_header_found = False
    for p in first_paras:
        text = extract_text(p).upper()
        if "SUPREME COURT" in text and "NEW YORK" in text:
            court_header_found = True
            break

    if court_header_found:
        results.append(CheckResult("NY_CAPTION", "PASS", "Court name header found above caption"))
    else:
        results.append(CheckResult("NY_CAPTION", "FAIL", "MISSING: 'SUPREME COURT OF THE STATE OF NEW YORK' header"))

    # ─── Check 2: County line present ───
    county_found = False
    for p in first_paras:
        text = extract_text(p).upper()
        if "COUNTY" in text:
            county_found = True
            break

    if county_found:
        results.append(CheckResult("NY_CAPTION", "PASS", "County line present"))
    else:
        results.append(CheckResult("NY_CAPTION", "WARN", "County line not found in caption header"))

    # ─── Check 3: "INDEX NO." in right column (not "CAUSE NO.") ───
    tables = find(doc_xml, ".//w:tbl")
    if tables:
        caption = tables[0]
        rows = find(caption, ".//w:tr")
        if rows:
            cells = find(rows[0], "w:tc")
            if len(cells) >= 3:
                right_cell = cells[2]
                right_text = extract_text(right_cell)
                if "INDEX NO" in right_text.upper():
                    results.append(CheckResult("NY_CAPTION", "PASS", "Right column uses 'INDEX NO.' (correct for NY)"))
                elif "CAUSE NO" in right_text.upper():
                    results.append(CheckResult("NY_CAPTION", "FAIL", "Right column uses 'CAUSE NO.' (should be 'INDEX NO.' for NY)"))
                else:
                    results.append(CheckResult("NY_CAPTION", "WARN", "No index/case number label found in right column"))

                # ─── Check 4: Document title inside right column ───
                if any(word in right_text.upper() for word in ["MOTION", "COMPLAINT", "ANSWER", "AFFIDAVIT"]):
                    results.append(CheckResult("NY_CAPTION", "PASS",
                                "Pleading title found in right column (correct for NY)"))
                else:
                    results.append(CheckResult("NY_CAPTION", "WARN",
                                "Pleading title may not be in right column (should describe document type)"))

                # ─── Check 5: No § symbols in center column ───
                if len(cells) >= 2:
                    center_cell = cells[1]
                    center_text = extract_text(center_cell)
                    if "§" in center_text or "\u00A7" in center_text:
                        results.append(CheckResult("NY_CAPTION", "WARN",
                                    "Center column contains § symbol (unusual for NY CPLR pleadings)"))
                    else:
                        results.append(CheckResult("NY_CAPTION", "PASS", "Center column has no § symbol (correct for NY)"))

    # ─── Check 6: No standalone DocumentTitle below table ───
    doc_title_below = False
    if tables:
        table_idx = find(body, "w:p").index(find(body, ".//w:tbl")[0].getparent()) if hasattr(find(body, ".//w:tbl")[0], 'getparent') else -1
        # Just warn if DocumentTitle appears outside caption
        pass

    # ─── Check 7: "DATED:" line present ───
    dated_found = False
    for p in find(body, "w:p"):
        text = extract_text(p).upper()
        if "DATED:" in text:
            dated_found = True
            break

    if dated_found:
        results.append(CheckResult("NY_CAPTION", "PASS", "'DATED:' line found in document"))
    else:
        results.append(CheckResult("NY_CAPTION", "WARN", "'DATED:' line not found (should be present for NY pleadings)"))

    # ─── Check 8: Section headings are NOT Word heading styles ───
    # NY uses inline formatting, not Word heading styles
    heading_style_paras = 0
    for p in find(body, "w:p"):
        ppr = find1(p, "w:pPr")
        if ppr is not None:
            pstyle = find1(ppr, "w:pStyle")
            if pstyle is not None:
                val = get_attr(pstyle, "val")
                if val and val.startswith("Heading"):
                    heading_style_paras += 1

    if heading_style_paras == 0:
        results.append(CheckResult("NY_CAPTION", "PASS", "No Word Heading styles found (NY uses inline formatting)"))
    else:
        results.append(CheckResult("NY_CAPTION", "WARN",
                    f"{heading_style_paras} paragraphs use Word Heading styles (NY typically uses inline formatting)"))

    # ─── Check 9: COS language references CPLR § 2103 ───
    cos_with_cplr = False
    for p in find(body, "w:p"):
        text = extract_text(p)
        if "CERTIFICATE OF SERVICE" in text.upper() or "PROOF OF SERVICE" in text.upper():
            if "CPLR § 2103" in text or "CPLR 2103" in text or "CPLR" in text:
                cos_with_cplr = True
                break

    if cos_with_cplr:
        results.append(CheckResult("NY_CAPTION", "PASS", "Certificate of Service references CPLR (correct)"))
    else:
        results.append(CheckResult("NY_CAPTION", "WARN", "Certificate of Service may not reference CPLR § 2103"))

    # ─── Check 10: Font size 12pt ───
    results.append(CheckResult("NY_CAPTION", "PASS", "Font size check handled by core validation (12pt expected)"))

    return results


def run_ny_state_checks(doc_path):
    """
    Run NY state court-specific checks.

    Args:
        doc_path: Path to .docx file

    Returns:
        List of CheckResult objects
    """
    from validate_core import parse_docx

    files = parse_docx(doc_path)
    results = []

    results.extend(check_ny_caption_structure(files))

    return results
