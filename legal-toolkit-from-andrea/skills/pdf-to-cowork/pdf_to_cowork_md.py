#!/usr/bin/env python3
"""
pdf_to_cowork_md.py — Cowork Edition (v2)
------------------------------------------
Converts PDF(s) to Markdown (.md) or plain-text (.txt) files that Cowork
can read completely and search.

HYBRID ARCHITECTURE:
  - pdftotext -layout  → raw text extraction (gold standard for text quality)
  - pymupdf (PyMuPDF)  → font metadata overlay (headings, bold, italic detection)
  - pdfplumber / pypdf  → fallback text extraction if pdftotext unavailable

DOCUMENT-TYPE ROUTING:
  - Depositions (monospaced, line numbers, Q/A markers) → .txt (pdftotext -layout)
  - Two-column detected → .txt (pdftotext -layout)
  - Everything else (briefs, motions, orders, contracts) → .md (Markdown with
    structural headings, inline emphasis, caption blockquotes)

Validation:
  - Page count match (extracted vs. PDF total)
  - Content threshold per page (flags thin/empty pages)
  - Sequential page marker integrity
  - Gap classification: IMAGE-ONLY, PARTIAL, EXTRACTION FAILED
  - CONTENT GAPS block at top of every output file
  - MANIFEST.md in the _cowork_txt/ output folder

Usage:
  python3 pdf_to_cowork_md.py /path/to/file.pdf
  python3 pdf_to_cowork_md.py /path/to/file.pdf --format txt
  python3 pdf_to_cowork_md.py /path/to/file.pdf --format auto
  python3 pdf_to_cowork_md.py /path/to/directory/          # batch mode
  python3 pdf_to_cowork_md.py /path/to/directory/ --skip-existing
  python3 pdf_to_cowork_md.py /path/to/file.pdf --method pdfplumber
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def _win_path(p: Path) -> Path:
    """Return a Path prefixed with \\?\ on Windows to bypass the 260-char MAX_PATH limit."""
    if sys.platform != "win32":
        return p
    s = str(p.resolve())
    if s.startswith("\\\\?\\"):
        return Path(s)
    return Path("\\\\?\\" + s)


# ── Constants ─────────────────────────────────────────────────────────────────

CONTENT_THRESHOLD_CHARS = 20   # Pages with fewer chars flagged as gaps
SEPARATOR = "=" * 70
HEADER_FOOTER_ZONE_PX = 60    # Top/bottom zone for header/footer detection
HEADER_FOOTER_THRESHOLD = 0.40  # 40% of pages must match to count as repeating
COLUMN_MIDPOINT_RATIO = 0.45   # Content clusters left of this = left column
MIN_HEADING_LEN = 4            # Lines shorter than this never become headings
DROPCAP_SIZE_RATIO = 0.75      # Small-cap letter must be <= this ratio of the initial cap

# ── Native-text sidecar (DISCO productions) ───────────────────────────────────

NATIVE_TXT_MIN_CHARS = 20  # sidecars shorter than this are treated as empty


def find_native_txt_sidecar(pdf_path: Path):
    """Return (path, text) of a sibling native-text sidecar, or (None, None).

    DISCO exports letter-perfect native text (from the original file, not OCR of
    the scanned image) as `<stem>.txt`, usually in a `Text/` subfolder. When one
    exists we prefer it over any PDF extraction.
    """
    if os.environ.get("COWORK_IGNORE_SIDECAR"):
        return None, None
    stem = pdf_path.stem
    for cand in (pdf_path.parent / "Text" / f"{stem}.txt",
                 pdf_path.parent / f"{stem}.txt"):
        try:
            if _win_path(cand).exists():
                txt = _win_path(cand).read_text(encoding="utf-8", errors="replace")
                if len(txt.strip()) >= NATIVE_TXT_MIN_CHARS:
                    return cand, txt
        except Exception:
            pass
    return None, None


_CORRESP_RE = re.compile(
    r"^\s*(From|To|Cc|Bcc|Subject|Sent|Date|Importance|Inline-Images|Re|Fwd)\b\s*:?",
    re.IGNORECASE,
)


def _is_correspondence_header(text: str) -> bool:
    """True for email/letter header lines (From:/To:/Subject:/…) which must
    never be rendered as Markdown headings."""
    return bool(_CORRESP_RE.match(text.strip()))


def structure_plaintext(text: str) -> str:
    """Text-only structuring entry point used by the audit harness' do-no-harm
    test. With no font geometry available we impose NO headings or bold — the
    invariant is 'no geometry ⇒ no imposed structure'. Returns text unchanged."""
    return text


def _build_native_validation(true_page_count: int) -> dict:
    return {
        "true_page_count": true_page_count,
        "extracted_count": true_page_count,
        "full_pages": true_page_count,
        "total_affected": 0,
        "completeness_pct": 100.0,
        "gaps": [],
        "missing_pages": [],
        "is_complete": True,
    }


def build_native_header(pdf_path: Path, sidecar_path: Path, true_page_count: int,
                        output_format: str) -> str:
    format_label = "Markdown (.md)" if output_format == "md" else "Plain text (.txt)"
    lines = [
        SEPARATOR,
        f"FILE:              {pdf_path.name}",
        f"SOURCE:            {pdf_path}",
        f"EXTRACTION METHOD: DISCO native text sidecar ({sidecar_path.name})",
        f"OUTPUT FORMAT:     {format_label}",
        f"TOTAL PAGES:       {true_page_count}",
        f"CONVERTED:         {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        SEPARATOR,
        "",
        SEPARATOR,
        "CONTENT GAPS:  None — verbatim native text (not OCR of the scanned image)",
        SEPARATOR,
        "",
        SEPARATOR,
        "NOTE: Body is the producing party's native extracted text, copied verbatim.",
        "      Bates number and CONFIDENTIAL designation are burned into the PDF",
        "      image and are NOT in this native text (the Bates is in the filename).",
        SEPARATOR,
        "",
    ]
    return "\n".join(lines)


# ── Document Type Detection ──────────────────────────────────────────────────

def detect_document_type(pdf_path: Path) -> str:
    """
    Analyze PDF with pymupdf to classify document type.
    Returns: "deposition", "two-column", or "general" (= use Markdown).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        # If pymupdf not available, default to general
        return "general"

    doc = fitz.open(str(_win_path(pdf_path)))
    if doc.page_count == 0:
        doc.close()
        return "general"

    # Sample up to 10 pages for analysis
    sample_pages = min(doc.page_count, 10)
    sample_indices = list(range(sample_pages))

    # Collect font info and content patterns
    font_counter = Counter()
    monospaced_pages = 0
    line_number_pages = 0
    qa_marker_pages = 0
    column_scores = []  # Per-page: fraction of blocks in left half

    monospaced_families = {
        "courier", "consolas", "mono", "monospaced", "arialmonospaced",
        "lucidaconsole", "dejavusansmono", "liberationmono",
    }

    for idx in sample_indices:
        page = doc[idx]
        page_width = page.rect.width
        blocks = page.get_text("dict")["blocks"]

        page_text = ""
        left_count = 0
        right_count = 0

        for block in blocks:
            if block.get("type") != 0:  # text blocks only
                continue

            # Column detection: classify block by x-position
            block_center_x = (block["bbox"][0] + block["bbox"][2]) / 2
            if block_center_x < page_width * COLUMN_MIDPOINT_RATIO:
                left_count += 1
            elif block_center_x > page_width * (1 - COLUMN_MIDPOINT_RATIO):
                right_count += 1

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    page_text += text + " "
                    font_name = span.get("font", "").lower().replace("-", "").replace(" ", "")
                    size = round(span.get("size", 0), 1)
                    is_bold = "bold" in font_name or (span.get("flags", 0) & 16)
                    font_counter[(font_name, size, bool(is_bold))] += len(text)

        # Check for monospaced font dominance
        if font_counter:
            top_font = font_counter.most_common(1)[0][0][0]
            if any(m in top_font for m in monospaced_families):
                monospaced_pages += 1

        # Check for line-number patterns (·1·, ·2·, etc. or bare line numbers)
        if re.search(r'[·\xb7]\s*\d+\s*[·\xb7]', page_text):
            line_number_pages += 1

        # Check for Q/A markers
        if re.search(r'\b[QA]\s+', page_text) or re.search(r'\b[QA]\.\s', page_text):
            qa_marker_pages += 1

        # Column balance
        total_blocks = left_count + right_count
        if total_blocks > 0:
            column_scores.append(min(left_count, right_count) / total_blocks)

    doc.close()

    # Decision logic

    # Two-column: if most sampled pages have balanced left/right content
    if column_scores:
        avg_balance = sum(column_scores) / len(column_scores)
        balanced_pages = sum(1 for s in column_scores if s > 0.3)
        if balanced_pages >= len(column_scores) * 0.5 and avg_balance > 0.25:
            return "two-column"

    # Deposition: monospaced + line numbers + Q/A markers
    mono_ratio = monospaced_pages / sample_pages
    ln_ratio = line_number_pages / sample_pages
    qa_ratio = qa_marker_pages / sample_pages

    if mono_ratio >= 0.5 and (ln_ratio >= 0.3 or qa_ratio >= 0.5):
        return "deposition"

    return "general"


# ── Font Metadata Extraction (pymupdf) ───────────────────────────────────────

def extract_font_metadata(pdf_path: Path) -> dict:
    """
    Extract per-page font metadata from PDF using pymupdf.
    Returns dict with:
      - body_font: (name, size) of the most common font
      - pages: list of page metadata (lines with font info)
    """
    try:
        import fitz
    except ImportError:
        return None

    doc = fitz.open(str(_win_path(pdf_path)))
    page_width = doc[0].rect.width if doc.page_count > 0 else 612
    page_height = doc[0].rect.height if doc.page_count > 0 else 792

    # First pass: determine body font/size
    font_counter = Counter()
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    font_name = span.get("font", "")
                    size = round(span.get("size", 0), 1)
                    font_counter[(font_name, size)] += len(text)

    if not font_counter:
        doc.close()
        return None

    body_font, body_size = font_counter.most_common(1)[0][0]

    # Second pass: extract structured line data per page
    pages_meta = []

    for page_idx, page in enumerate(doc):
        blocks = page.get_text("dict")["blocks"]
        page_lines = []

        for block in blocks:
            if block.get("type") != 0:
                continue

            block_bbox = block["bbox"]  # (x0, y0, x1, y1)

            for line in block.get("lines", []):
                line_bbox = line.get("bbox", block_bbox)
                spans = line.get("spans", [])
                if not spans:
                    continue

                # Join drop-cap small caps (Fix 1)
                joined_spans = _join_dropcap_spans(spans)

                # Group spans sharing the same y-position into visual lines
                line_y = round(line_bbox[1], 1)

                line_data = {
                    "y": line_y,
                    "x0": line_bbox[0],
                    "x1": line_bbox[2],
                    "block_y0": block_bbox[1],
                    "block_y1": block_bbox[3],
                    "spans": joined_spans,
                    "page": page_idx,
                }
                page_lines.append(line_data)

        # Sort lines by y-position, then x-position
        page_lines.sort(key=lambda l: (l["y"], l["x0"]))

        # Merge lines at the same y-position (y-position grouping fix)
        merged_lines = _merge_same_y_lines(page_lines)

        pages_meta.append({
            "lines": merged_lines,
            "width": page.rect.width,
            "height": page.rect.height,
        })

    doc.close()

    return {
        "body_font": body_font,
        "body_size": body_size,
        "page_width": page_width,
        "page_height": page_height,
        "pages": pages_meta,
    }


def _join_dropcap_spans(spans: list) -> list:
    """
    Fix 1: Join drop-cap small caps.
    When a single uppercase letter at size X is followed by uppercase text
    at size Y < X with the same font family and style, join them.
    """
    if len(spans) <= 1:
        return [_normalize_span(s) for s in spans]

    result = []
    i = 0
    while i < len(spans):
        span = spans[i]
        text = span.get("text", "")
        font = span.get("font", "")
        size = span.get("size", 0)
        flags = span.get("flags", 0)

        # Check if this is a single uppercase letter
        if (len(text.strip()) == 1 and text.strip().isupper()
                and i + 1 < len(spans)):
            next_span = spans[i + 1]
            next_text = next_span.get("text", "")
            next_font = next_span.get("font", "")
            next_size = next_span.get("size", 0)
            next_flags = next_span.get("flags", 0)

            # Same font family, next is smaller, both uppercase
            same_family = _same_font_family(font, next_font)
            smaller = next_size <= size * (DROPCAP_SIZE_RATIO + 0.05)
            next_upper = next_text.strip().isupper() if next_text.strip() else False
            same_style = (flags & 16) == (next_flags & 16)  # same bold flag

            if same_family and smaller and next_upper and same_style:
                # Join: "R" + "ESPONDENT " = "RESPONDENT "
                joined_text = text.strip() + next_text
                bbox1 = span.get("bbox", [0, 0, 0, 0])
                bbox2 = next_span.get("bbox", [0, 0, 0, 0])
                result.append({
                    "text": joined_text,
                    "font": font,
                    "size": size,
                    "flags": flags,
                    "is_bold": bool(flags & 16) or "bold" in font.lower(),
                    "is_italic": bool(flags & 2) or "italic" in font.lower(),
                    "x0": bbox1[0],
                    "x1": bbox2[2],
                })
                i += 2
                continue

        result.append(_normalize_span(span))
        i += 1

    return result


def _normalize_span(span: dict) -> dict:
    """Extract the fields we care about from a pymupdf span."""
    font = span.get("font", "")
    flags = span.get("flags", 0)
    bbox = span.get("bbox", [0, 0, 0, 0])
    return {
        "text": span.get("text", ""),
        "font": font,
        "size": span.get("size", 0),
        "flags": flags,
        "is_bold": bool(flags & 16) or "bold" in font.lower(),
        "is_italic": bool(flags & 2) or "italic" in font.lower(),
        "x0": bbox[0],
        "x1": bbox[2],
    }


def _same_font_family(font1: str, font2: str) -> bool:
    """Check if two font names belong to the same family."""
    # Strip style suffixes
    def base(f):
        f = f.lower().replace("-", "").replace(" ", "")
        for suffix in ["bold", "italic", "regular", "medium", "light",
                        "semibold", "demibold", "black", "heavy"]:
            f = f.replace(suffix, "")
        return f
    return base(font1) == base(font2)


def _merge_same_y_lines(lines: list) -> list:
    """
    Merge lines that share the same y-position (within 2px tolerance).
    This fixes PDFs where each word is reported as a separate "line."
    """
    if not lines:
        return []

    merged = []
    current = lines[0].copy()
    current["spans"] = list(current["spans"])

    for line in lines[1:]:
        if abs(line["y"] - current["y"]) <= 2.0:
            # Same visual line — merge spans
            current["spans"].extend(line["spans"])
            current["x1"] = max(current["x1"], line["x1"])
        else:
            merged.append(current)
            current = line.copy()
            current["spans"] = list(current["spans"])

    merged.append(current)
    return merged


# ── Header/Footer Detection ─────────────────────────────────────────────────

def detect_headers_footers(meta: dict) -> dict:
    """
    Fix 2: Detect repeating headers and footers with fuzzy matching.
    Strips page numbers before comparison so "Page 3" and "Page 4" variants
    still match.
    Returns dict with 'header_texts' and 'footer_texts' sets to strip.
    """
    if not meta or not meta.get("pages"):
        return {"header_texts": set(), "footer_texts": set()}

    page_height = meta["page_height"]
    num_pages = len(meta["pages"])
    if num_pages < 3:
        return {"header_texts": set(), "footer_texts": set()}

    # Collect raw header/footer texts per page
    raw_headers = []  # list of (raw_text, cleaned_text) per page
    raw_footers = []

    for page_meta in meta["pages"]:
        page_h = page_meta.get("height", page_height)
        header_zone = HEADER_FOOTER_ZONE_PX
        footer_zone = page_h - HEADER_FOOTER_ZONE_PX

        h_texts = []
        f_texts = []

        for line in page_meta["lines"]:
            full_text = "".join(s["text"] for s in line["spans"]).strip()
            if not full_text:
                continue
            if line["y"] < header_zone:
                h_texts.append(full_text)
            elif line["y"] > footer_zone:
                f_texts.append(full_text)

        raw_headers.append(h_texts)
        raw_footers.append(f_texts)

    # Build sets of repeating content (with fuzzy page-number stripping)
    header_texts = _find_repeating_texts(raw_headers, num_pages)
    footer_texts = _find_repeating_texts(raw_footers, num_pages)

    return {"header_texts": header_texts, "footer_texts": footer_texts}


def _clean_page_numbers(text: str) -> str:
    """Strip page-number patterns from text for fuzzy comparison."""
    cleaned = text
    # "– Page 3", "- Page 3", "— Page 42"
    cleaned = re.sub(r'\s*[–—-]\s*[Pp]age\s+\d+\s*$', '', cleaned)
    # Trailing bare numbers (common page markers)
    cleaned = re.sub(r'\s+\d+\s*$', '', cleaned)
    # Leading bare numbers
    cleaned = re.sub(r'^\s*\d+\s+', '', cleaned)
    # "Page 3 of 66"
    cleaned = re.sub(r'\s*[Pp]age\s+\d+\s+(of|/)\s+\d+\s*', '', cleaned)
    return cleaned.strip()


def _find_repeating_texts(per_page_texts: list, num_pages: int) -> set:
    """
    Find text strings that repeat across enough pages to be headers/footers.
    Uses fuzzy matching (strips page numbers before comparison).
    Returns set of cleaned base strings that should be stripped.
    """
    # Count cleaned versions
    cleaned_counter = Counter()
    cleaned_to_raw = {}  # cleaned → set of raw variants

    for page_texts in per_page_texts:
        for raw_text in page_texts:
            cleaned = _clean_page_numbers(raw_text)
            if not cleaned:
                continue
            cleaned_counter[cleaned] += 1
            if cleaned not in cleaned_to_raw:
                cleaned_to_raw[cleaned] = set()
            cleaned_to_raw[cleaned].add(raw_text)

    # Find cleaned strings hitting the threshold
    # Base threshold on pages with content in this zone, not total pages.
    # Prevents exhibit/image pages from diluting the count.
    pages_with_zone_content = sum(1 for texts in per_page_texts if texts)
    effective_base = max(pages_with_zone_content, 3)  # floor of 3
    repeating = set()
    threshold = effective_base * HEADER_FOOTER_THRESHOLD

    for cleaned, count in cleaned_counter.items():
        if count >= threshold:
            repeating.add(cleaned)
            # Also add all raw variants
            for raw in cleaned_to_raw.get(cleaned, set()):
                repeating.add(raw)

    return repeating


def _is_header_footer(line_text: str, hf_data: dict) -> bool:
    """Check if a line's text matches a detected header or footer."""
    stripped = line_text.strip()
    if not stripped:
        return False
    cleaned = _clean_page_numbers(stripped)
    return stripped in hf_data["header_texts"] or stripped in hf_data["footer_texts"] or \
           cleaned in hf_data["header_texts"] or cleaned in hf_data["footer_texts"]


# ── Caption Detection ────────────────────────────────────────────────────────

def is_caption_block(lines_text: list[str]) -> bool:
    """
    Fix 3: Detect court-filing caption blocks.
    A caption block has § symbols, party-vs-party structure, or court identifiers.
    """
    full_text = " ".join(lines_text)

    # Count § symbols
    section_count = full_text.count("§")
    if section_count >= 3:
        return True

    # "v." or "vs." with party-label words
    has_versus = bool(re.search(r'\bv[s]?\.\s', full_text, re.IGNORECASE))
    has_court_words = bool(re.search(
        r'\b(DISTRICT\s+COURT|JUDICIAL\s+DISTRICT|COUNTY|CAUSE\s+NO|CASE\s+NO|PLAINTIFF|DEFENDANT)\b',
        full_text
    ))

    if has_versus and has_court_words:
        return True

    if section_count >= 1 and has_court_words:
        return True

    return False


# ── Markdown Rendering ───────────────────────────────────────────────────────

def render_page_as_markdown(
    page_meta: dict,
    body_font: str,
    body_size: float,
    hf_data: dict,
) -> str:
    """
    Render a single page's font metadata as Markdown.
    Uses conservative heading detection with caption awareness.
    """
    lines = page_meta["lines"]
    page_width = page_meta.get("width", 612)

    if not lines:
        return ""

    # Collect all line texts for caption detection
    all_line_texts = []
    for line in lines:
        text = _spans_to_text(line["spans"]).strip()
        all_line_texts.append(text)

    # Check if the whole page region has caption blocks
    # We'll do per-block detection below instead

    # Page-level bold-noise guard: on scanned pages the bold flag fires on most
    # text; if bold dominates, treat it as noise and suppress emphasis here.
    _tot = _boldc = 0
    for _l in lines:
        for _s in _l["spans"]:
            _t = _s.get("text", "")
            _tot += len(_t)
            if _s.get("is_bold"):
                _boldc += len(_t)
    suppress_bold = _tot > 0 and (_boldc / _tot) > 0.5

    md_lines = []
    i = 0
    in_caption = False

    while i < len(lines):
        line = lines[i]
        spans = line["spans"]
        raw_text = _spans_to_text(spans).strip()

        # Skip empty lines
        if not raw_text:
            md_lines.append("")
            i += 1
            continue

        # Skip headers/footers
        if _is_header_footer(raw_text, hf_data):
            i += 1
            continue

        # Caption block detection (look ahead for § clusters).
        # Captions only appear on the cover page; checking on later pages produces
        # false positives from "COUNTY" in docket-stamp headers + "v." in case citations.
        if not in_caption and page_meta.get("page_num", 1) == 1:
            # Look ahead up to 15 lines for caption pattern
            lookahead = [all_line_texts[j] for j in range(i, min(i + 15, len(lines)))]
            if is_caption_block(lookahead):
                in_caption = True

        if in_caption:
            # Render as blockquote until we hit body text (no more §, no more party names)
            md_lines.append(f"> {raw_text}")
            # Check if caption is ending (no § in next few lines).
            # Don't use "v." as a caption signal — case citations contain v. and would
            # keep body prose stuck in caption mode indefinitely.
            remaining = [all_line_texts[j] for j in range(i + 1, min(i + 5, len(lines)))]
            remaining_text = " ".join(remaining)
            if not remaining_text.count("§"):
                in_caption = False
            i += 1
            continue

        # Heading detection (conservative, context-aware)
        # Pass surrounding line texts so we can detect all-caps paragraphs vs titles
        prev_text = all_line_texts[i - 1] if i > 0 else ""
        next_text = all_line_texts[i + 1] if i + 1 < len(all_line_texts) else ""
        heading_level = _detect_heading(line, raw_text, body_font, body_size, page_width, prev_text, next_text)
        if heading_level:
            prefix = "#" * heading_level
            md_lines.append(f"\n{prefix} {raw_text}\n")
            i += 1
            continue

        # Regular text: apply inline emphasis
        md_text = _render_inline_emphasis(spans, body_font, body_size, suppress_bold)
        md_lines.append(md_text)
        i += 1

    return "\n".join(md_lines)


def _detect_heading(line: dict, text: str, body_font: str, body_size: float, page_width: float,
                    prev_text: str = "", next_text: str = "") -> int:
    """
    Conservative heading detection. Returns heading level (1-3) or 0.
    Fix 3: Excludes single chars, § symbols, short lines.
    Fix 4: Disabled for depositions (handled at routing level).
    Fix 5: All-caps paragraph guard — if adjacent lines are also all-caps,
            this is a paragraph in all-caps (e.g., contract integration clause),
            not a standalone heading.
    """
    if not text or len(text) < MIN_HEADING_LEN:
        return 0

    # Never make § a heading
    if text.strip() in ("§", "§§"):
        return 0

    spans = line["spans"]
    if not spans:
        return 0

    # Get dominant font properties for this line
    total_chars = sum(len(s["text"]) for s in spans)
    if total_chars == 0:
        return 0

    bold_chars = sum(len(s["text"]) for s in spans if s.get("is_bold"))
    avg_size = sum(s["size"] * len(s["text"]) for s in spans) / total_chars

    is_all_bold = bold_chars >= total_chars * 0.8
    is_larger = avg_size > body_size * 1.15
    is_much_larger = avg_size > body_size * 1.4

    # All-caps centered: likely a title
    is_allcaps = text.isupper() and len(text) > 3
    line_center = (line["x0"] + line["x1"]) / 2
    page_center = page_width / 2
    is_centered = abs(line_center - page_center) < page_width * 0.15

    # Short line (< 80 chars) — candidate for heading if styled differently
    is_short = len(text) < 80

    # All-caps paragraph guard: if either the previous or next non-empty line
    # is also all-caps at the same size, this is a paragraph, not a heading.
    # Standalone all-caps titles are isolated — they have non-allcaps neighbors.
    if is_allcaps and not is_much_larger:
        prev_allcaps = prev_text.strip().isupper() and len(prev_text.strip()) > 3 if prev_text.strip() else False
        next_allcaps = next_text.strip().isupper() and len(next_text.strip()) > 3 if next_text.strip() else False
        if prev_allcaps or next_allcaps:
            return 0  # Part of an all-caps paragraph, not a heading

    # Correspondence headers (From:/To:/Subject:/…) are never headings.
    if _is_correspondence_header(text):
        return 0

    # Decision tree — require strong evidence to avoid scan-noise headings.
    if is_much_larger and is_short:
        return 1
    if is_allcaps and is_centered and is_short and is_larger:
        return 2
    if is_larger and is_all_bold and is_short and is_allcaps:
        return 2

    return 0


def _spans_to_text(spans: list) -> str:
    """Join span texts with proper spacing based on x-position gaps."""
    if not spans:
        return ""
    parts = []
    prev_x1 = None
    for span in spans:
        text = span.get("text", "")
        if not text:
            continue
        x0 = span.get("x0", 0)
        # Insert space if there's an x-gap between spans and no existing whitespace
        if prev_x1 is not None and x0 > prev_x1 + 1.0:
            # There's a gap — check if we already have whitespace
            if parts and not parts[-1].endswith((" ", "\t")) and not text.startswith((" ", "\t")):
                parts.append(" ")
        parts.append(text)
        prev_x1 = span.get("x1", x0 + len(text))
    return "".join(parts)


def _coalesce_spans(spans: list) -> list:
    """Merge consecutive spans sharing bold/italic/size, inserting a space when
    there is an x-gap between them, so emphasis wraps whole runs and we never
    emit marker collisions like **a****b** (which drop the inter-word space)."""
    out = []
    for s in spans:
        t = s.get("text", "")
        if not t:
            continue
        if out:
            prev = out[-1]
            same = (bool(prev.get("is_bold")) == bool(s.get("is_bold"))
                    and bool(prev.get("is_italic")) == bool(s.get("is_italic"))
                    and round(prev.get("size", 0), 1) == round(s.get("size", 0), 1))
            if same:
                gap = s.get("x0", 0) > prev.get("x1", 0) + 1.0
                sep = " " if (gap and not prev["text"].endswith((" ", "\t"))
                              and not t.startswith((" ", "\t"))) else ""
                prev["text"] = prev["text"] + sep + t
                prev["x1"] = s.get("x1", prev.get("x1"))
                continue
        out.append(dict(s))
    return out


def _render_inline_emphasis(spans: list, body_font: str, body_size: float,
                            suppress_bold: bool = False) -> str:
    """
    Render spans with Markdown inline emphasis.
    Bold body-size text → **bold**, italic → *italic*.
    Adjacent spans sharing a style are coalesced first so we never emit marker
    collisions like **a****b**. When suppress_bold is set (scan pages where the
    bold flag is noise), emphasis is skipped and text is emitted verbatim.
    """
    if not spans:
        return ""

    spans = _coalesce_spans(spans)
    parts = []
    prev_x1 = None

    for span in spans:
        text = span.get("text", "")
        if not text:
            continue

        x0 = span.get("x0", 0)
        if prev_x1 is not None and x0 > prev_x1 + 1.0:
            if parts and not parts[-1].endswith((" ", "\t")) and not text.startswith((" ", "\t")):
                parts.append(" ")

        is_bold = span.get("is_bold", False) and not suppress_bold
        is_italic = span.get("is_italic", False)
        size = span.get("size", body_size)
        is_body_size = abs(size - body_size) / body_size < 0.20 if body_size > 0 else True

        stripped = text.strip()
        if stripped:
            leading = text[:len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()):]
        else:
            leading = trailing = ""

        if is_body_size and stripped and is_bold and is_italic:
            parts.append(f"{leading}***{stripped}***{trailing}")
        elif is_body_size and stripped and is_bold:
            parts.append(f"{leading}**{stripped}**{trailing}")
        elif is_body_size and stripped and is_italic:
            parts.append(f"{leading}*{stripped}*{trailing}")
        else:
            parts.append(text)

        prev_x1 = span.get("x1", x0 + len(text))

    result = "".join(parts)
    # Clean up doubled emphasis markers that span word boundaries
    result = re.sub(r'\*\*\s+\*\*', ' ', result)
    result = re.sub(r'\*\*\*\s+\*\*\*', ' ', result)
    result = re.sub(r'\*\s+\*', ' ', result)
    # Clean up trailing spaces before punctuation
    result = re.sub(r'\s+([.,;:!?])', r'\1', result)

    return result


# ── Text Extraction Methods ──────────────────────────────────────────────────

def extract_with_pdftotext(pdf_path: Path) -> tuple[list[str], list[dict]]:
    """Returns (list of page texts, list of gap dicts)."""
    # pdftotext.exe (poppler) does not understand the \\?\ extended-length
    # prefix, so pass the plain resolved path. Genuinely long paths that
    # exceed MAX_PATH will fail here and fall back to pdfplumber/pypdf.
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path.resolve()), "-"],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")

    raw = result.stdout
    raw_pages = raw.split("\x0c")
    pages = []
    gaps = []

    for raw_page in raw_pages:
        stripped = raw_page.strip()
        if not stripped and not pages:
            continue
        page_num = len(pages) + 1
        if not stripped:
            gaps.append({
                "page": page_num,
                "type": "IMAGE-ONLY",
                "chars": 0,
                "detail": "No extractable text — image/scan/signature page."
            })
            pages.append("")
        elif len(stripped) < CONTENT_THRESHOLD_CHARS:
            gaps.append({
                "page": page_num,
                "type": "PARTIAL",
                "chars": len(stripped),
                "detail": f"Only {len(stripped)} characters extracted (expected ~2000+). Likely a scan or degraded text layer."
            })
            pages.append(stripped)
        else:
            pages.append(stripped)

    # Remove trailing empty pages
    while pages and not pages[-1]:
        removed_page = len(pages)
        pages.pop()
        gaps = [g for g in gaps if g["page"] != removed_page]

    return pages, gaps


def extract_with_pdfplumber(pdf_path: Path) -> tuple[list[str], list[dict]]:
    import pdfplumber
    pages = []
    gaps = []
    with pdfplumber.open(str(_win_path(pdf_path))) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                gaps.append({
                    "page": i,
                    "type": "IMAGE-ONLY",
                    "chars": 0,
                    "detail": "No extractable text — image/scan/signature page."
                })
            elif len(text) < CONTENT_THRESHOLD_CHARS:
                gaps.append({
                    "page": i,
                    "type": "PARTIAL",
                    "chars": len(text),
                    "detail": f"Only {len(text)} characters extracted (expected ~2000+). Likely a scan or degraded text layer."
                })
            pages.append(text)
    return pages, gaps


def extract_with_pypdf(pdf_path: Path) -> tuple[list[str], list[dict]]:
    from pypdf import PdfReader
    reader = PdfReader(str(_win_path(pdf_path)))
    pages = []
    gaps = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            gaps.append({
                "page": i,
                "type": "IMAGE-ONLY",
                "chars": 0,
                "detail": "No extractable text — image/scan/signature page."
            })
        elif len(text) < CONTENT_THRESHOLD_CHARS:
            gaps.append({
                "page": i,
                "type": "PARTIAL",
                "chars": len(text),
                "detail": f"Only {len(text)} characters extracted (expected ~2000+). Likely a scan or degraded text layer."
            })
        pages.append(text)
    return pages, gaps


# ── Method Registry ──────────────────────────────────────────────────────────

METHOD_MAP = {
    "pdftotext": ("pdftotext (CLI)", extract_with_pdftotext),
    "pdfplumber": ("pdfplumber (Python)", extract_with_pdfplumber),
    "pypdf": ("pypdf (Python)", extract_with_pypdf),
}
DEFAULT_ORDER = ["pdftotext", "pdfplumber", "pypdf"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_pdf_page_count(pdf_path: Path) -> int:
    """Get true page count from PDF metadata without full extraction."""
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(_win_path(pdf_path))).pages)
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(str(_win_path(pdf_path))) as pdf:
            return len(pdf.pages)
    except Exception:
        pass
    return 0


def validate_extraction(pages: list[str], gaps: list[dict], true_page_count: int) -> dict:
    """Validate extracted content against the source PDF."""
    extracted_count = len(pages)
    gap_pages = {g["page"] for g in gaps}
    full_pages = extracted_count - len(gaps)

    missing_pages = []
    if true_page_count > 0 and extracted_count != true_page_count:
        for p in range(1, true_page_count + 1):
            if p > extracted_count:
                missing_pages.append(p)
                gaps.append({
                    "page": p,
                    "type": "EXTRACTION FAILED",
                    "chars": 0,
                    "detail": "Page exists in PDF but was not captured during extraction."
                })

    gaps.sort(key=lambda g: g["page"])

    total_affected = len(gaps)
    effective_total = max(true_page_count, extracted_count) if true_page_count > 0 else extracted_count
    completeness = ((effective_total - total_affected) / effective_total * 100) if effective_total > 0 else 0.0

    return {
        "true_page_count": true_page_count,
        "extracted_count": extracted_count,
        "full_pages": effective_total - total_affected,
        "total_affected": total_affected,
        "completeness_pct": round(completeness, 1),
        "gaps": gaps,
        "missing_pages": missing_pages,
        "is_complete": total_affected == 0 and (true_page_count == 0 or extracted_count == true_page_count),
    }


def build_content_gaps_block(validation: dict, output_format: str = "txt") -> str:
    """Build the CONTENT GAPS block at the top of every output file."""
    lines = [SEPARATOR]

    if validation["is_complete"]:
        lines.append(
            f"CONTENT GAPS:  None — all {validation['extracted_count']} pages fully extracted"
        )
    else:
        affected = validation["total_affected"]
        total = max(validation["true_page_count"], validation["extracted_count"])
        lines.append(f"CONTENT GAPS ({affected} of {total} pages affected)")
        lines.append("-" * 70)
        for gap in validation["gaps"]:
            tag = f"[{gap['type']}]"
            lines.append(f"PAGE {gap['page']:>3}:  {tag:<22} {gap['detail']}")
            lines.append(
                f"          → Review original PDF page {gap['page']} for this content."
            )
        lines.append("-" * 70)
        lines.append(
            f"COMPLETENESS: {validation['full_pages']}/{total} pages fully extracted "
            f"({validation['completeness_pct']}%)"
        )

    lines.append(SEPARATOR)
    return "\n".join(lines)


def build_file_header(pdf_path: Path, method: str, validation: dict, output_format: str) -> str:
    """Build the full header: file info + content gaps + notes."""
    total = max(validation["true_page_count"], validation["extracted_count"])
    format_label = "Markdown (.md)" if output_format == "md" else "Plain text (.txt)"
    lines = [
        SEPARATOR,
        f"FILE:              {pdf_path.name}",
        f"SOURCE:            {pdf_path}",
        f"EXTRACTION METHOD: {method}",
        f"OUTPUT FORMAT:     {format_label}",
        f"TOTAL PAGES:       {total}",
        f"CONVERTED:         {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        SEPARATOR,
        "",
        build_content_gaps_block(validation, output_format),
        "",
        SEPARATOR,
    ]

    if output_format == "md":
        lines.append("NOTE: Headings, bold, and italic are inferred from PDF font metadata.")
        lines.append("      Caption blocks are rendered as blockquotes (> ).")
        lines.append("      [CONTENT GAP] markers indicate pages requiring original PDF review.")
    else:
        lines.append("NOTE: Two-column legal transcripts preserve side-by-side layout.")
        lines.append("      All content is verbatim and fully searchable.")
        lines.append("      [CONTENT GAP] markers indicate pages requiring original PDF review.")

    lines.append(SEPARATOR)
    lines.append("")
    return "\n".join(lines)


def build_page_text_txt(pages: list[str], gaps: list[dict]) -> str:
    """Build body text for .txt output (original format with page markers)."""
    total = len(pages)
    gap_pages = {g["page"]: g for g in gaps}
    sections = []

    for i, text in enumerate(pages, start=1):
        header = f"{SEPARATOR}\n=== PAGE {i} of {total} ===\n{SEPARATOR}"
        if i in gap_pages:
            gap = gap_pages[i]
            if gap["type"] == "IMAGE-ONLY":
                body = f"[CONTENT GAP — PAGE {i}: No extractable text — image/scan/signature page]\n[→ Review original PDF for this page]"
            elif gap["type"] == "PARTIAL":
                body = f"[CONTENT GAP — PAGE {i}: Partial extraction ({gap['chars']} chars). Content below may be incomplete]\n[→ Review original PDF for full content]\n\n{text}"
            else:
                body = f"[CONTENT GAP — PAGE {i}: Extraction failed]\n[→ Review original PDF for this page]"
        else:
            body = text
        sections.append(f"{header}\n\n{body}")

    for gap in gaps:
        if gap["page"] > total:
            header = f"{SEPARATOR}\n=== PAGE {gap['page']} of {gap['page']} ===\n{SEPARATOR}"
            body = f"[CONTENT GAP — PAGE {gap['page']}: Page exists in PDF but extraction failed]\n[→ Review original PDF for this page]"
            sections.append(f"{header}\n\n{body}")

    return "\n\n".join(sections)


def build_page_text_md(pages: list[str], gaps: list[dict], meta: dict, hf_data: dict) -> str:
    """
    Build body text for .md output.
    Uses pdftotext raw text as the base, overlaid with pymupdf structural metadata.
    """
    total = len(pages)
    gap_pages = {g["page"]: g for g in gaps}
    sections = []

    body_font = meta["body_font"] if meta else ""
    body_size = meta["body_size"] if meta else 12.0

    for i, text in enumerate(pages, start=1):
        # HTML comment page marker (invisible when rendered, searchable in raw)
        header = f"<!-- Page {i} of {total} -->"

        if i in gap_pages:
            gap = gap_pages[i]
            if gap["type"] == "IMAGE-ONLY":
                body = f"[CONTENT GAP — PAGE {i}: No extractable text — image/scan/signature page]\n[→ Review original PDF for this page]"
            elif gap["type"] == "PARTIAL":
                body = f"[CONTENT GAP — PAGE {i}: Partial extraction ({gap['chars']} chars). Content below may be incomplete]\n[→ Review original PDF for full content]\n\n{text}"
            else:
                body = f"[CONTENT GAP — PAGE {i}: Extraction failed]\n[→ Review original PDF for this page]"
            sections.append(f"{header}\n\n{body}")
        elif meta and i <= len(meta["pages"]):
            # Render with Markdown formatting from font metadata
            page_meta = meta["pages"][i - 1]
            page_meta["page_num"] = i
            md_content = render_page_as_markdown(page_meta, body_font, body_size, hf_data)

            if md_content.strip():
                sections.append(f"{header}\n\n{md_content}")
            else:
                # Fallback to raw pdftotext if pymupdf page had no content
                sections.append(f"{header}\n\n{text}")
        else:
            # No metadata for this page, use raw text
            sections.append(f"{header}\n\n{text}")

    for gap in gaps:
        if gap["page"] > total:
            header = f"<!-- Page {gap['page']} of {gap['page']} -->"
            body = f"[CONTENT GAP — PAGE {gap['page']}: Page exists in PDF but extraction failed]\n[→ Review original PDF for this page]"
            sections.append(f"{header}\n\n{body}")

    return "\n\n".join(sections)


# ── Manifest ─────────────────────────────────────────────────────────────────

def update_manifest(manifest_path: Path, pdf_name: str, method: str, validation: dict, output_format: str):
    """Append or update an entry in the _cowork_txt/MANIFEST.md file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = max(validation["true_page_count"], validation["extracted_count"])
    format_label = f".{output_format}"

    entry_lines = [
        f"## {pdf_name}",
        f"- Converted: {timestamp}",
        f"- Pages: {validation['full_pages']}/{total} fully extracted | Method: {method}",
        f"- Format: {format_label}",
    ]

    if validation["gaps"]:
        gap_summary_parts = []
        for gap in validation["gaps"]:
            gap_summary_parts.append(f"PAGE {gap['page']} ({gap['type'].lower()})")
        entry_lines.append(f"- Gaps: {', '.join(gap_summary_parts)}")
    else:
        entry_lines.append("- Gaps: None")

    entry_lines.append(f"- Completeness: {validation['completeness_pct']}%")
    entry_lines.append("")

    entry_block = "\n".join(entry_lines)

    if manifest_path.exists():
        existing = manifest_path.read_text(encoding="utf-8")
        marker = f"## {pdf_name}"
        if marker in existing:
            parts = existing.split(marker)
            before = parts[0]
            after_rest = marker.join(parts[1:])
            next_heading = after_rest.find("\n## ", 1)
            if next_heading == -1:
                after = ""
            else:
                after = after_rest[next_heading:]
            new_content = before + entry_block + after
        else:
            new_content = existing.rstrip() + "\n\n" + entry_block
    else:
        new_content = f"# Cowork Conversion Manifest\n\nLast updated: {timestamp}\n\n{entry_block}"

    manifest_path.write_text(new_content, encoding="utf-8")


# ── Core Conversion ─────────────────────────────────────────────────────────

def determine_format(pdf_path: Path, requested_format: str) -> str:
    """
    Determine output format based on document type and user request.
    Returns "md" or "txt".
    """
    if requested_format in ("md", "txt"):
        return requested_format

    # Auto-detect
    doc_type = detect_document_type(pdf_path)
    print(f"  Document type: {doc_type}")

    if doc_type in ("deposition", "two-column"):
        return "txt"
    return "md"


def convert_pdf(
    pdf_path: Path,
    output_path: Path = None,
    force_method: str = None,
    cowork_subfolder: bool = True,
    requested_format: str = "auto",
) -> tuple[Path, dict]:
    """
    Convert a single PDF to .md or .txt with full validation.
    Returns (output_path, validation_report).
    """
    if not _win_path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Determine format
    output_format = determine_format(pdf_path, requested_format)
    suffix = f"_COWORK.{output_format}"

    # Determine output location
    if output_path:
        out_path = output_path
    elif cowork_subfolder:
        subfolder = pdf_path.parent / "_cowork_txt"
        subfolder.mkdir(exist_ok=True)
        out_path = subfolder / f"{pdf_path.stem}{suffix}"
    else:
        out_path = pdf_path.with_name(f"{pdf_path.stem}{suffix}")

    # Get true page count
    true_page_count = get_pdf_page_count(pdf_path)
    print(f"  PDF page count: {true_page_count}")
    print(f"  Output format: .{output_format}")

    # Prefer a DISCO native-text sidecar when present: it is letter-perfect
    # (extracted from the native file, not OCR of a scanned image).
    sidecar_path, sidecar_txt = find_native_txt_sidecar(pdf_path)
    if sidecar_txt is not None:
        print(f"  Using DISCO native text sidecar: {sidecar_path.name}")
        validation = _build_native_validation(true_page_count)
        header = build_native_header(pdf_path, sidecar_path, true_page_count, output_format)
        full_text = header + sidecar_txt.strip() + "\n"
        _win_path(out_path.parent).mkdir(parents=True, exist_ok=True)
        _win_path(out_path).write_text(full_text, encoding="utf-8")
        manifest_path = out_path.parent / "MANIFEST.md"
        update_manifest(manifest_path, pdf_path.name, "DISCO native txt sidecar",
                        validation, output_format)
        print(f"  SAVED (native): {out_path}")
        return out_path, validation

    # Extract font metadata if doing Markdown
    meta = None
    hf_data = {"header_texts": set(), "footer_texts": set()}
    if output_format == "md":
        print("  Extracting font metadata...", end=" ", flush=True)
        meta = extract_font_metadata(pdf_path)
        if meta:
            print(f"OK (body: {meta['body_font']} @ {meta['body_size']}pt)")
            hf_data = detect_headers_footers(meta)
            hf_count = len(hf_data["header_texts"]) + len(hf_data["footer_texts"])
            if hf_count:
                print(f"  Detected {hf_count} repeating header/footer patterns to strip")
        else:
            print("SKIP (pymupdf not available, falling back to plain text in .md)")

    # Try extraction methods
    order = [force_method] if force_method else DEFAULT_ORDER
    last_error = None

    for key in order:
        if key not in METHOD_MAP:
            continue
        method_name, func = METHOD_MAP[key]

        if key == "pdftotext" and not shutil.which("pdftotext"):
            print(f"  Skipping {method_name} — not in PATH")
            continue

        try:
            print(f"  Trying {method_name}...", end=" ", flush=True)
            pages, gaps = func(pdf_path)

            # Validate
            validation = validate_extraction(pages, gaps, true_page_count)

            # Build output
            header = build_file_header(pdf_path, method_name, validation, output_format)

            if output_format == "md" and meta:
                body = build_page_text_md(pages, validation["gaps"], meta, hf_data)
            else:
                body = build_page_text_txt(pages, validation["gaps"])

            full_text = header + body

            # Write output (use \\?\ prefix on Windows to bypass 260-char MAX_PATH)
            _win_path(out_path.parent).mkdir(parents=True, exist_ok=True)
            _win_path(out_path).write_text(full_text, encoding="utf-8")
            size_kb = _win_path(out_path).stat().st_size // 1024

            status = "COMPLETE" if validation["is_complete"] else f"GAPS DETECTED ({validation['completeness_pct']}%)"
            print(f"OK  ({validation['extracted_count']} pages, {size_kb} KB) [{status}]")

            if validation["gaps"]:
                for gap in validation["gaps"]:
                    print(f"  WARNING PAGE {gap['page']}: [{gap['type']}] {gap['detail']}")

            print(f"  SAVED: {out_path}")

            # Update manifest
            manifest_path = out_path.parent / "MANIFEST.md"
            update_manifest(manifest_path, pdf_path.name, method_name, validation, output_format)

            return out_path, validation

        except Exception as e:
            print(f"FAIL  ({e})")
            last_error = e

    raise RuntimeError(f"All methods failed for {pdf_path.name}. Last error: {last_error}")


def convert_directory(
    dir_path: Path,
    force_method: str = None,
    skip_existing: bool = False,
    cowork_subfolder: bool = True,
    requested_format: str = "auto",
) -> dict:
    """Batch-convert all PDFs in a directory."""
    pdfs = sorted(dir_path.glob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found in {dir_path}")
        return {"total": 0, "successes": [], "failures": [], "skipped": []}

    print(f"Found {len(pdfs)} PDF(s) in {dir_path}\n")
    successes = []
    failures = []
    skipped = []

    for pdf in pdfs:
        if skip_existing:
            if cowork_subfolder:
                existing_md = dir_path / "_cowork_txt" / f"{pdf.stem}_COWORK.md"
                existing_txt = dir_path / "_cowork_txt" / f"{pdf.stem}_COWORK.txt"
            else:
                existing_md = pdf.with_name(f"{pdf.stem}_COWORK.md")
                existing_txt = pdf.with_name(f"{pdf.stem}_COWORK.txt")
            if _win_path(existing_md).exists() or _win_path(existing_txt).exists():
                print(f"[{pdf.name}] — skipped (output already exists)")
                skipped.append(pdf.name)
                continue

        print(f"[{pdf.name}]")
        try:
            out_path, validation = convert_pdf(
                pdf,
                force_method=force_method,
                cowork_subfolder=cowork_subfolder,
                requested_format=requested_format,
            )
            successes.append({
                "name": pdf.name,
                "output": str(out_path),
                "format": out_path.suffix.lstrip("."),
                "completeness": validation["completeness_pct"],
                "gaps": len(validation["gaps"]),
            })
        except Exception as e:
            print(f"  FAILED: {e}")
            failures.append({"name": pdf.name, "error": str(e)})
        print()

    print(SEPARATOR)
    print(f"Converted: {len(successes)} / {len(pdfs)}")
    if skipped:
        print(f"Skipped:   {len(skipped)} (already had output)")
    if failures:
        print(f"Failed:    {', '.join(f['name'] for f in failures)}")

    incomplete = [s for s in successes if s["completeness"] < 100]
    if incomplete:
        print(f"\nWARNING FILES WITH CONTENT GAPS:")
        for s in incomplete:
            print(f"  {s['name']}: {s['completeness']}% complete ({s['gaps']} pages affected)")
        print(f"  -> Check MANIFEST.md in _cowork_txt/ for details.")

    return {
        "total": len(pdfs),
        "successes": successes,
        "failures": failures,
        "skipped": skipped,
    }


# ── JSON Report ──────────────────────────────────────────────────────────────

def write_json_report(report_path: Path, results: dict):
    """Write a machine-readable JSON report for the Cowork skill."""
    report_path.write_text(
        json.dumps(results, indent=2, default=str),
        encoding="utf-8"
    )


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF(s) to Markdown or plain-text files readable by Cowork.",
    )
    parser.add_argument("input", help="PDF file path, or directory to batch-convert")
    parser.add_argument(
        "--output", "-o",
        help="Output path (single-file mode only)",
        default=None,
    )
    parser.add_argument(
        "--method", "-m",
        choices=list(METHOD_MAP.keys()),
        help="Force text extraction method (default: auto-tries all)",
        default=None,
    )
    parser.add_argument(
        "--format", "-f",
        choices=["md", "txt", "auto"],
        help="Output format: md (Markdown), txt (plain text), auto (detect document type). Default: auto.",
        default="auto",
        dest="format",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Directory mode: skip PDFs that already have output",
    )
    parser.add_argument(
        "--no-subfolder",
        action="store_true",
        help="Save output next to the PDF instead of in _cowork_txt/ subfolder",
    )
    parser.add_argument(
        "--json-report",
        help="Path to write a JSON report (for Cowork skill integration)",
        default=None,
    )

    args = parser.parse_args()
    target = Path(args.input)
    use_subfolder = not args.no_subfolder

    if target.is_dir():
        results = convert_directory(
            target,
            force_method=args.method,
            skip_existing=args.skip_existing,
            cowork_subfolder=use_subfolder,
            requested_format=args.format,
        )
        if args.json_report:
            write_json_report(Path(args.json_report), results)
    elif target.is_file():
        out_path, validation = convert_pdf(
            target,
            output_path=Path(args.output) if args.output else None,
            force_method=args.method,
            cowork_subfolder=use_subfolder,
            requested_format=args.format,
        )
        if args.json_report:
            write_json_report(Path(args.json_report), {
                "total": 1,
                "successes": [{
                    "name": target.name,
                    "output": str(out_path),
                    "format": out_path.suffix.lstrip("."),
                    "completeness": validation["completeness_pct"],
                    "gaps": len(validation["gaps"]),
                }],
                "failures": [],
                "skipped": [],
            })
    else:
        print(f"Error: {target} is not a file or directory.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
