#!/usr/bin/env python3
"""
AAA Arbitration-specific validation checks for court filings.

Checks:
1. Forum header contains "BEFORE THE AMERICAN ARBITRATION ASSOCIATION"
2. Caption table exists (1 row x 3 cols)
3. Case number cell contains "AAA Case No."
4. No Certificate of Conference heading present (warn if found)

Usage:
    Called by validate_court_filing.py when --court-type aaa-arb
"""
import os
import zipfile
import tempfile
from lxml import etree

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


class CheckResult:
    def __init__(self, check, status, message):
        self.check = check
        self.status = status
        self.message = message


def _get_all_text(element):
    """Extract all text from a w:p or w:tc element."""
    texts = element.findall('.//w:t', NS)
    return ''.join((t.text or '') for t in texts).strip()


def _parse_docx(docx_path):
    """Unpack docx and parse document.xml."""
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(docx_path, 'r') as z:
        z.extractall(tmpdir)
    doc_path = os.path.join(tmpdir, 'word', 'document.xml')
    tree = etree.parse(doc_path)
    return tree, tmpdir


def run_aaa_arb_checks(docx_path):
    """Run AAA arbitration-specific validation checks."""
    results = []
    tree, tmpdir = _parse_docx(docx_path)
    root = tree.getroot()
    body = root.find('.//w:body', NS)

    if body is None:
        results.append(CheckResult('AAA_BODY', 'FAIL', 'No document body found'))
        return results

    paragraphs = body.findall('w:p', NS)
    tables = body.findall('w:tbl', NS)

    # ── Check 1: Forum header ──────────────────────────────────────────
    found_header = False
    for p in paragraphs:
        text = _get_all_text(p).upper()
        if 'BEFORE THE AMERICAN ARBITRATION ASSOCIATION' in text:
            found_header = True
            # Check it's centered
            jc = p.find('.//w:jc', NS)
            if jc is not None and jc.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val') == 'center':
                results.append(CheckResult('AAA_HEADER', 'PASS',
                    'Forum header found and centered'))
            else:
                results.append(CheckResult('AAA_HEADER', 'WARN',
                    'Forum header found but may not be centered'))
            break

    if not found_header:
        results.append(CheckResult('AAA_HEADER', 'FAIL',
            'Forum header "BEFORE THE AMERICAN ARBITRATION ASSOCIATION" not found'))

    # ── Check 2: Caption table exists ──────────────────────────────────
    if len(tables) == 0:
        results.append(CheckResult('AAA_CAPTION_TABLE', 'FAIL',
            'No tables found in document'))
    else:
        caption_table = tables[0]
        rows = caption_table.findall('w:tr', NS)
        if len(rows) == 1:
            cells = rows[0].findall('w:tc', NS)
            if len(cells) == 3:
                results.append(CheckResult('AAA_CAPTION_TABLE', 'PASS',
                    'Caption table: 1 row x 3 columns'))
            else:
                results.append(CheckResult('AAA_CAPTION_TABLE', 'FAIL',
                    f'Caption table has {len(cells)} columns, expected 3'))
        else:
            results.append(CheckResult('AAA_CAPTION_TABLE', 'WARN',
                f'First table has {len(rows)} rows, expected 1 for caption'))

    # ── Check 3: AAA Case No. in column 3 ─────────────────────────────
    if len(tables) > 0:
        caption_table = tables[0]
        rows = caption_table.findall('w:tr', NS)
        if rows:
            cells = rows[0].findall('w:tc', NS)
            if len(cells) >= 3:
                col3_text = _get_all_text(cells[2]).upper()
                if 'AAA CASE NO' in col3_text:
                    results.append(CheckResult('AAA_CASE_NUMBER', 'PASS',
                        'AAA Case No. found in column 3'))
                else:
                    results.append(CheckResult('AAA_CASE_NUMBER', 'FAIL',
                        f'Column 3 does not contain "AAA Case No." — found: "{_get_all_text(cells[2])[:60]}"'))

    # ── Check 4: No Certificate of Conference ──────────────────────────
    found_conf = False
    for p in paragraphs:
        text = _get_all_text(p).upper()
        if 'CERTIFICATE OF CONFERENCE' in text:
            found_conf = True
            break

    if found_conf:
        results.append(CheckResult('AAA_NO_CONF', 'WARN',
            'Certificate of Conference found — not expected in AAA arbitration filings'))
    else:
        results.append(CheckResult('AAA_NO_CONF', 'PASS',
            'No Certificate of Conference (correct for AAA arbitration)'))

    # ── Check 5: Party labels use arbitration terms ────────────────────
    if len(tables) > 0:
        caption_table = tables[0]
        rows = caption_table.findall('w:tr', NS)
        if rows:
            cells = rows[0].findall('w:tc', NS)
            if cells:
                col1_text = _get_all_text(cells[0]).upper()
                has_court_terms = 'PLAINTIFF' in col1_text or 'DEFENDANT' in col1_text
                has_arb_terms = 'CLAIMANT' in col1_text or 'RESPONDENT' in col1_text
                if has_court_terms and not has_arb_terms:
                    results.append(CheckResult('AAA_PARTY_LABELS', 'WARN',
                        'Caption uses court terms (Plaintiff/Defendant) instead of arbitration terms (Claimant/Respondent)'))
                elif has_arb_terms:
                    results.append(CheckResult('AAA_PARTY_LABELS', 'PASS',
                        'Caption uses arbitration party labels (Claimant/Respondent)'))
                else:
                    results.append(CheckResult('AAA_PARTY_LABELS', 'WARN',
                        'Could not identify party labels in caption column 1'))

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

    return results


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python validate_aaa_arb.py <path_to_docx>")
        sys.exit(1)
    results = run_aaa_arb_checks(sys.argv[1])
    for r in results:
        icon = {"PASS": "OK", "FAIL": "X", "WARN": "!"}[r.status]
        print(f"[{icon}] {r.status}: {r.check} — {r.message}")
