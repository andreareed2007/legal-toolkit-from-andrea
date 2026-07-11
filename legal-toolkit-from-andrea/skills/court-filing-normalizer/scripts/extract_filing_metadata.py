#!/usr/bin/env python3
"""
extract_filing_metadata.py — Scan a folder of court filing PDFs and extract
docket number, filing date, and document title from each file.

Usage:
    python extract_filing_metadata.py "/path/to/folder"

Outputs JSON to stdout. Errors/warnings go to stderr.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Filename pattern regexes (extensible — add new court systems here)
# ---------------------------------------------------------------------------

PATTERNS = {
    # NYSCEF: {index}_{year}_{PARTY_v_PARTY}__{DOC_TYPE}_{docket}.pdf
    # Optionally prefixed with [docket#]
    "nyscef": re.compile(
        r"^(?:\[(\d+)\]\s*)?"          # optional [docket#] prefix
        r"(\d+)_(\d{4})_"              # index_number, year
        r"[A-Z0-9_]+_v_[A-Z0-9_]+__"  # party_v_party
        r"([A-Z_]+?)_"                 # doc_type (truncated)
        r"(\d+)\.pdf$",               # trailing docket number
        re.IGNORECASE
    ),
    # ECF: ECF {zero-padded docket#} {short title}.pdf
    "ecf": re.compile(
        r"^ECF\s+0*(\d+)\s+(.+)\.pdf$",
        re.IGNORECASE
    ),
    # Raw PACER: {docket#}.pdf or {docket#}-{attachment}.pdf
    "pacer_raw": re.compile(
        r"^(\d+)(?:-(\d+))?\.pdf$"
    ),
    # PACER gov format: gov.uscourts.{dist}.{case}.{docket}.{attach}.pdf
    "pacer_gov": re.compile(
        r"^gov\.uscourts\.\w+\.\d+\.(\d+)\.(\d+)\.pdf$"
    ),
    # eFileTexas: varies widely, try cause_number patterns
    "efiletexas": re.compile(
        r"^(?:DC|CV|CC)[-_]?\d{2}[-_]\d+.*\.pdf$",
        re.IGNORECASE
    ),
}

# Already-normalized pattern — skip these files
NORMALIZED_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2} \[\d+\] .+\.pdf$")

# ---------------------------------------------------------------------------
# Date extraction regexes (from PDF content)
# ---------------------------------------------------------------------------

DATE_PATTERNS = [
    # 1. NYSCEF RECEIVED stamp
    (re.compile(r"RECEIVED NYSCEF[:\s]*(\d{2}/\d{2}/\d{4})"), "%m/%d/%Y", "nyscef_received"),
    # 2. ECF header stamp: Filed MM/DD/YYYY or MM/DD/YY
    (re.compile(r"Filed\s+(\d{2}/\d{2}/\d{4})"), "%m/%d/%Y", "ecf_filed_4yr"),
    (re.compile(r"Filed\s+(\d{2}/\d{2}/\d{2})\b"), "%m/%d/%y", "ecf_filed_2yr"),
    # 3. PACER docket text
    (re.compile(r"Document Filed:\s*(\d{2}/\d{2}/\d{4})"), "%m/%d/%Y", "pacer_doc_filed"),
    # 4. eFileTexas stamp
    (re.compile(r"FILED\s*[:\s]*(\d{1,2}/\d{1,2}/\d{4})"), "%m/%d/%Y", "efiletexas_filed"),
    (re.compile(r"Filed:\s*(\d{1,2}/\d{1,2}/\d{4})"), "%m/%d/%Y", "efiletexas_filed2"),
    (re.compile(r"FILED\s+(\w+ \d{1,2},?\s*\d{4})"), None, "efiletexas_filed_text"),
    # 5. Generic "Filed" with date
    (re.compile(r"(?:FILED|Filing Date)[:\s]*(\d{1,2}/\d{1,2}/\d{4})"), "%m/%d/%Y", "generic_filed"),
]

# ---------------------------------------------------------------------------
# Title extraction helpers
# ---------------------------------------------------------------------------

# Affirmation of service: extract party served
AOS_PARTY_RE = re.compile(
    r"Party served:\s*(.+?)(?:\s*b\.\s*Person served|$)",
    re.IGNORECASE | re.DOTALL
)

# Corporate suffixes to preserve in title case
PRESERVE_SUFFIXES = {
    "llc", "lp", "llp", "inc", "inc.", "ltd", "ltd.", "corp", "corp.",
    "plc", "na", "n.a.", "pllc", "pc", "p.c.", "co", "co.",
}

# Characters not allowed in Windows filenames
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


def try_read_pdf(filepath):
    """Try to extract text from a PDF. Returns (text, method) or (None, None)."""
    text = None
    method = None

    # Try pymupdf first
    try:
        import fitz
        doc = fitz.open(filepath)
        pages = []
        for i in range(min(3, len(doc))):
            pages.append(doc[i].get_text())
        doc.close()
        text = "\n".join(pages)
        method = "pymupdf"
    except Exception:
        pass

    # Try pypdf as fallback
    if not text:
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            pages = []
            for i in range(min(3, len(reader.pages))):
                page_text = reader.pages[i].extract_text()
                if page_text:
                    pages.append(page_text)
            if pages:
                text = "\n".join(pages)
                method = "pypdf"
        except Exception:
            pass

    return text, method


def extract_date_from_text(text):
    """Extract filing date from PDF text. Returns (date_str YYYY-MM-DD, source) or (None, None)."""
    for pattern, fmt, source in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            date_str = m.group(1).strip()
            try:
                if fmt:
                    dt = datetime.strptime(date_str, fmt)
                else:
                    # Try common text date formats
                    for tfmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"]:
                        try:
                            dt = datetime.strptime(date_str, tfmt)
                            break
                        except ValueError:
                            continue
                    else:
                        continue
                return dt.strftime("%Y-%m-%d"), source
            except ValueError:
                continue
    return None, None


def extract_docket_from_filename(filename):
    """Try to extract docket number from filename patterns. Returns int or None."""
    basename = filename

    # Check for [docket#] prefix
    prefix_m = re.match(r"^\[(\d+)\]", basename)
    if prefix_m:
        return int(prefix_m.group(1))

    # NYSCEF pattern
    m = PATTERNS["nyscef"].match(basename)
    if m:
        # group(1) is optional prefix docket, group(5) is trailing docket
        return int(m.group(1) or m.group(5))

    # ECF pattern
    m = PATTERNS["ecf"].match(basename)
    if m:
        return int(m.group(1))

    # Raw PACER
    m = PATTERNS["pacer_raw"].match(basename)
    if m:
        return int(m.group(1))

    # PACER gov format
    m = PATTERNS["pacer_gov"].match(basename)
    if m:
        return int(m.group(1))

    return None


def extract_docket_from_text(text):
    """Try to extract docket number from PDF content."""
    # ECF header: Document N
    m = re.search(r"Document\s+(\d+)\s+Filed", text)
    if m:
        return int(m.group(1))

    # NYSCEF: Doc. No. or Document No.
    m = re.search(r"Doc(?:ument)?\.?\s*No\.?\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))

    return None


def smart_title_case(s):
    """Title case that preserves corporate suffixes."""
    words = s.split()
    result = []
    for w in words:
        if w.lower().rstrip(".,") in PRESERVE_SUFFIXES:
            # Preserve the suffix as-is (uppercase common forms)
            clean = w.rstrip(".,")
            trail = w[len(clean):]
            result.append(clean.upper() + trail)
        else:
            result.append(w.capitalize() if w.islower() or w.isupper() else w)
    return " ".join(result)


def clean_entity_name(name):
    """Clean up an entity name for use in a filename."""
    name = name.strip()
    # Replace f/k/a and n/k/a
    name = re.sub(r"\bf/k/a\b", "fka", name, flags=re.IGNORECASE)
    name = re.sub(r"\bn/k/a\b", "nka", name, flags=re.IGNORECASE)
    # Remove invalid filename characters
    name = INVALID_FILENAME_CHARS.sub("", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    # Smart title case
    name = smart_title_case(name)
    return name


def extract_title_from_text(text, filename):
    """Extract document title from PDF text. Returns (title, source, confidence)."""

    # 1. Affirmation/Affidavit of Service — parse party served
    if re.search(r"affirmation of service|affidavit of service", text, re.IGNORECASE):
        m = AOS_PARTY_RE.search(text)
        if m:
            party = clean_entity_name(m.group(1))
            if party and len(party) > 2:
                return f"Affirmation of Service - {party}", "pdf_content", "high"
        return "Affirmation of Service", "pdf_content", "medium"

    # 2. Look for common document headings in first ~2000 chars
    # These are typically centered, uppercase or title-case headings
    first_chunk = text[:3000]

    heading_patterns = [
        # Motions
        (r"((?:DEFENDANT'?S?|PLAINTIFF'?S?)\s+MOTION\s+.+?)(?:\n|$)", "motion"),
        (r"(MOTION\s+(?:TO|FOR)\s+.+?)(?:\n|$)", "motion"),
        # Briefs and memoranda
        (r"((?:MEMORANDUM|BRIEF)\s+(?:IN\s+)?(?:SUPPORT|OPPOSITION|REPLY).+?)(?:\n|$)", "brief"),
        # Complaints, answers, petitions
        (r"((?:FIRST\s+|SECOND\s+|THIRD\s+)?AMENDED\s+(?:COMPLAINT|PETITION|ANSWER).+?)(?:\n|$)", "pleading"),
        (r"\b(VERIFIED\s+(?:COMPLAINT|PETITION))\b", "pleading"),
        (r"\b(COMPLAINT)\b", "pleading"),
        (r"\b(ANSWER(?:\s+AND\s+COUNTERCLAIM)?)\b", "pleading"),
        # Orders and judgments
        (r"(ORDER\s+.+?)(?:\n|$)", "order"),
        (r"\b(JUDGMENT)\b", "order"),
        # Declarations and affidavits
        (r"((?:DECLARATION|AFFIDAVIT)\s+OF\s+.+?)(?:\n|$)", "declaration"),
        # Summons
        (r"\b(SUMMONS)\b", "summons"),
        # Subpoena
        (r"\b(SUBPOENA\s*(?:DUCES\s+TECUM)?)\b", "subpoena"),
        # Notice
        (r"(NOTICE\s+OF\s+.+?)(?:\n|$)", "notice"),
        # Request for Judicial Intervention
        (r"(REQUEST FOR JUDICIAL INTERVENTION)", "rji"),
        # Commercial Division Addendum
        (r"(COMMERCIAL DIVISION ADDENDUM)", "cd_addendum"),
        # Exhibits
        (r"(EXHIBIT\s+[A-Z0-9]+)", "exhibit"),
    ]

    for pat, _ in heading_patterns:
        m = re.search(pat, first_chunk, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            # Clean up: title case, trim trailing whitespace/punct
            title = re.sub(r"\s+", " ", title)
            title = title.rstrip(".")
            title = smart_title_case(title)
            return title, "pdf_content", "medium"

    return None, None, None


def extract_title_from_filename(filename):
    """Fallback: extract title from filename patterns."""
    basename = os.path.splitext(filename)[0]

    # ECF pattern: strip "ECF XXXX " prefix
    m = PATTERNS["ecf"].match(filename)
    if m:
        title = m.group(2).strip()
        title = smart_title_case(title)
        return title, "filename", "medium"

    # NYSCEF pattern: try to use the doc_type segment
    m = PATTERNS["nyscef"].match(filename)
    if m:
        doc_type = m.group(4)
        if doc_type:
            # Convert underscores to spaces, title case
            title = doc_type.replace("_", " ").strip()
            title = smart_title_case(title)
            return title, "filename", "low"  # low confidence — NYSCEF truncates

    return None, "unknown", None


def process_file(filepath):
    """Process a single PDF file. Returns a metadata dict."""
    filename = os.path.basename(filepath)
    result = {
        "filename": filename,
        "filepath": filepath,
        "docket_number": None,
        "filing_date": None,
        "title": None,
        "date_source": "unknown",
        "docket_source": "unknown",
        "title_source": "unknown",
        "confidence": "low",
        "flags": [],
        "pdf_readable": False,
    }

    # Skip already-normalized files
    if NORMALIZED_RE.match(filename):
        result["flags"].append("already_normalized")
        result["confidence"] = "skip"
        return result

    # Skip non-PDF files
    if not filename.lower().endswith(".pdf"):
        result["flags"].append("not_pdf")
        result["confidence"] = "skip"
        return result

    # --- Extract docket number from filename first ---
    docket_from_fn = extract_docket_from_filename(filename)
    if docket_from_fn is not None:
        result["docket_number"] = docket_from_fn
        result["docket_source"] = "filename"

    # --- Try to read PDF content ---
    text, read_method = try_read_pdf(filepath)
    if text and len(text.strip()) > 50:
        result["pdf_readable"] = True

        # Date from content
        date, date_source = extract_date_from_text(text)
        if date:
            result["filing_date"] = date
            result["date_source"] = date_source

        # Docket from content (override filename if found)
        docket_from_text = extract_docket_from_text(text)
        if docket_from_text is not None:
            result["docket_number"] = docket_from_text
            result["docket_source"] = "pdf_content"

        # Title from content
        title, title_source, title_conf = extract_title_from_text(text, filename)
        if title:
            result["title"] = title
            result["title_source"] = title_source
    else:
        result["flags"].append("unreadable_pdf")
        if text is None:
            result["flags"].append(f"read_failed")

    # --- Fallback: title from filename if not found in content ---
    if not result["title"]:
        fn_title, fn_source, fn_conf = extract_title_from_filename(filename)
        if fn_title:
            result["title"] = fn_title
            result["title_source"] = fn_source

    # --- Flag missing fields ---
    if result["filing_date"] is None:
        result["flags"].append("date_missing")
    if result["docket_number"] is None:
        result["flags"].append("docket_missing")
    if result["title"] is None:
        result["flags"].append("title_ambiguous")

    # --- Compute overall confidence ---
    missing = sum(1 for f in result["flags"] if f in ("date_missing", "docket_missing", "title_ambiguous"))
    if missing == 0 and result["pdf_readable"]:
        result["confidence"] = "high"
    elif missing <= 1:
        result["confidence"] = "medium"
    else:
        result["confidence"] = "low"

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_filing_metadata.py <folder_path>", file=sys.stderr)
        sys.exit(1)

    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print(f"Error: '{folder}' is not a directory", file=sys.stderr)
        sys.exit(1)

    # List all files in the folder (not recursive)
    all_files = sorted(os.listdir(folder))
    pdf_files = [f for f in all_files if f.lower().endswith(".pdf")]
    non_pdf = [f for f in all_files if not f.lower().endswith(".pdf")]

    results = []
    skipped_normalized = 0
    skipped_non_pdf = len(non_pdf)

    for f in pdf_files:
        filepath = os.path.join(folder, f)
        meta = process_file(filepath)
        if "already_normalized" in meta.get("flags", []):
            skipped_normalized += 1
        results.append(meta)

    output = {
        "folder": folder,
        "total_files": len(all_files),
        "total_pdfs": len(pdf_files),
        "skipped_normalized": skipped_normalized,
        "skipped_non_pdf": skipped_non_pdf,
        "non_pdf_files": non_pdf if non_pdf else [],
        "files": results,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
