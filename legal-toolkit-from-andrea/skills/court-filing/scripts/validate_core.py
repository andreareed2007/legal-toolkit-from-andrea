#!/usr/bin/env python3
"""
Core validation checks shared across all court types.

These checks apply to state, federal, NY, and Business Court filings.
Court-type-specific modules import this and call run_core_checks().
"""

import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

# ====== CONSTANTS ======
NSMAP = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}

# Register namespaces so XPath works
for prefix, uri in NSMAP.items():
    ET.register_namespace(prefix, uri)

# Known attorneys for /s/ validation -- loaded from the user profile
# (~/.legal-skills/config.json, written by the environment-setup skill).
# Falls back to an empty list when no profile exists; the /s/ name check is
# WARN-only, so an empty list never fails a document -- it just skips the check.
try:
    from config_helper import attorney_names
    KNOWN_ATTORNEYS = attorney_names()
except Exception:
    KNOWN_ATTORNEYS = []


def find(element, xpath):
    """Find elements using namespace-aware XPath."""
    return element.findall(xpath, NSMAP)


def find1(element, xpath):
    """Find first element using namespace-aware XPath."""
    return element.find(xpath, NSMAP)


def get_attr(element, attr_name):
    """Get attribute with w: namespace prefix."""
    return element.get(f"{{{NSMAP['w']}}}{attr_name}")


def extract_text(paragraph):
    """Extract all text content from a paragraph."""
    texts = []
    for t_elem in find(paragraph, ".//w:t"):
        if t_elem.text:
            texts.append(t_elem.text)
    return "".join(texts)


def parse_docx(docx_path):
    """Unpack docx and parse key XML files."""
    files = {}
    with zipfile.ZipFile(docx_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                files[name] = ET.fromstring(zf.read(name))
    return files


class CheckResult:
    """Represents a single check result."""
    def __init__(self, check, status, message):
        self.check = check
        self.status = status  # PASS, FAIL, WARN
        self.message = message

    def __repr__(self):
        return f"<CheckResult {self.check} {self.status}: {self.message}>"


# ====== CORE CHECK FUNCTIONS ======

def check_styles(files, require_heading_styles=True):
    """Check heading style definitions in styles.xml."""
    results = []
    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "styles.xml not found in docx"))
        return results

    heading_styles = {}
    for style in find(styles_xml, ".//w:style"):
        style_id = get_attr(style, "styleId")
        if style_id and style_id.startswith("Heading"):
            heading_styles[style_id] = style

    if not require_heading_styles:
        # Skip heading style checks for NY which uses inline formatting
        results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading style checks skipped (NY inline formatting mode)"))
        return results

    # Check Heading1 exists
    if "Heading1" in heading_styles:
        results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading1 style defined in styles.xml"))
        h1 = heading_styles["Heading1"]

        # Check keepNext
        ppr = find1(h1, ".//w:pPr")
        if ppr is not None:
            keep_next = find1(ppr, "w:keepNext")
            if keep_next is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading1 has keepNext"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading1 MISSING keepNext"))

            keep_lines = find1(ppr, "w:keepLines")
            if keep_lines is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading1 has keepLines"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading1 MISSING keepLines"))

            # Check contextualSpacing = false
            ctx = find1(ppr, "w:contextualSpacing")
            if ctx is not None:
                val = get_attr(ctx, "val")
                if val in ("0", "false"):
                    results.append(CheckResult("P13_SPACING", "PASS", "Heading1 contextualSpacing=false"))
                else:
                    results.append(CheckResult("P13_SPACING", "FAIL", f"Heading1 contextualSpacing={val} (should be false/0)"))
            else:
                results.append(CheckResult("P13_SPACING", "WARN", "Heading1 contextualSpacing not explicitly set (inherits default)"))

            # Check spacing: line=240, after=240 (12pt -- per Global Prefs P13)
            spacing = find1(ppr, "w:spacing")
            if spacing is not None:
                line_val = get_attr(spacing, "line")
                after_val = get_attr(spacing, "after")
                if line_val == "240":
                    results.append(CheckResult("P13_SPACING", "PASS", "Heading1 single-spaced (line=240)"))
                else:
                    results.append(CheckResult("P13_SPACING", "FAIL", f"Heading1 line={line_val} (should be 240)"))
                if after_val == "240":
                    results.append(CheckResult("P13_SPACING", "PASS", "Heading1 spaceAfter=240 (12pt)"))
                else:
                    results.append(CheckResult("P13_SPACING", "FAIL", f"Heading1 spaceAfter={after_val} (should be 240 / 12pt)"))

            # Check outlineLevel
            outline = find1(ppr, "w:outlineLevel")
            if outline is None:
                outline = find1(ppr, "w:outlineLvl")
            if outline is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading1 has outlineLevel"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "WARN", "Heading1 outlineLevel not found in style pPr"))

        # Check run properties for small caps
        rpr = find1(h1, ".//w:rPr")
        if rpr is not None:
            small_caps = find1(rpr, "w:smallCaps")
            if small_caps is not None:
                results.append(CheckResult("P4_SMALL_CAPS", "PASS", "Heading1 style has smallCaps in run properties"))
            else:
                results.append(CheckResult("P4_SMALL_CAPS", "FAIL", "Heading1 style MISSING smallCaps in run properties"))

            bold = find1(rpr, "w:b")
            if bold is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading1 style has bold"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "WARN", "Heading1 style missing bold in run properties"))

            # Check font
            fonts = find1(rpr, "w:rFonts")
            if fonts is not None:
                ascii_font = get_attr(fonts, "ascii")
                if ascii_font and "Century Schoolbook" in ascii_font:
                    results.append(CheckResult("P7_TYPOGRAPHY", "PASS", "Heading1 font is Century Schoolbook"))
                else:
                    results.append(CheckResult("P7_TYPOGRAPHY", "FAIL", f"Heading1 font is '{ascii_font}' (should be Century Schoolbook)"))
    else:
        results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading1 style NOT FOUND in styles.xml"))

    # Check Heading2 exists
    if "Heading2" in heading_styles:
        results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading2 style defined in styles.xml"))
        h2 = heading_styles["Heading2"]

        ppr = find1(h2, ".//w:pPr")
        if ppr is not None:
            keep_next = find1(ppr, "w:keepNext")
            keep_lines = find1(ppr, "w:keepLines")
            if keep_next is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading2 has keepNext"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading2 MISSING keepNext"))
            if keep_lines is not None:
                results.append(CheckResult("P10_HEADING_STYLES", "PASS", "Heading2 has keepLines"))
            else:
                results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading2 MISSING keepLines"))

        rpr = find1(h2, ".//w:rPr")
        if rpr is not None:
            small_caps = find1(rpr, "w:smallCaps")
            if small_caps is not None:
                sc_val = get_attr(small_caps, "val")
                if sc_val in ("0", "false", None):
                    if sc_val is None:
                        results.append(CheckResult("P11_HEADING_HIERARCHY", "FAIL",
                                        "Heading2 style has smallCaps ON (should be off - sentence case only)"))
                    else:
                        results.append(CheckResult("P11_HEADING_HIERARCHY", "PASS",
                                        "Heading2 style does not use smallCaps (correct)"))
                else:
                    results.append(CheckResult("P11_HEADING_HIERARCHY", "FAIL",
                                    f"Heading2 style has smallCaps={sc_val} (should be off)"))
            else:
                results.append(CheckResult("P11_HEADING_HIERARCHY", "PASS",
                                "Heading2 style does not use smallCaps (correct)"))
    else:
        results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "Heading2 style NOT FOUND in styles.xml"))

    return results


def check_document_font(files, font_size=24):
    """Check default document font. font_size=24 for 12pt, 28 for 14pt (BC)."""
    results = []
    styles_xml = files.get("word/styles.xml")
    if styles_xml is None:
        return results

    # Check default run properties
    doc_defaults = find1(styles_xml, ".//w:docDefaults")
    if doc_defaults is not None:
        rpr_default = find1(doc_defaults, ".//w:rPrDefault//w:rPr")
        if rpr_default is not None:
            fonts = find1(rpr_default, "w:rFonts")
            if fonts is not None:
                ascii_font = get_attr(fonts, "ascii")
                if ascii_font and "Century Schoolbook" in ascii_font:
                    results.append(CheckResult("P7_TYPOGRAPHY", "PASS", f"Default document font: {ascii_font}"))
                else:
                    results.append(CheckResult("P7_TYPOGRAPHY", "FAIL",
                                    f"Default document font is '{ascii_font}' (should be Century Schoolbook)"))
            else:
                results.append(CheckResult("P7_TYPOGRAPHY", "WARN", "No rFonts in document defaults"))

            sz = find1(rpr_default, "w:sz")
            if sz is not None:
                val = get_attr(sz, "val")
                if val == str(font_size):
                    pt = int(font_size) // 2
                    results.append(CheckResult("P7_TYPOGRAPHY", "PASS", f"Default font size: {pt}pt ({val} half-pts)"))
                else:
                    actual_pt = int(val) // 2
                    expected_pt = font_size // 2
                    results.append(CheckResult("P7_TYPOGRAPHY", "FAIL",
                                    f"Default font size is {actual_pt}pt ({val} half-pts) - should be {expected_pt}pt ({font_size})"))

    return results


def check_heading_numbering(files):
    """Check that headings use auto-numbering, not manually typed numbers."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        results.append(CheckResult("P11_HEADING_NUMBERING", "FAIL", "document.xml not found"))
        return results

    heading_paras = []
    for para in find(doc_xml, ".//w:p"):
        ppr = find1(para, "w:pPr")
        if ppr is None:
            continue
        pstyle = find1(ppr, "w:pStyle")
        if pstyle is None:
            continue
        style_val = get_attr(pstyle, "val")
        if style_val and style_val.startswith("Heading"):
            heading_paras.append((style_val, para))

    if not heading_paras:
        results.append(CheckResult("P10_HEADING_STYLES", "FAIL", "No paragraphs with Heading styles found in document"))
        return results

    results.append(CheckResult("P10_HEADING_STYLES", "PASS", f"Found {len(heading_paras)} paragraphs with Heading styles"))

    # Check each heading for numPr (auto-numbering) vs manually typed numbers
    manually_numbered = 0
    auto_numbered = 0
    for style_id, para in heading_paras:
        ppr = find1(para, "w:pPr")
        num_pr = find1(ppr, "w:numPr") if ppr is not None else None
        text = extract_text(para)

        if num_pr is not None:
            auto_numbered += 1
        else:
            # Check if text starts with a manual number pattern
            manual_patterns = [
                r"^[IVXLC]+\.\s",    # Roman numerals: I. II. III.
                r"^[A-Z]\.\s",        # Capital letters: A. B. C.
                r"^\d+\.\s",          # Arabic numerals: 1. 2. 3.
                r"^\([ivxlc]+\)\s",   # Lowercase roman in parens: (i) (ii)
            ]
            is_manual = any(re.match(p, text) for p in manual_patterns)
            if is_manual:
                manually_numbered += 1
                results.append(CheckResult("P11_HEADING_NUMBERING", "FAIL",
                                f"{style_id}: \"{text[:50]}...\" has MANUALLY TYPED number (no numPr)"))
            else:
                results.append(CheckResult("P11_HEADING_NUMBERING", "WARN",
                                f"{style_id}: \"{text[:50]}\" has no numbering (may be intentional)"))

    if auto_numbered > 0:
        results.append(CheckResult("P11_HEADING_NUMBERING", "PASS",
                         f"{auto_numbered} headings use auto-numbering via numPr"))
    if manually_numbered > 0:
        results.append(CheckResult("P11_HEADING_NUMBERING", "FAIL",
                         f"{manually_numbered} headings have MANUALLY TYPED numbers (must use numbering config)"))

    return results


def check_numbering_config(files, expected_spacing="double"):
    """Check numbering.xml for proper ListParagraph and heading numbering configs."""
    results = []
    numbering_xml = files.get("word/numbering.xml")
    if numbering_xml is None:
        results.append(CheckResult("P14_LIST_PARAGRAPH", "FAIL", "numbering.xml not found - no numbering definitions"))
        return results

    # Paragraph numbering list lookup
    para_num_lists = {}
    for absnum in find(numbering_xml, ".//w:abstractNum"):
        absnum_id = get_attr(absnum, "abstractNumId")
        if absnum_id:
            para_num_lists[absnum_id] = absnum

    # Check ListParagraph styles
    num_lists = find(numbering_xml, ".//w:num")
    for num in num_lists:
        num_id = get_attr(num, "numId")
        absnum_id_elem = find1(num, "w:abstractNumId")
        if absnum_id_elem is None:
            continue
        absnum_id = get_attr(absnum_id_elem, "val")
        if absnum_id not in para_num_lists:
            continue

        absnum = para_num_lists[absnum_id]
        # Check level 0 (body list)
        for lvl in find(absnum, "w:lvl"):
            ilvl = get_attr(lvl, "ilvl")
            if ilvl != "0":
                continue

            # --- CHECK: left=900, hanging=360 (body list) ---
            ppr = find1(lvl, ".//w:pPr")
            if ppr is not None:
                ind = find1(ppr, "w:ind")
                if ind is not None:
                    left = get_attr(ind, "left") or "0"
                    hanging = get_attr(ind, "hanging") or "0"
                    if left == "900" and hanging == "360":
                        results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS",
                                        f"Numbering ID {num_id}: left=900, hanging=360 (correct)"))
                    else:
                        results.append(CheckResult("P14_LIST_PARAGRAPH", "FAIL",
                                        f"Numbering ID {num_id}: left={left}, hanging={hanging} (should be 900/360)"))

            # Check pSuffix = tab
            psfx = find1(lvl, "w:pSuffix")
            if psfx is not None:
                psfx_val = get_attr(psfx, "val")
                if psfx_val == "tab":
                    results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS",
                                    f"Numbering ID {num_id}: pSuffix=tab (correct)"))
                else:
                    results.append(CheckResult("P14_LIST_PARAGRAPH", "FAIL",
                                    f"Numbering ID {num_id}: pSuffix={psfx_val} (should be tab)"))
            else:
                results.append(CheckResult("P14_LIST_PARAGRAPH", "WARN",
                                f"Numbering ID {num_id}: pSuffix not found"))

            # Check tabStop = 900
            tabs = find(ppr, "w:tabs/w:tab") if ppr is not None else []
            tab_val_found = False
            for tab in tabs:
                tab_pos = get_attr(tab, "val")
                if tab_pos == "900":
                    tab_val_found = True
                    break
            if tab_val_found:
                results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS",
                                f"Numbering ID {num_id}: tabStop=900 (correct)"))
            else:
                results.append(CheckResult("P14_LIST_PARAGRAPH", "WARN",
                                f"Numbering ID {num_id}: tabStop=900 not found"))

            # Check run properties: bold=false, italics=false
            rpr = find1(lvl, ".//w:rPr")
            if rpr is not None:
                bold = find1(rpr, "w:b")
                italics = find1(rpr, "w:i")
                if bold is None and italics is None:
                    results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS",
                                    f"Numbering ID {num_id}: number run has bold=false, italics=false"))
                else:
                    issues = []
                    if bold is not None:
                        issues.append("bold=true")
                    if italics is not None:
                        issues.append("italics=true")
                    results.append(CheckResult("P14_LIST_PARAGRAPH", "FAIL",
                                    f"Numbering ID {num_id}: number run has {', '.join(issues)} (should be false)"))

    return results


def check_empty_paragraphs(files):
    """Check for empty spacer paragraphs outside signature block."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")

    # Find signature block start
    sig_start_idx = None
    for i, para in enumerate(paragraphs):
        text = extract_text(para)
        if "Respectfully submitted" in text or "CERTIFICATE OF" in text.upper():
            sig_start_idx = i
            break

    if sig_start_idx is None:
        sig_start_idx = len(paragraphs)  # No sig block, check all

    empty_count = 0
    for i in range(sig_start_idx):
        para = paragraphs[i]
        text = extract_text(para).strip()
        if text == "":
            empty_count += 1

    if empty_count == 0:
        results.append(CheckResult("GEN_EMPTY_PARAS", "PASS", "No empty spacer paragraphs found outside signature block"))
    else:
        results.append(CheckResult("GEN_EMPTY_PARAS", "FAIL",
                        f"{empty_count} empty paragraphs found in body (before signature)"))

    return results


def check_body_spacing(files, expected_spacing="double"):
    """Check body text line spacing matches --spacing parameter.

    Carve-outs: the following are ALWAYS single-spaced (line=240) regardless
    of the document's body spacing setting, and must not be flagged:
    - Heading styles (Heading1, Heading2, Heading3)
    - DocumentTitle style
    - Paragraphs inside tables (caption table, signature block container)
    - Certificate headings, bodies, and signatures (detected by position
      after "CERTIFICATE" heading text)
    - Footer paragraphs (in header/footer, not in body -- already excluded)
    """
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    expected_line = "480" if expected_spacing == "double" else "240"
    expected_name = "double-spaced" if expected_spacing == "double" else "single-spaced"

    # Exempt styles that are always single-spaced
    EXEMPT_STYLES = {"Heading1", "Heading2", "Heading3", "DocumentTitle"}

    # Walk body children to detect table paragraphs and certificate zone
    in_certificates = False
    spacing_correct = 0
    spacing_wrong = 0
    skipped_exempt = 0

    for child in body:
        tag = child.tag.split("}")[-1]

        # Skip all paragraphs inside tables (caption, sig block)
        if tag == "tbl":
            continue

        if tag != "p":
            continue

        para = child
        ppr = find1(para, "w:pPr")

        # Check if paragraph has an exempt style
        if ppr is not None:
            pstyle = find1(ppr, "w:pStyle")
            if pstyle is not None:
                style_val = get_attr(pstyle, "val")
                if style_val in EXEMPT_STYLES:
                    skipped_exempt += 1
                    continue

        # Detect certificate zone FIRST: any paragraph whose text contains "CERTIFICATE"
        para_text = extract_text(para).upper()
        if "CERTIFICATE" in para_text:
            in_certificates = True

        # Skip all certificate-zone paragraphs (they are always single-spaced)
        if in_certificates:
            skipped_exempt += 1
            continue

        # Exempt document title: centered + bold + underline + single-spaced
        # (may not have DocumentTitle style if built with inline formatting)
        if ppr is not None:
            jc = find1(ppr, "w:jc")
            if jc is not None and get_attr(jc, "val") == "center":
                runs = find(para, ".//w:r")
                if runs:
                    rpr = find1(runs[0], "w:rPr")
                    if rpr is not None:
                        has_bold = find1(rpr, "w:b") is not None
                        has_underline = find1(rpr, "w:u") is not None
                        if has_bold and has_underline:
                            skipped_exempt += 1
                            continue

        if ppr is None:
            continue
        spacing = find1(ppr, "w:spacing")
        if spacing is None:
            continue

        line_val = get_attr(spacing, "line")
        if line_val and line_val == expected_line:
            spacing_correct += 1
        elif line_val and line_val == "240" and expected_spacing == "double":
            spacing_wrong += 1
        elif line_val and line_val == "480" and expected_spacing == "single":
            spacing_wrong += 1

    if spacing_correct > 0 and spacing_wrong == 0:
        results.append(CheckResult("P13_SPACING", "PASS",
                        f"Body text is {expected_name} (line={expected_line}); "
                        f"{skipped_exempt} exempt paragraphs skipped"))
    elif spacing_wrong > 0:
        results.append(CheckResult("P13_SPACING", "FAIL",
                        f"{spacing_wrong} paragraphs have wrong spacing (expected {expected_name}); "
                        f"{skipped_exempt} exempt paragraphs skipped"))

    return results


def check_double_spaces(files):
    """Check for double spaces after periods (should be one space)."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")
    double_space_count = 0
    for para in paragraphs:
        text = extract_text(para)
        if ".  " in text:  # Two spaces after period
            double_space_count += 1

    if double_space_count == 0:
        results.append(CheckResult("GEN_SPACING", "PASS", "No double spaces after periods"))
    else:
        results.append(CheckResult("GEN_SPACING", "FAIL",
                        f"{double_space_count} paragraphs have double spaces after periods (should be single space)"))

    return results


def check_footer(files):
    """Check footer formatting: 10pt, Century Schoolbook, small caps + bold title, left-aligned."""
    results = []
    # Footer is typically in the main document or a separate footer part
    footer_xml = None
    for key in files.keys():
        if "footer" in key.lower() and key.endswith(".xml"):
            footer_xml = files[key]
            break

    if footer_xml is None:
        results.append(CheckResult("FOOTER", "WARN", "No footer found in document"))
        return results

    # Check footer paragraphs
    footer_paras = find(footer_xml, ".//w:p")
    if not footer_paras:
        results.append(CheckResult("FOOTER", "WARN", "Footer has no paragraphs"))
        return results

    # First footer paragraph should have title text
    first_para = footer_paras[0]
    first_text = extract_text(first_para)

    # Check alignment = left
    ppr = find1(first_para, "w:pPr")
    if ppr is not None:
        jc = find1(ppr, "w:jc")
        if jc is not None:
            jc_val = get_attr(jc, "val")
            if jc_val in ("left", "start", None):
                results.append(CheckResult("FOOTER", "PASS", "Footer is left-aligned"))
            else:
                results.append(CheckResult("FOOTER", "FAIL", f"Footer alignment is '{jc_val}' (should be left)"))
        else:
            results.append(CheckResult("FOOTER", "PASS", "Footer is left-aligned (default)"))

    # Check for "[Title] - Page X" format
    if re.search(r"-\s*Page\s+\d+", first_text):
        results.append(CheckResult("FOOTER", "PASS", f"Footer format: \"{first_text}\""))
    else:
        results.append(CheckResult("FOOTER", "WARN",
                        f"Footer format may be wrong: \"{first_text}\" (expected \"[Title] - Page X\")"))

    # Check font size = 10pt in runs
    runs = find(first_para, ".//w:r")
    for run in runs:
        rpr = find1(run, "w:rPr")
        if rpr is not None:
            sz = find1(rpr, "w:sz")
            if sz is not None:
                sz_val = get_attr(sz, "val")
                if sz_val == "20":  # 10pt = 20 half-pts
                    results.append(CheckResult("FOOTER", "PASS", "Footer font size: 10pt (20 half-pts)"))
                else:
                    results.append(CheckResult("FOOTER", "WARN",
                                    f"Footer font size: {int(sz_val)//2}pt ({sz_val} half-pts) (expected 10pt)"))

    return results


def check_caption_table(files):
    """Check that a caption table exists with proper structure."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    tables = find(doc_xml, ".//w:tbl")
    if not tables:
        results.append(CheckResult("CAPTION", "FAIL", "No tables found in document (caption should be a table)"))
        return results

    # Check first table (should be caption)
    caption_table = tables[0]
    rows = find(caption_table, ".//w:tr")

    # ── CRITICAL: Caption MUST be exactly 1 row ──
    if len(rows) == 1:
        results.append(CheckResult("CAPTION", "PASS", "Caption table has exactly 1 row (correct)"))
    else:
        results.append(CheckResult("CAPTION", "FAIL",
                         f"Caption table has {len(rows)} rows -- MUST be exactly 1 row "
                         f"with multiple paragraphs per cell"))

    # ── CRITICAL: Table-level borders MUST be none ──
    tbl_pr = find1(caption_table, "w:tblPr")
    if tbl_pr is not None:
        tbl_borders = find1(tbl_pr, "w:tblBorders")
        if tbl_borders is not None:
            bad_borders = []
            for border_name in ["top", "left", "bottom", "right", "insideH", "insideV"]:
                border_el = find1(tbl_borders, f"w:{border_name}")
                if border_el is not None:
                    val = get_attr(border_el, "val")
                    if val and val != "none" and val != "nil":
                        bad_borders.append(f"{border_name}={val}")
            if bad_borders:
                results.append(CheckResult("CAPTION", "FAIL",
                            f"Table-level borders are NOT none: {', '.join(bad_borders)}"))
            else:
                results.append(CheckResult("CAPTION", "PASS", "Table-level borders are all none (correct)"))
        else:
            results.append(CheckResult("CAPTION", "WARN",
                        "No table-level borders element found -- borders may inherit Word defaults"))
    else:
        results.append(CheckResult("CAPTION", "WARN", "No table properties found on caption table"))

    # Check for 3 columns (left party, §, right court info)
    if rows:
        first_row = rows[0]
        cells = find(first_row, "w:tc")
        if len(cells) == 3:
            results.append(CheckResult("CAPTION", "PASS", "Caption table has 3 columns"))
        else:
            results.append(CheckResult("CAPTION", "FAIL",
                        f"Caption table has {len(cells)} columns (should be 3)"))

        # Check that § symbol is present
        all_text = ""
        for row in rows:
            for cell in find(row, "w:tc"):
                all_text += extract_text(cell) + " "

        if "\u00A7" in all_text or "§" in all_text:
            results.append(CheckResult("CAPTION", "PASS", "Caption contains § symbol"))
        else:
            results.append(CheckResult("CAPTION", "FAIL", "Caption MISSING § symbol in center column"))

        # Check for cause/docket number
        body = find1(doc_xml, ".//w:body")
        first_paras = find(body, "w:p")[:3] if body else []
        found_cause = False
        for p in first_paras:
            text = extract_text(p)
            if any(pattern in text.upper() for pattern in ["CAUSE NO", "CIVIL ACTION NO", "CASE NO", "DOCKET NO", "INDEX NO"]):
                found_cause = True

        # Also check inside the caption table cells
        if not found_cause:
            for row in rows:
                for cell in find(row, "w:tc"):
                    for p in find(cell, "w:p"):
                        text = extract_text(p)
                        if any(pattern in text.upper() for pattern in ["CAUSE NO", "CIVIL ACTION NO", "CASE NO", "DOCKET NO", "INDEX NO"]):
                            found_cause = True
                        if re.search(r"DC-\d{2}-\d+", text) or re.search(r"\d+:\d+-CV-\d+", text, re.IGNORECASE):
                            found_cause = True
                        if re.search(r"\d{3}-\d{2}-\d{5}", text):
                            found_cause = True

        if found_cause:
            results.append(CheckResult("CAPTION", "PASS", "Cause/docket/index number found in caption"))
        else:
            results.append(CheckResult("CAPTION", "FAIL", "Cause/docket/index number not found in caption or above table"))

    return results


def check_page_size(files):
    """Check page size = 8.5x11 inches, margins 1 inch on all sides."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    sect = find1(doc_xml, ".//w:sectPr")
    if sect is None:
        results.append(CheckResult("PAGE_SIZE", "WARN", "No section properties found"))
        return results

    # Check page size (pgSz)
    pgsz = find1(sect, "w:pgSz")
    if pgsz is not None:
        w = get_attr(pgsz, "w") or "0"
        h = get_attr(pgsz, "h") or "0"
        # Standard letter: 8.5" × 11" = 12,240 × 15,840 twips
        if w == "12240" and h == "15840":
            results.append(CheckResult("PAGE_SIZE", "PASS", "Page size: 8.5 x 11 inches (letter)"))
        else:
            results.append(CheckResult("PAGE_SIZE", "WARN",
                            f"Page size: {int(w)/1440:.2f}\" x {int(h)/1440:.2f}\" (expected 8.5 x 11)"))

    # Check margins (pgMar)
    pgmar = find1(sect, "w:pgMar")
    if pgmar is not None:
        top = int(get_attr(pgmar, "top") or "1440")
        bottom = int(get_attr(pgmar, "bottom") or "1440")
        left = int(get_attr(pgmar, "left") or "1440")
        right = int(get_attr(pgmar, "right") or "1440")
        margin_1in = 1440  # 1 inch = 1440 twips
        if all(abs(m - margin_1in) < 100 for m in [top, bottom, left, right]):
            results.append(CheckResult("PAGE_SIZE", "PASS", "Page margins: ~1 inch on all sides"))
        else:
            results.append(CheckResult("PAGE_SIZE", "WARN",
                            f"Margins: T={top//1440}\", B={bottom//1440}\", L={left//1440}\", R={right//1440}\" (expected 1\")"))

    return results


def check_list_paragraphs_in_body(files):
    """Check that all ListParagraph items follow numbering rules."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    list_paras = []
    for para in find(body, "w:p"):
        ppr = find1(para, "w:pPr")
        if ppr is None:
            continue
        pstyle = find1(ppr, "w:pStyle")
        if pstyle is not None:
            style_val = get_attr(pstyle, "val")
            if style_val == "ListParagraph":
                list_paras.append(para)

    if not list_paras:
        results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS", "No ListParagraph items found (or none required)"))
        return results

    results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS", f"Found {len(list_paras)} ListParagraph items"))

    # Check each ListParagraph for correct formatting
    issues = []
    for para in list_paras:
        ppr = find1(para, "w:pPr")
        if ppr is None:
            continue

        ind = find1(ppr, "w:ind")
        if ind is None:
            issues.append("missing indentation")
            continue

        left = get_attr(ind, "left") or "0"
        hanging = get_attr(ind, "hanging") or "0"
        if left != "900" or hanging != "360":
            issues.append(f"indent: left={left} (expect 900), hanging={hanging} (expect 360)")

    if not issues:
        results.append(CheckResult("P14_LIST_PARAGRAPH", "PASS",
                        f"All {len(list_paras)} ListParagraph items have correct indentation"))
    else:
        results.append(CheckResult("P14_LIST_PARAGRAPH", "WARN",
                        f"{len(issues)} ListParagraph formatting issue(s): {'; '.join(issues[:3])}"))

    return results


def check_signature_block(files):
    """Check main signature block formatting."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        results.append(CheckResult("SIG_BLOCK", "FAIL", "document.xml not found"))
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        results.append(CheckResult("SIG_BLOCK", "FAIL", "No body element found"))
        return results

    paragraphs = find(body, "w:p")

    # Check 1: "Respectfully submitted," exists
    found_respectfully = False
    for para in paragraphs:
        text = extract_text(para)
        if "Respectfully submitted" in text:
            found_respectfully = True
            break

    if found_respectfully:
        results.append(CheckResult("SIG_BLOCK", "PASS", '"Respectfully submitted," paragraph found'))
    else:
        results.append(CheckResult("SIG_BLOCK", "FAIL", '"Respectfully submitted," paragraph NOT FOUND'))

    # Check 2: "By:" signature line uses underline+tab technique
    found_by_line = False
    by_line_correct = False
    for para in paragraphs:
        text = extract_text(para)
        if text.strip().startswith("By:") and "/s/" in text:
            found_by_line = True
            runs = find(para, "w:r")
            has_underline_slash_s = False
            has_underline_tab = False

            for run in runs:
                rpr = find1(run, "w:rPr")
                has_underline = False
                if rpr is not None:
                    u_elem = find1(rpr, "w:u")
                    if u_elem is not None:
                        u_val = get_attr(u_elem, "val")
                        if u_val == "single":
                            has_underline = True

                run_text = ""
                for t_elem in find(run, "w:t"):
                    if t_elem.text:
                        run_text += t_elem.text
                if "/s/" in run_text and has_underline:
                    has_underline_slash_s = True

                tab_elem = find1(run, "w:tab")
                if tab_elem is not None and has_underline:
                    has_underline_tab = True

            if has_underline_slash_s and has_underline_tab:
                by_line_correct = True
                results.append(CheckResult("SIG_BLOCK", "PASS", '"By:" line uses correct underline+tab technique'))
            else:
                issues = []
                if not has_underline_slash_s:
                    issues.append("/s/ text missing underline")
                if not has_underline_tab:
                    issues.append("tab character missing underline")
                results.append(CheckResult("SIG_BLOCK", "FAIL",
                            f'"By:" line has wrong formatting: {"; ".join(issues)}'))
            break

    if not found_by_line:
        results.append(CheckResult("SIG_BLOCK", "WARN", '"By: /s/" line not found (may be intentional for non-motion filings)'))

    return results


def check_certificate_signatures(files):
    """Check that certificate signatures use the single-paragraph underline+tab technique."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")
    in_certificates = False
    cert_sig_count = 0
    cert_sig_correct = 0
    cert_sig_issues = []

    for i, para in enumerate(paragraphs):
        text = extract_text(para)

        # Detect certificate area
        if "CERTIFICATE OF" in text.upper() and len(text.strip()) < 40:
            in_certificates = True
            continue

        if not in_certificates:
            continue

        # Look for /s/ paragraphs in certificate area
        if "/s/" not in text:
            continue

        cert_sig_count += 1
        runs = find(para, "w:r")

        has_slash_s_underline = False
        has_name_italic_underline = False
        has_tab_underline = False
        has_line_break = False
        has_plain_name = False
        has_underscores = "___" in text

        past_break = False
        for run in runs:
            rpr = find1(run, "w:rPr")
            has_underline = False
            has_italic = False
            if rpr is not None:
                u_elem = find1(rpr, "w:u")
                if u_elem is not None:
                    u_val = get_attr(u_elem, "val")
                    if u_val == "single":
                        has_underline = True
                i_elem = find1(rpr, "w:i")
                if i_elem is not None:
                    i_val = get_attr(i_elem, "val")
                    if i_val is None or (i_val not in ("0", "false")):
                        has_italic = True

            run_text = ""
            for t_elem in find(run, "w:t"):
                if t_elem.text:
                    run_text += t_elem.text

            tab_elem = find1(run, "w:tab")
            br_elem = find1(run, "w:br")

            if "/s/" in run_text and has_underline:
                has_slash_s_underline = True

            if has_italic and has_underline and run_text and "/s/" not in run_text and not past_break:
                has_name_italic_underline = True

            if tab_elem is not None and has_underline:
                has_tab_underline = True

            if br_elem is not None:
                has_line_break = True
                past_break = True

            if past_break and run_text.strip() and not has_underline and not has_italic:
                has_plain_name = True

        # Determine if this signature is correctly formatted
        issues = []
        if not has_slash_s_underline:
            issues.append("/s/ missing underline")
        if not has_name_italic_underline:
            issues.append("name missing italic+underline")
        if not has_tab_underline:
            issues.append("no underlined tab")
        if not has_line_break:
            issues.append("no <w:br/> (not single-paragraph)")
        if not has_plain_name:
            issues.append("no plain printed name after break")
        if has_underscores:
            issues.append("UNDERSCORE CHARACTERS (___) detected")

        if not issues:
            cert_sig_correct += 1
        else:
            cert_sig_issues.append((text[:50], issues))

    # Report results
    if cert_sig_count == 0:
        results.append(CheckResult("SIG_CERTIFICATES", "WARN", "No certificate signatures found (may be intentional)"))
    else:
        if cert_sig_correct == cert_sig_count:
            results.append(CheckResult("SIG_CERTIFICATES", "PASS",
                             f"All {cert_sig_count} certificate signature(s) use correct underline+tab technique"))
        else:
            bad = cert_sig_count - cert_sig_correct
            results.append(CheckResult("SIG_CERTIFICATES", "FAIL",
                             f"{bad} of {cert_sig_count} certificate signature(s) have formatting issues"))
            for text_snip, issues in cert_sig_issues:
                results.append(CheckResult("SIG_CERTIFICATES", "FAIL",
                            f'  "{text_snip}": {"; ".join(issues)}'))

    return results


def check_no_underscore_lines(files):
    """Check that there are no underscore character lines (___) in end matter."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")
    underscore_paras = 0
    for para in paragraphs:
        text = extract_text(para)
        if re.match(r"^_+$", text.strip()):
            underscore_paras += 1

    if underscore_paras == 0:
        results.append(CheckResult("SIG_NO_UNDERSCORE", "PASS", "No underscore character lines (_____) found"))
    else:
        results.append(CheckResult("SIG_NO_UNDERSCORE", "FAIL",
                        f"{underscore_paras} underscore character line(s) found (use underline+tab instead)"))

    return results


def check_known_attorney_names(files):
    """Check that /s/ names match known attorneys."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")
    unknown_names = []
    known_count = 0

    for para in paragraphs:
        text = extract_text(para)
        # Extract text between /s/ and first line break or end
        match = re.search(r"/s/\s*(.+?)(?:\n|$)", text)
        if match:
            name = match.group(1).strip()
            found_match = False
            for known_name in KNOWN_ATTORNEYS:
                if name.lower() == known_name.lower() or known_name.lower() in name.lower():
                    known_count += 1
                    found_match = True
                    break
            if not found_match and name:
                unknown_names.append(name)

    if unknown_names:
        results.append(CheckResult("SIG_KNOWN_ATTORNEYS", "WARN",
                        f"Unknown attorney name(s): {', '.join(unknown_names[:3])} (known: {', '.join(KNOWN_ATTORNEYS)})"))
    elif known_count > 0:
        results.append(CheckResult("SIG_KNOWN_ATTORNEYS", "PASS",
                        f"All {known_count} signature(s) use known attorney names"))
    else:
        results.append(CheckResult("SIG_KNOWN_ATTORNEYS", "WARN", "No /s/ signatures found to verify"))

    return results


def check_cert_heading_matches_content(files):
    """Check that certificate heading text (e.g., 'CERTIFICATE OF SERVICE') matches body content."""
    results = []
    doc_xml = files.get("word/document.xml")
    if doc_xml is None:
        return results

    body = find1(doc_xml, ".//w:body")
    if body is None:
        return results

    paragraphs = find(body, "w:p")

    # Find certificate heading and body
    cert_heading = None
    cert_body = None
    for i, para in enumerate(paragraphs):
        text = extract_text(para).strip()
        if "CERTIFICATE OF" in text.upper():
            cert_heading = text
            if i + 1 < len(paragraphs):
                cert_body = extract_text(paragraphs[i + 1])
            break

    if cert_heading is None:
        results.append(CheckResult("SIG_CERT_HEADING", "WARN", "No certificate heading found"))
        return results

    # Extract heading type (e.g., "SERVICE", "COUNSEL")
    heading_type = cert_heading.replace("CERTIFICATE OF", "").strip()

    # Check if body mentions the same type
    if heading_type and cert_body:
        if heading_type.upper() in cert_body.upper():
            results.append(CheckResult("SIG_CERT_HEADING", "PASS",
                            f"Certificate heading matches body: '{heading_type}'"))
        else:
            results.append(CheckResult("SIG_CERT_HEADING", "WARN",
                            f"Certificate heading '{heading_type}' may not match body text"))
    else:
        results.append(CheckResult("SIG_CERT_HEADING", "PASS", "Certificate heading text checked"))

    return results


def run_core_checks(doc_path, spacing="double", font_size=24, require_heading_styles=True):
    """
    Run all core checks shared across court types.

    Args:
        doc_path: Path to .docx file
        spacing: "single" or "double"
        font_size: 24 for 12pt (standard), 28 for 14pt (BC)
        require_heading_styles: True for Word heading styles, False to skip (NY)

    Returns:
        List of CheckResult objects
    """
    files = parse_docx(doc_path)
    results = []

    # Core checks
    results.extend(check_page_size(files))
    results.extend(check_document_font(files, font_size=font_size))
    results.extend(check_styles(files, require_heading_styles=require_heading_styles))
    results.extend(check_heading_numbering(files))
    results.extend(check_numbering_config(files, spacing))
    results.extend(check_body_spacing(files, spacing))
    results.extend(check_empty_paragraphs(files))
    results.extend(check_double_spaces(files))
    results.extend(check_caption_table(files))
    results.extend(check_footer(files))
    results.extend(check_list_paragraphs_in_body(files))

    # Signature checks
    results.extend(check_signature_block(files))
    results.extend(check_certificate_signatures(files))
    results.extend(check_no_underscore_lines(files))
    results.extend(check_known_attorney_names(files))
    results.extend(check_cert_heading_matches_content(files))

    return results
