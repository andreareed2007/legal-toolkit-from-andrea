#!/usr/bin/env python3
"""
pdf_to_cowork_md.py — Cowork Edition (v3, opendataloader-pdf engine)
--------------------------------------------------------------------
Converts PDF(s) to Markdown (.md) or plain-text (.txt) files that Cowork
can read completely and search.

ENGINE CHAIN (every tier quality-gated against the pdftotext reference):
  - opendataloader-pdf → first tier for .md AND .txt: XY-Cut++ reading order,
    headings, lists, real Markdown tables, ~~strikethrough~~, header/footer
    and hidden/off-page text filtering. Local Java mode ONLY — the hybrid/AI
    (OCR) backend is never enabled.
  - pymupdf4llm        → second-tier .md engine (font-hierarchy headings, tables)
  - pymupdf (PyMuPDF)  → legacy font-metadata renderer (headings, bold, italic)
  - pdftotext -layout  → reference text extraction + .txt fallback
  - pdfplumber / pypdf → fallback text extraction if pdftotext unavailable

DOCUMENT-TYPE ROUTING:
  - Depositions (monospaced, line numbers, Q/A markers) → .txt
  - Two-column detected → .txt
  - Everything else (briefs, motions, orders, contracts) → .md

WORKING COPY:
  - --password decrypts once into a temp copy; --pages subsets the PDF and
    the output keeps ORIGINAL page numbers.

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
    r"""Return a Path prefixed with \\?\ on Windows to bypass the 260-char MAX_PATH limit."""
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


# ── Duplicate text-layer dedup ─────────────────────────────────────────────────
# Some PDFs (e.g. a scanned brief re-saved with an OCR text layer over the
# original export layer) carry TWO overlapping text layers. PyMuPDF get_text()
# and pdftotext both extract BOTH copies, so every page appears twice and the
# citation/instance count roughly doubles. We remove the duplicate two ways,
# both font-agnostic:
#   (1) span-font layer drop — when a page's fonts split into two groups whose
#       words are near-identical, drop the more span-fragmented (OCR-style)
#       group and keep the other. Runs on the PyMuPDF font metadata that drives
#       the .md render path.
#   (2) normalized-similarity text dedup — collapse line-level ("A A" on one
#       row, as pdftotext -layout emits) and page-level (first-half ~ second-
#       half) duplication in already-extracted page text. Runs on the pdftotext
#       path (.txt output and the .md fallback) and catches doubling from any
#       cause, not just this font pattern.

from difflib import SequenceMatcher

_DUPE_QUOTE_MAP = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
                   "\u201e": '"', "\u201a": "'", "\u2032": "'", "\u2033": '"',
                   "`": "'", "\u00b4": "'"}
_DUPE_DASH_MAP = {"\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2212": "-"}


def _dupe_fold(s: str) -> str:
    """Normalize text for duplicate detection: fold smart quotes/dashes, strip
    markdown emphasis, collapse whitespace, lowercase."""
    for a, b in _DUPE_QUOTE_MAP.items():
        s = s.replace(a, b)
    for a, b in _DUPE_DASH_MAP.items():
        s = s.replace(a, b)
    s = s.replace("*", "").replace("_", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def _dupe_richness(s: str) -> int:
    """Higher for the 'original' layer copy: it keeps smart quotes / em dashes /
    markdown emphasis, whereas the OCR-style duplicate uses straight ASCII."""
    return (s.count("\u201c") + s.count("\u201d") + s.count("\u2018")
            + s.count("\u2019") + s.count("\u2014") + s.count("\u2013")
            + s.count("*"))


def _token_containment(a: str, b: str) -> float:
    """Order-independent share of a's tokens (with multiplicity) that also appear
    in b. Containment, not Jaccard: the OCR duplicate layer's words are a SUBSET
    of the full-page vocabulary, so containment stays high even when the original
    layer is split across several fonts (roman + italic case names + dotted
    leaders) or a third unrelated font (e.g. figure labels) is present."""
    ca = Counter(_dupe_fold(a).split())
    cb = Counter(_dupe_fold(b).split())
    if not ca or not cb:
        return 0.0
    inter = sum((ca & cb).values())
    return inter / sum(ca.values())


_LAYER_COVERAGE = 0.25   # a duplicate layer must cover >= this share of the page
_LAYER_CONTAIN = 0.62    # share of the OCR layer's tokens found in the other layer


def detect_duplicate_layer_fonts(font_stats: dict) -> set:
    """Given {font_name: {'chars': int, 'spans': int, 'text': str}} for one page,
    return the set of font names belonging to a duplicate (OCR-style) text layer
    that should be dropped, or an empty set if the page is not doubled.

    Font-agnostic: the duplicate layer is the MOST span-fragmented font group
    (OCR relayers emit roughly one span per word) whose words replicate the rest
    of the page. We keep the less-fragmented group — the original export layer,
    which carries the smart quotes and the italic/bold runs used for emphasis."""
    total = sum(v["chars"] for v in font_stats.values())
    if total < 60 or len(font_stats) < 2:
        return set()
    cands = sorted(font_stats.items(),
                   key=lambda kv: kv[1]["spans"] / max(1, kv[1]["chars"]),
                   reverse=True)
    for font, info in cands:
        if info["chars"] < total * _LAYER_COVERAGE:
            continue
        rest_text = "".join(v["text"] for g, v in font_stats.items() if g != font)
        if len(_dupe_fold(rest_text)) < total * 0.20:
            continue
        if _token_containment(info["text"], rest_text) >= _LAYER_CONTAIN:
            return {font}
    return set()


_DUPE_MIN_FOLD = 12
_DUPE_LINE_SIM = 0.90
_DUPE_PAGE_SIM = 0.90
_DUPE_BLOCK_SIM = 0.90


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _dedupe_doubled_line(line: str):
    """If one physical line is its own content repeated twice ('A A' side by
    side, as pdftotext -layout emits for a dual text layer), return one copy;
    else None."""
    core = line.strip()
    folded = _dupe_fold(core)
    if len(folded) < _DUPE_MIN_FOLD:
        return None
    fw = folded.split(" ")
    n = len(fw)
    if n < 4:
        return None
    best = None
    for m in {n // 2, (n + 1) // 2}:
        if 0 < m < n:
            r = _ratio(" ".join(fw[:m]), " ".join(fw[m:]))
            if best is None or r > best[0]:
                best = (r, m)
    if not best or best[0] < _DUPE_LINE_SIM:
        return None
    raw = core.split()
    if len(raw) != n:
        return None
    m = best[1]
    first = " ".join(raw[:m]); second = " ".join(raw[m:])
    return second if _dupe_richness(second) >= _dupe_richness(first) else first


_DUPE_FOOTER_LINE_MAX = 45  # a footer-like line is short (page no. / doc id)


def _dedupe_adjacent_blocks(lines: list):
    """Collapse an immediately repeated run of SHORT footer-like lines (e.g. a
    footer emitted twice by a dual layer: '57' / 'CORE/...' / '57' / 'CORE/...').
    Restricted to short lines so parallel prose (string cites, list items) is
    never collapsed. Returns (lines, changed)."""
    out = []
    i = 0
    n = len(lines)
    changed = False
    while i < n:
        collapsed = False
        for L in range(min(4, (n - i) // 2), 0, -1):
            a = lines[i:i + L]
            b = lines[i + L:i + 2 * L]
            # every line in both blocks must be short (footer-like)
            if any(len(_dupe_fold(x)) > _DUPE_FOOTER_LINE_MAX for x in a + b):
                continue
            fa = _dupe_fold("\n".join(a)); fb = _dupe_fold("\n".join(b))
            if len(fa) < 4:
                continue
            if _ratio(fa, fb) >= 0.95:
                keep = b if _dupe_richness("\n".join(b)) >= _dupe_richness("\n".join(a)) else a
                out.extend(keep)
                i += 2 * L
                collapsed = True
                changed = True
                break
        if not collapsed:
            out.append(lines[i])
            i += 1
    return out, changed


def _dedupe_page_halves(lines: list):
    """Collapse whole-page doubling where the first half ~ the second half.
    Returns (lines, changed)."""
    ne = [i for i, l in enumerate(lines) if l.strip()]
    if len(ne) < 6:
        return lines, False
    best = None
    lo = int(len(ne) * 0.30); hi = int(len(ne) * 0.70) + 1
    for k in range(max(1, lo), min(len(ne) - 1, hi)):
        s = ne[k]
        top = _dupe_fold("\n".join(lines[:s])); bot = _dupe_fold("\n".join(lines[s:]))
        if len(top) < _DUPE_MIN_FOLD or len(bot) < _DUPE_MIN_FOLD:
            continue
        r = _ratio(top, bot)
        if best is None or r > best[0]:
            best = (r, s)
    if best and best[0] >= _DUPE_PAGE_SIM:
        s = best[1]
        top = lines[:s]; bot = lines[s:]
        keep = bot if _dupe_richness("\n".join(bot)) >= _dupe_richness("\n".join(top)) else top
        return keep, True
    return lines, False


def dedupe_page_text(text: str):
    """Remove line-level and page-level text-layer duplication from one page's
    text. Returns (deduped_text, changed_bool). No-op on non-doubled pages."""
    lines = text.split("\n")
    changed = False
    p0 = []
    for ln in lines:
        d = _dedupe_doubled_line(ln)
        if d is not None:
            indent = ln[:len(ln) - len(ln.lstrip())] if ln.strip() else ""
            p0.append(indent + d)
            changed = True
        else:
            p0.append(ln)
    lines = p0
    lines, c1 = _dedupe_adjacent_blocks(lines); changed = changed or c1
    lines, c2 = _dedupe_page_halves(lines); changed = changed or c2
    lines, c3 = _dedupe_adjacent_blocks(lines); changed = changed or c3
    return "\n".join(lines), changed


def page_doubling_fraction(page_texts: list) -> float:
    """Fraction of pages whose text is internally near-duplicated by a
    SUBSTANTIAL amount (>25% of the page's characters collapse away). The
    substantial-removal test avoids counting a page merely because a short
    doubled footer collapsed. Used by the cite-check doubling gate."""
    if not page_texts:
        return 0.0
    doubled = 0
    for t in page_texts:
        stripped = t.strip()
        if len(stripped) < 200:
            continue
        new, changed = dedupe_page_text(t)
        if changed and (len(stripped) - len(new.strip())) >= 0.25 * len(stripped):
            doubled += 1
    return doubled / len(page_texts)


def find_native_txt_sidecar(pdf_path: Path):
    """Return (path, text) of a sibling native-text sidecar, or (None, None).

    DISCO exports letter-perfect native text (from the original file, not OCR of
    the scanned image) as `<stem>.txt`, usually in a `Text/` subfolder. When one
    exists we prefer it over any PDF extraction.
    """
    if os.environ.get("COWORK_IGNORE_SIDECAR"):
        return None, None
    stem = pdf_path.stem
    parent = pdf_path.parent
    cands = [parent / "Text" / f"{stem}.txt", parent / f"{stem}.txt"]
    # Relativity / Concordance load-file layout: the PDF sits in
    # VOL001/IMAGES/IMG001/ and its extracted text in VOL001/TEXT/TEXT001/.
    # Walk up to the volume folder (2 levels) and look under any TEXT* dir.
    for up in (parent.parent, parent.parent.parent):
        try:
            if not _win_path(up).is_dir():
                continue
            for d in _win_path(up).iterdir():
                if d.is_dir() and d.name.upper().startswith("TEXT"):
                    cands.append(d / f"{stem}.txt")
                    for sub in d.iterdir():
                        if sub.is_dir():
                            cands.append(sub / f"{stem}.txt")
        except Exception:
            pass
    for cand in cands:
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

    # Pre-pass: per page, detect a duplicate (OCR-style) text layer to drop.
    page_drop_fonts = []
    doubled_layer_pages = 0
    for page in doc:
        fstats = {}
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fn = span.get("font", "")
                    d = fstats.setdefault(fn, {"chars": 0, "spans": 0, "text": ""})
                    txt = span.get("text", "")
                    d["chars"] += len(txt)
                    d["spans"] += 1
                    d["text"] += txt + " "
        drop = detect_duplicate_layer_fonts(fstats)
        page_drop_fonts.append(drop)
        if drop:
            doubled_layer_pages += 1
    if doubled_layer_pages:
        print(f"    duplicate text layer on {doubled_layer_pages}/{doc.page_count} "
              f"page(s) — dropping the OCR-style copy", flush=True)

    # First pass: determine body font/size (ignoring any dropped layer)
    font_counter = Counter()
    for page_idx, page in enumerate(doc):
        drop = page_drop_fonts[page_idx]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("font", "") in drop:
                        continue
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
                drop = page_drop_fonts[page_idx]
                spans = [s for s in line.get("spans", []) if s.get("font", "") not in drop]
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
    # "Page 3 of 66", "- Page 3 of 66", "Page 3 / 66" — must run BEFORE the
    # trailing-number strip below, which would otherwise eat the "66" and
    # leave "Page 3 of" varying per page (so the footer never repeats).
    cleaned = re.sub(r'\s*[–—-]?\s*[Pp]age\s+\d+\s*(of|/)\s*\d+\s*', ' ', cleaned)
    # "– Page 3", "- Page 3", "— Page 42"
    cleaned = re.sub(r'\s*[–—-]\s*[Pp]age\s+\d+\s*$', '', cleaned)
    # Trailing bare numbers (common page markers)
    cleaned = re.sub(r'\s+\d+\s*$', '', cleaned)
    # Leading bare numbers
    cleaned = re.sub(r'^\s*\d+\s+', '', cleaned)
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


# ── pymupdf4llm Markdown Engine (v2026.08.27-1) ──────────────────────────────
# Default engine for .md output. Produces GitHub-flavored Markdown per page:
# heading levels from document font hierarchy, real Markdown tables, bold /
# italic, lists. Text-layer only — never OCRs. Quality-gated: if output loses
# content vs the reference extraction, or shows dual-layer doubling, the
# legacy font-metadata renderer is used instead.

def _alnum_len(s: str) -> int:
    return sum(1 for c in s if c.isalnum())


def extract_md_pages_pymupdf4llm(pdf_path: Path, true_page_count: int):
    """
    Per-page Markdown via pymupdf4llm. Returns list of page markdown strings
    (index 0 = page 1) or None if pymupdf4llm is unavailable or errors.
    """
    try:
        import pymupdf4llm
    except ImportError:
        return None
    try:
        try:
            # HARD RULE: use_ocr=False / force_ocr=False — pymupdf4llm >= 1.28
            # ships an integrated OCR path (Tesseract/RapidOCR) that is ON by
            # default. This skill NEVER OCRs without going through its own
            # controlled Tesseract pass, so it is disabled unconditionally here.
            chunks = pymupdf4llm.to_markdown(
                str(_win_path(pdf_path)), page_chunks=True, show_progress=False,
                use_ocr=False, force_ocr=False,
            )
        except TypeError:
            # Older pymupdf4llm without the layout/OCR engine: the rag path
            # has no OCR capability at all, so plain call is safe.
            chunks = pymupdf4llm.to_markdown(str(_win_path(pdf_path)), page_chunks=True)
    except Exception as e:
        print(f"[pymupdf4llm failed: {e}]", end=" ", flush=True)
        return None
    md_pages = [""] * true_page_count
    for i, ch in enumerate(chunks):
        if i >= true_page_count:
            break
        md_pages[i] = (ch.get("text") or "").strip()
    return md_pages


def md_engine_quality_ok(md_pages, raw_pages, engine: str = "pymupdf4llm", skip_pages=None) -> bool:
    """
    Doc-level acceptance gate for a structured engine's output (opendataloader-pdf
    or pymupdf4llm). Reject (-> next engine in the chain) when content is lost
    vs the reference pdftotext extraction or when the output shows
    duplicate-text-layer doubling.
    """
    if md_pages is None:
        return False
    skip = set(skip_pages or ())   # 1-based pages whose reference text came from OCR
    md_chars = sum(_alnum_len(p) for i, p in enumerate(md_pages, 1) if i not in skip)
    raw_chars = sum(_alnum_len(p) for i, p in enumerate(raw_pages, 1) if i not in skip)
    if raw_chars >= 200 and md_chars < 0.70 * raw_chars:
        print(f"[{engine} content check failed: {md_chars} vs {raw_chars} alnum chars]",
              end=" ", flush=True)
        return False
    if page_doubling_fraction(md_pages) >= 0.30:
        print(f"[{engine} doubling check failed]", end=" ", flush=True)
        return False
    return True


# ── opendataloader-pdf Engine ────────────────────────────────────────────────
#
# opendataloader-pdf (Apache 2.0, https://github.com/opendataloader-project/
# opendataloader-pdf) is a deterministic Java PDF-structure extractor with
# XY-Cut++ reading order, heading/list/table detection, strikethrough
# detection, header/footer filtering and hidden/off-page text filtering.
# It is the FIRST-TIER engine for both .md and .txt output. Only its free,
# local Java mode is ever used here. The hybrid/AI backend (docling-fast,
# hancom-ai) performs OCR and is NEVER enabled — see the No-OCR rule.
# Requires: pip install opendataloader-pdf, plus a Java 11+ runtime on PATH.
# If either is missing the chain falls through to pymupdf4llm / pdftotext.

ODL_PAGE_SEP = "\n@@COWORK-ODL-PAGE %page-number%@@\n"
_ODL_PAGE_RE = re.compile(r"^@@COWORK-ODL-PAGE (\d+)@@[ \t]*$", re.M)
_ODL_ORDINAL_BULLET_RE = re.compile(
    r"^(\s*)- (?=(?:\d{1,4}[.)]|\(\w{1,4}\)|[A-Za-z][.)]|[ivxlcIVXLC]{1,6}[.)])\s)"
)


def odl_version():
    try:
        from importlib.metadata import version
        return version("opendataloader-pdf")
    except Exception:
        return None


def odl_available():
    """Returns (ok, reason). Never installs anything."""
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError:
        return False, "opendataloader-pdf not installed (pip install opendataloader-pdf)"
    if not shutil.which("java"):
        return False, "java runtime not in PATH (opendataloader-pdf needs Java 11+)"
    return True, ""


def _normalize_odl_md(page: str) -> str:
    """
    Post-process one page of opendataloader-pdf Markdown for verbatim fidelity:
      - '- 1. text' / '- (a) text' list items: the document's own ordinal is
        the marker, so drop the synthetic '- ' bullet (keeps numbered
        paragraphs verbatim; '1. text' is still a valid Markdown list).
      - Drop empty tables (rectangles on signature pages become '| |').
      - Collapse 3+ blank lines to 2.
    """
    if not page:
        return page
    out = []
    table_buf = []

    def flush_table():
        if table_buf:
            if any(_alnum_len(l) for l in table_buf):
                out.extend(table_buf)
            table_buf.clear()

    for line in page.splitlines():
        if line.lstrip().startswith("|"):
            table_buf.append(line)
            continue
        flush_table()
        out.append(_ODL_ORDINAL_BULLET_RE.sub(r"\1", line))
    flush_table()
    text = "\n".join(out)
    # '~~This~~ ~~clause~~ ~~was~~' (per-word strikethrough) -> '~~This clause was~~'
    text = re.sub(r"~~([ \t]+)~~(?=\S)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def run_odl(pdf_path: Path, fmt: str, odl_opts: dict):
    """
    Run opendataloader-pdf ONCE (local Java mode only) and return
    {"md": <markdown text or None>, "json": <parsed JSON tree or None>}.
    Markdown is requested for fmt='md'; JSON is always requested — it is the
    source for .txt output (see _odl_json_pages) and for Bates/confidentiality
    stamp detection (see detect_stamps_from_odl). Returns None if the engine
    is unavailable or fails.
    """
    ok, reason = odl_available()
    if not ok:
        print(f"[skip: {reason}]", end=" ", flush=True)
        return None
    import contextlib
    import io
    import tempfile
    import opendataloader_pdf

    odl_opts = odl_opts or {}
    result = {"md": None, "json": None}
    with tempfile.TemporaryDirectory(prefix="cowork_odl_") as td:
        kwargs = dict(
            input_path=str(pdf_path.resolve()),
            output_dir=td,
            format="markdown,json" if fmt == "md" else "json",
            quiet=True,
            image_output="off",
        )
        # Verbatim first: ODL's own header/footer/watermark filter is
        # position-based over the WHOLE page and drops anything that repeats
        # at the same spot on most pages — running headers, but also Bates
        # stamps, CONFIDENTIAL legends, exhibit labels and (on form-like
        # documents) real body text. So it is kept OFF here; the .md path
        # strips only zone-limited repeating headers/footers via the skill's
        # own detector (_strip_hf_from_md), and .txt keeps everything.
        kwargs["include_header_footer"] = True
        if fmt == "md":
            kwargs["markdown_page_separator"] = ODL_PAGE_SEP
            kwargs["detect_strikethrough"] = True
        else:
            # .txt is the verbatim path: keep the PDF's own line breaks
            # (applies to the JSON 'content' fields too).
            kwargs["keep_line_breaks"] = True
        if odl_opts.get("use_struct_tree"):
            kwargs["use_struct_tree"] = True
        if odl_opts.get("sanitize"):
            kwargs["sanitize"] = True
        if odl_opts.get("content_safety_off"):
            kwargs["content_safety_off"] = odl_opts["content_safety_off"]
        if odl_opts.get("table_method"):
            kwargs["table_method"] = odl_opts["table_method"]
        if odl_opts.get("threads") and int(odl_opts["threads"]) > 1:
            kwargs["threads"] = str(odl_opts["threads"])
        # NOTE: no 'hybrid' / 'hybrid_mode' / 'hybrid_url' keys are ever set.

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                opendataloader_pdf.convert(**kwargs)
        except Exception as e:
            msg = str(e).strip().splitlines()[-1] if str(e).strip() else repr(e)
            print(f"[opendataloader-pdf failed: {msg}]", end=" ", flush=True)
            return None

        def _find(ext):
            cand = Path(td) / f"{pdf_path.stem}.{ext}"
            if cand.exists():
                return cand
            hits = list(Path(td).glob(f"*.{ext}"))
            return hits[0] if hits else None

        jf = _find("json")
        if jf is not None:
            try:
                result["json"] = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                print(f"[opendataloader-pdf json unreadable: {e}]", end=" ", flush=True)
        if fmt == "md":
            mf = _find("md")
            if mf is not None:
                result["md"] = mf.read_text(encoding="utf-8", errors="replace")
    if result["md"] is None and result["json"] is None:
        print("[opendataloader-pdf produced no output]", end=" ", flush=True)
        return None
    return result


def _odl_md_pages(md_text: str, true_page_count: int):
    """Split ODL Markdown on the page separator into a per-page list."""
    pages = [""] * true_page_count
    if not md_text:
        return None
    parts = _ODL_PAGE_RE.split(md_text)
    if len(parts) < 3:
        if true_page_count == 1:
            pages[0] = md_text.strip()
    else:
        for i in range(1, len(parts) - 1, 2):
            try:
                n = int(parts[i])
            except ValueError:
                continue
            if 1 <= n <= true_page_count:
                pages[n - 1] = parts[i + 1].strip()
    return [_normalize_odl_md(p) for p in pages]


_ODL_CHILD_KEYS = ("kids", "list items", "rows", "cells")


def _odl_walk(node, page, out):
    """Depth-first walk of the ODL JSON tree in reading order.
    Appends (page, type, content, bbox) for every node with text content;
    children inherit the page of their nearest ancestor when their own is null."""
    if isinstance(node, list):
        for k in node:
            _odl_walk(k, page, out)
        return
    if not isinstance(node, dict):
        return
    pg = node.get("page number")
    if not isinstance(pg, int):
        pg = page
    content = node.get("content")
    if isinstance(content, str) and content.strip():
        out.append((pg, node.get("type", ""), content, node.get("bounding box")))
    if node.get("type") == "table":
        cells = _transcript_cells_in_order(node)
        if cells is not None:
            for c in cells:
                _odl_walk(c, pg, out)
            return
    # Child containers are keyed by element type: generic 'kids', list
    # 'list items', table 'rows', table row 'cells' (ODL 2.5.x JSON).
    for key in _ODL_CHILD_KEYS:
        kids = node.get(key)
        if kids:
            _odl_walk(kids, pg, out)


_TRANSCRIPT_PAGE_LABEL_RE = re.compile(r"^\s*Page\s+(\d{1,5})\s*$", re.I)


def _first_text(node):
    """First non-empty text content in a subtree (depth-first)."""
    if isinstance(node, list):
        for k in node:
            t = _first_text(k)
            if t:
                return t
        return None
    if not isinstance(node, dict):
        return None
    c = node.get("content")
    if isinstance(c, str) and c.strip():
        return c.strip().splitlines()[0]
    for key in _ODL_CHILD_KEYS:
        if node.get(key):
            t = _first_text(node[key])
            if t:
                return t
    return None


def _transcript_cells_in_order(table: dict):
    """
    A condensed deposition transcript prints 2 or 4 mini-pages per sheet
    inside ruled boxes, which ODL sees as a table and would read ROW by row
    (13, 15 / 14, 16). Each mini-page starts with its own 'Page N' label, so
    when every cell of a table starts that way, return the cells sorted by
    that label (13, 14, 15, 16). Otherwise return None (normal table order).
    """
    cells = []
    for row in table.get("rows") or []:
        for cell in (row.get("cells") or []) if isinstance(row, dict) else []:
            cells.append(cell)
    if len(cells) < 2:
        return None
    keyed = []
    for c in cells:
        t = _first_text(c)
        m = _TRANSCRIPT_PAGE_LABEL_RE.match(t or "")
        if not m:
            return None
        keyed.append((int(m.group(1)), c))
    keyed.sort(key=lambda x: x[0])
    return [c for _, c in keyed]


def _odl_json_pages(tree: dict, true_page_count: int):
    """
    Build per-page plain text from the ODL JSON tree (reading order).

    Why not ODL's own text writer: as of 2.5.x it drops list content nested
    inside table cells — and a condensed deposition transcript with ruled
    boxes around each mini-page IS a table of lists, so its text output
    kept only the 'Page 13' labels. The JSON tree has everything; walking it
    here gives the same reading order with every element's text.
    """
    if not tree:
        return None
    items = []
    _odl_walk(tree.get("kids", []), None, items)
    pages = [[] for _ in range(true_page_count)]
    prev_type = [None] * true_page_count
    for pg, typ, content, _bbox in items:
        if not isinstance(pg, int) or not (1 <= pg <= true_page_count):
            continue
        buf = pages[pg - 1]
        content = content.rstrip()
        if buf:
            # consecutive list items (transcript lines) stay single-spaced
            if typ == "list item" and prev_type[pg - 1] == "list item":
                buf.append("\n")
            else:
                buf.append("\n\n")
        buf.append(content)
        prev_type[pg - 1] = typ
    return ["".join(b).strip() for b in pages]


def extract_pages_odl(pdf_path: Path, true_page_count: int, fmt: str, odl_opts: dict,
                      _cache: dict = None):
    """
    Return a per-page list (index 0 = page 1) of Markdown (fmt='md') or plain
    text (fmt='txt') from opendataloader-pdf, or None if unavailable/failed.
    If _cache (a dict) is given, the raw run result is stored under 'odl_run'
    so stamp detection can reuse the JSON without a second Java launch.
    """
    run = run_odl(pdf_path, fmt, odl_opts)
    if _cache is not None:
        _cache["odl_run"] = run
    if run is None:
        return None
    if fmt == "md":
        return _odl_md_pages(run.get("md"), true_page_count)
    return _odl_json_pages(run.get("json"), true_page_count)


# ── Bates / confidentiality stamp capture (from ODL JSON bounding boxes) ─────
#
# Production PDFs carry a Bates number and often a confidentiality legend
# burned into the page margin. They are short, sit in the top or bottom
# margin zone, and repeat with a changing number — exactly what repeating-
# header/footer stripping removes from the body. So we capture them from the
# ODL element tree (which has a bounding box for every element) and write
# them into each page's marker line instead.

STAMP_ZONE_PT = 100.0   # element must lie within this many points of the top or bottom edge
STAMP_MAX_CHARS = 90    # stamps are short

_BATES_RE = re.compile(
    r"(?<![A-Za-z0-9])"                       # not glued to other text
    r"([A-Z][A-Z0-9]{1,14}(?:[-_. ][A-Z0-9]{1,14}){0,3}?)"   # prefix, e.g. ACME, DEF-PROD, ABC_ 
    r"[-_ ]?0*(\d{4,9})"                      # 4+ digit sequence (allow leading zeros)
    r"(?![A-Za-z0-9])"
)
# Legends are stamped in CAPS (or Title Case); spoken/body text ("marked
# confidential") is lower case and must not match, so no re.I here.
_CONF_RE = re.compile(
    r"\b((?:HIGHLY|Highly)\s+(?:CONFIDENTIAL|Confidential)(?:\s*[-–—]\s*(?:ATTORNEYS?|Attorneys?)['’]?\s+(?:EYES|Eyes)\s+(?:ONLY|Only))?"
    r"|(?:CONFIDENTIAL|Confidential)(?:\s*[-–—]\s*(?:(?:ATTORNEYS?|Attorneys?)['’]?\s+(?:EYES|Eyes)\s+(?:ONLY|Only)"
    r"|(?:SUBJECT|Subject)\s+(?:TO|to)\s+(?:PROTECTIVE|Protective)\s+(?:ORDER|Order)))?"
    r"|(?:ATTORNEYS?|Attorneys?)['’]?\s+(?:EYES|Eyes)\s+(?:ONLY|Only)"
    r"|(?:OUTSIDE|Outside)\s+(?:COUNSEL|Counsel)(?:['’][Ss])?\s+(?:EYES|Eyes)\s+(?:ONLY|Only)"
    r"|PROTECTED(?:\s+HEALTH\s+INFORMATION)?|PRIVILEGED(?:\s+(?:AND|&)\s+CONFIDENTIAL)?"
    r"|AEO|RESTRICTED)\b"
)
STAMP_MAX_REMAINDER = 25   # chars left in the element after removing the stamp(s) and page tokens
_PAGE_TOKEN_RE = re.compile(r"\b(?:Page|PAGE|p\.)\s*\d+(?:\s*(?:of|OF|/)\s*\d+)?\b|\b\d{1,4}\b|[-–—|·•]")
_BATES_BLACKLIST = {"PAGE", "EXHIBIT", "EX", "NO", "CASE", "CAUSE", "DOC", "DOCUMENT", "ECF", "NYSCEF", "DKT", "ID", "PDF"}
# A number introduced by one of these is a docket/cause/case/invoice number,
# not a Bates stamp (e.g. "NO. DC-00-00000", "Case 4:23-cv-01234").
_BATES_CONTEXT_BLOCK_RE = re.compile(
    r"(?:\bNO\.?|\bNUMBER|\bCAUSE|\bCASE|\bCIVIL\s+ACTION|\bDOCKET|\bDKT\.?|\bINDEX|\bINVOICE|\bACCT\.?|\bACCOUNT|#)\s*$",
    re.I,
)


def _page_heights_pymupdf(pdf_path: Path, n: int):
    try:
        import fitz
        doc = fitz.open(str(_win_path(pdf_path)))
        hs = [doc[i].rect.height for i in range(min(n, doc.page_count))]
        doc.close()
        return hs
    except Exception:
        return [792.0] * n


def detect_stamps_from_odl(tree: dict, true_page_count: int, pdf_path: Path = None) -> dict:
    """
    Returns {page: {"bates": "ABC_000123" | None, "conf": "CONFIDENTIAL" | None}}
    for pages where at least one stamp was found in the top/bottom margin zone.
    ODL bounding boxes are [left, bottom, right, top] in PDF points, origin
    bottom-left.
    """
    stamps = {}
    if not tree:
        return stamps
    items = []
    _odl_walk(tree.get("kids", []), None, items)
    heights = _page_heights_pymupdf(pdf_path, true_page_count) if pdf_path else [792.0] * true_page_count
    for pg, typ, content, bbox in items:
        if not isinstance(pg, int) or not (1 <= pg <= true_page_count):
            continue
        text = " ".join(content.split())
        if not text or len(text) > STAMP_MAX_CHARS:
            continue
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        h = heights[pg - 1] if pg - 1 < len(heights) else 792.0
        bottom, top = bbox[1], bbox[3]
        in_zone = (bottom <= STAMP_ZONE_PT) or (top >= h - STAMP_ZONE_PT)
        if not in_zone:
            continue
        bates = conf = None
        remainder = text
        for m in _BATES_RE.finditer(text):
            prefix = m.group(1)
            if prefix.upper().rstrip("-_. ") in _BATES_BLACKLIST:
                continue
            if re.fullmatch(r"\d{1,2}", prefix):
                continue
            if _BATES_CONTEXT_BLOCK_RE.search(text[:m.start()]):
                continue
            bates = m.group(0).strip()
            remainder = remainder.replace(m.group(0), " ", 1)
            break
        m = _CONF_RE.search(text)
        if m:
            conf = " ".join(m.group(1).upper().split())
            remainder = remainder.replace(m.group(0), " ", 1)
        if bates is None and conf is None:
            continue
        # A stamp element is (almost) nothing but the stamp(s) and a page
        # token. Anything with real sentence text around it is body text.
        remainder = _PAGE_TOKEN_RE.sub(" ", remainder)
        if len("".join(remainder.split())) > STAMP_MAX_REMAINDER:
            continue
        entry = stamps.setdefault(pg, {"bates": None, "conf": None})
        if bates and entry["bates"] is None:
            entry["bates"] = bates
        if conf and entry["conf"] is None:
            entry["conf"] = conf
    return stamps


def stamp_suffix(entry: dict) -> str:
    parts = []
    if entry.get("bates"):
        parts.append(f"Bates {entry['bates']}")
    if entry.get("conf"):
        parts.append(entry["conf"])
    return " | ".join(parts)


_MARKER_MD_RE = re.compile(r"<!-- Page (\d+) of (\d+) -->")
_MARKER_TXT_RE = re.compile(r"=== PAGE (\d+) of (\d+) ===")


def apply_stamps_to_body(body: str, stamps: dict) -> str:
    """Append ' | Bates X | CONFIDENTIAL' to each page marker that has a stamp."""
    if not stamps:
        return body

    def _md(m):
        e = stamps.get(int(m.group(1)))
        return m.group(0) if not e else f"<!-- Page {m.group(1)} of {m.group(2)} | {stamp_suffix(e)} -->"

    def _txt(m):
        e = stamps.get(int(m.group(1)))
        return m.group(0) if not e else f"=== PAGE {m.group(1)} of {m.group(2)} | {stamp_suffix(e)} ==="

    body = _MARKER_MD_RE.sub(_md, body)
    body = _MARKER_TXT_RE.sub(_txt, body)
    return body


def bates_range_line(stamps: dict) -> str:
    """'BATES RANGE:       ABC_000123 – ABC_000162 (40 of 40 pages stamped)' or ''."""
    nums = [(pg, e["bates"]) for pg, e in sorted(stamps.items()) if e.get("bates")]
    if not nums:
        return ""
    first, last = nums[0][1], nums[-1][1]
    return f"{first} – {last} ({len(nums)} pages stamped)" if first != last else f"{first} (1 page stamped)"


def confidentiality_summary(stamps: dict) -> str:
    kinds = Counter(e["conf"] for e in stamps.values() if e.get("conf"))
    if not kinds:
        return ""
    return ", ".join(f"{k} ({n} pg)" for k, n in kinds.most_common())


# ── Bash OCR (tesseract) for image-only pages ────────────────────────────────
#
# Image-only pages (vendor TIFF-to-PDF productions, scans, signature pages)
# have no text layer for any extractor to read. Tesseract runs here as a
# bash subprocess: it costs ZERO Claude tokens — the token-expensive thing
# is Claude's Read tool rendering PDF pages as images, which this skill
# never does. Pages are rendered with PyMuPDF at OCR_DPI, tesseract writes
# txt + tsv (word confidences and boxes), and the result flows into the
# normal pipeline: page text, CONTENT GAPS (with an OCR-LOW-CONFIDENCE
# class), page markers, MANIFEST, and Bates/legend stamp capture (from the
# tesseract word boxes, converted to PDF points).

OCR_DPI = 300
OCR_MIN_ALNUM = 20          # fewer alnum chars than this after OCR -> still IMAGE-ONLY
OCR_LOW_CONF = 60.0         # mean word confidence below this -> OCR-LOW-CONFIDENCE gap
OCR_TIMEOUT_S = 180


OCR_BACKEND = None          # "cli" = tesseract executable; "pymupdf" = Tesseract compiled into PyMuPDF


def _tessdata_dir():
    """Folder holding <lang>.traineddata: $TESSDATA_PREFIX first, then tessdata/ beside this script."""
    for c in (os.environ.get("TESSDATA_PREFIX"), str(Path(__file__).resolve().parent / "tessdata")):
        if c and any(Path(c).glob("*.traineddata")):
            return c
    return None


def ocr_available():
    global OCR_BACKEND
    try:
        import fitz  # noqa: F401
    except ImportError:
        return False, "PyMuPDF not installed (needed to render pages for OCR)"
    if shutil.which("tesseract"):
        OCR_BACKEND = "cli"
        return True, ""
    if _tessdata_dir():
        OCR_BACKEND = "pymupdf"
        return True, ""
    return False, ("no tesseract executable in PATH and no tessdata folder found "
                   "(set TESSDATA_PREFIX to a folder containing eng.traineddata, "
                   "ship tessdata/ beside the script, or apt-get install -y tesseract-ocr)")


def _ocr_page_pymupdf(pdf_path: Path, page_index: int, lang: str = "eng"):
    """
    OCR one page with the Tesseract engine compiled into PyMuPDF. Needs no
    tesseract executable, so it works on locked-down Windows machines. Same
    return shape as ocr_page except conf is None (PyMuPDF exposes no word
    confidences) and line boxes are already in PDF points (scale 1.0).
    """
    import fitz
    try:
        doc = fitz.open(str(_win_path(pdf_path)))
        page = doc[page_index]
        h_pt = page.rect.height
        tp = page.get_textpage_ocr(flags=0, language=lang, dpi=OCR_DPI, full=True,
                                   tessdata=_tessdata_dir())
        text = page.get_text(textpage=tp)
        words = page.get_text("words", textpage=tp)
        doc.close()
    except Exception:
        return None
    grouped = {}
    for x0, y0, x1, y1, w, blk, ln, _ in words:
        g = grouped.setdefault((blk, ln), {"words": [], "l": x0, "t": y0, "r": x1, "b": y1})
        g["words"].append(w)
        g["l"] = min(g["l"], x0)
        g["t"] = min(g["t"], y0)
        g["r"] = max(g["r"], x1)
        g["b"] = max(g["b"], y1)
    lines = [(" ".join(g["words"]), None, g["l"], g["t"], g["r"] - g["l"], g["b"] - g["t"])
             for _, g in sorted(grouped.items())]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"text": text, "conf": None, "lines": lines, "page_height_pt": h_pt, "scale": 1.0}


def _tsv_lines(tsv_text: str):
    """Group tesseract TSV words into lines -> [(text, conf_mean, l, t, w, h)] in pixels."""
    rows = tsv_text.splitlines()
    if not rows:
        return []
    hdr = rows[0].split("\t")
    idx = {k: i for i, k in enumerate(hdr)}
    need = ("level", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text")
    if any(k not in idx for k in need):
        return []
    lines = {}
    order = []
    for r in rows[1:]:
        f = r.split("\t")
        if len(f) < len(hdr):
            continue
        try:
            if int(f[idx["level"]]) != 5:
                continue
            conf = float(f[idx["conf"]])
        except ValueError:
            continue
        word = f[idx["text"]].strip()
        if not word or conf < 0:
            continue
        key = (f[idx["block_num"]], f[idx["par_num"]], f[idx["line_num"]])
        l, t, w, h = (int(f[idx["left"]]), int(f[idx["top"]]), int(f[idx["width"]]), int(f[idx["height"]]))
        if key not in lines:
            lines[key] = {"words": [], "confs": [], "l": l, "t": t, "r": l + w, "b": t + h}
            order.append(key)
        L = lines[key]
        L["words"].append(word)
        L["confs"].append(conf)
        L["l"] = min(L["l"], l)
        L["t"] = min(L["t"], t)
        L["r"] = max(L["r"], l + w)
        L["b"] = max(L["b"], t + h)
    out = []
    for key in order:
        L = lines[key]
        out.append((" ".join(L["words"]), sum(L["confs"]) / len(L["confs"]),
                    L["l"], L["t"], L["r"] - L["l"], L["b"] - L["t"]))
    return out


def ocr_page(pdf_path: Path, page_index: int, lang: str = "eng"):
    """
    OCR one page (0-based) in bash. Returns dict:
      {"text": str, "conf": float|None, "lines": [(text, conf, l, t, w, h)],
       "page_height_pt": float, "scale": pt_per_px}
    or None on failure.
    """
    if OCR_BACKEND == "pymupdf":
        return _ocr_page_pymupdf(pdf_path, page_index, lang)
    import fitz
    import tempfile
    try:
        doc = fitz.open(str(_win_path(pdf_path)))
        page = doc[page_index]
        h_pt = page.rect.height
        pix = page.get_pixmap(dpi=OCR_DPI)
        with tempfile.TemporaryDirectory(prefix="cowork_ocr_") as td:
            png = Path(td) / "page.png"
            pix.save(str(png))
            base = Path(td) / "out"
            r = subprocess.run(
                ["tesseract", str(png), str(base), "-l", lang, "--psm", "3", "txt", "tsv"],
                capture_output=True, text=True, timeout=OCR_TIMEOUT_S,
            )
            if r.returncode != 0:
                doc.close()
                return None
            text = (base.with_suffix(".txt")).read_text(encoding="utf-8", errors="replace")
            tsv = (base.with_suffix(".tsv")).read_text(encoding="utf-8", errors="replace")
        doc.close()
    except Exception:
        return None
    lines = _tsv_lines(tsv)
    confs = [c for _, c, *_ in lines]
    conf = (sum(confs) / len(confs)) if confs else None
    # collapse 3+ blank lines; keep the PDF's own line structure otherwise
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"text": text, "conf": conf, "lines": lines,
            "page_height_pt": h_pt, "scale": 72.0 / OCR_DPI}


def ocr_result_to_odl_tree(results: dict) -> dict:
    """
    Build a minimal ODL-shaped element tree from tesseract lines so
    detect_stamps_from_odl can run unchanged on OCR'd pages.
    results: {page_number(1-based): ocr_page() dict}
    """
    kids = []
    for pg, res in results.items():
        if not res:
            continue
        sc = res["scale"]
        h = res["page_height_pt"]
        for text, conf, l, t, w, hh in res["lines"]:
            left = l * sc
            right = (l + w) * sc
            top = h - t * sc
            bottom = h - (t + hh) * sc
            kids.append({"type": "paragraph", "page number": pg, "content": text,
                         "bounding box": [round(left, 2), round(bottom, 2), round(right, 2), round(top, 2)]})
    return {"kids": kids}


def ocr_gap_pages(pdf_path: Path, pages: list, gaps: list, mode: str = "auto",
                  lang: str = "eng", workers: int = 2, true_page_count: int = 0):
    """
    OCR the pages that need it and splice the text into `pages`/`gaps`.
      mode 'auto'  : IMAGE-ONLY and PARTIAL pages only
      mode 'force' : every page (for garbage text layers)
      mode 'off'   : no-op
    Returns (pages, gaps, ocr_info) where ocr_info = {"pages": [n,...],
    "low_conf": [n,...], "still_empty": [n,...], "results": {n: ocr dict}}.
    """
    info = {"pages": [], "low_conf": [], "still_empty": [], "results": {}}
    if mode == "off":
        return pages, gaps, info
    ok, reason = ocr_available()
    if not ok:
        print(f"  OCR skipped: {reason}")
        return pages, gaps, info
    n_total = max(len(pages), true_page_count)
    # make sure the pages list covers the whole document
    while len(pages) < n_total:
        pages.append("")
    gap_by_page = {g["page"]: g for g in gaps}
    if mode == "force":
        targets = list(range(1, n_total + 1))
    else:
        targets = sorted(pg for pg, g in gap_by_page.items()
                         if g["type"] in ("IMAGE-ONLY", "PARTIAL", "EXTRACTION FAILED") and pg <= n_total)
    if not targets:
        return pages, gaps, info

    _eng = "PyMuPDF built-in tesseract" if OCR_BACKEND == "pymupdf" else "tesseract, bash"
    print(f"  OCR ({_eng}) on {len(targets)} page(s)...", end=" ", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for pg, res in zip(targets, ex.map(lambda p: ocr_page(pdf_path, p - 1, lang), targets)):
            results[pg] = res
    for pg in targets:
        res = results.get(pg)
        if not res or _alnum_len(res["text"]) < OCR_MIN_ALNUM:
            info["still_empty"].append(pg)
            continue
        pages[pg - 1] = res["text"]
        info["pages"].append(pg)
        info["results"][pg] = res
        gaps[:] = [g for g in gaps if g["page"] != pg]
        if res["conf"] is not None and res["conf"] < OCR_LOW_CONF:
            info["low_conf"].append(pg)
            gaps.append({"page": pg, "type": "OCR-LOW-CONFIDENCE", "chars": len(res["text"]),
                         "detail": f"OCR text (mean word confidence {res['conf']:.0f}%) — verify against the image."})
    gaps.sort(key=lambda g: g["page"])
    print(f"OK ({len(info['pages'])} pg OCR'd"
          + (f", {len(info['low_conf'])} low-confidence" if info["low_conf"] else "")
          + (f", {len(info['still_empty'])} still blank" if info["still_empty"] else "") + ")")
    return pages, gaps, info



# ── Working-copy preparation (password / page range) ─────────────────────────

def parse_page_spec(spec: str, max_page: int) -> list:
    """'1,3,5-7' -> [1, 3, 5, 6, 7], clipped to max_page, de-duplicated, sorted."""
    pages = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else max_page
            for p in range(a, b + 1):
                pages.add(p)
        else:
            pages.add(int(part))
    return sorted(p for p in pages if 1 <= p <= (max_page or 10 ** 9))


def prepare_working_pdf(pdf_path: Path, password: str = None, pages_spec: str = None):
    """
    Produce the PDF the pipeline actually reads. Handles two cases the
    extractors cannot on their own:
      - encrypted PDFs (--password): decrypted once into a temp copy
      - page ranges (--pages): a temp subset PDF
    Returns (work_path, page_map, source_total, tmpdir_handle, decrypted).
    page_map is None when the whole document is converted, else a list mapping
    output page index (1-based) -> original page number.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(_win_path(pdf_path)))
    if reader.is_encrypted:
        rc = 0
        try:
            rc = reader.decrypt(password if password is not None else "")
        except Exception as e:
            raise RuntimeError(f"PDF is encrypted and could not be opened: {e}")
        if not rc:
            if password is None:
                raise RuntimeError("PDF is password-protected — rerun with --password <pw>.")
            raise RuntimeError("Incorrect password for encrypted PDF.")
    source_total = len(reader.pages)

    page_map = None
    if pages_spec:
        page_map = parse_page_spec(pages_spec, source_total)
        if not page_map:
            raise RuntimeError(f"--pages '{pages_spec}' selects no pages (PDF has {source_total}).")
        if len(page_map) == source_total:
            page_map = None  # whole document after all

    if not reader.is_encrypted and page_map is None:
        return pdf_path, None, source_total, None, False

    import tempfile
    tmpdir = tempfile.TemporaryDirectory(prefix="cowork_work_")
    writer = PdfWriter()
    for p in (page_map or range(1, source_total + 1)):
        writer.add_page(reader.pages[p - 1])
    work_path = Path(tmpdir.name) / pdf_path.name
    with open(work_path, "wb") as fh:
        writer.write(fh)
    return work_path, page_map, source_total, tmpdir, bool(reader.is_encrypted)


_RELABEL_PATTERNS = [
    (re.compile(r"<!-- Page (\d+) of (\d+)( \| [^>]*?)? -->"), "<!-- Page {p} of {t}{x} -->"),
    (re.compile(r"=== PAGE (\d+) of (\d+)( \| .*?)? ==="), "=== PAGE {p} of {t}{x} ==="),
    (re.compile(r"CONTENT GAP — PAGE (\d+)"), "CONTENT GAP — PAGE {p}"),
    (re.compile(r"^(PAGE +)(\d+):", re.M), None),
    (re.compile(r"Review original PDF page (\d+)"), "Review original PDF page {p}"),
]


def relabel_pages(text: str, page_map: list, source_total: int) -> str:
    """Rewrite subset page numbers (1..k) in an output file to original page numbers."""
    if not page_map:
        return text

    def orig(n: int) -> int:
        return page_map[n - 1] if 1 <= n <= len(page_map) else n

    for rx, fmt in _RELABEL_PATTERNS:
        if fmt is None:
            text = rx.sub(lambda m: f"{m.group(1)}{orig(int(m.group(2)))}:", text)
        elif "{t}" in fmt:
            text = rx.sub(lambda m: fmt.format(p=orig(int(m.group(1))), t=source_total,
                                               x=(m.group(3) or "")), text)
        else:
            text = rx.sub(lambda m: fmt.format(p=orig(int(m.group(1)))), text)
    return text


def compact_page_spec(page_map: list) -> str:
    """[5,6,7,10] -> '5-7,10'"""
    if not page_map:
        return ""
    out = []
    start = prev = page_map[0]
    for p in page_map[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = p
    out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def _strip_hf_from_md(md_pages, hf_data):
    """
    Strip detected repeating header/footer lines from pymupdf4llm output,
    reusing the legacy hf detection (fuzzy page-number matching). Table rows
    (lines starting with '|') are never stripped.
    """
    if not hf_data or not (hf_data.get("header_texts") or hf_data.get("footer_texts")):
        return md_pages
    out = []
    for page in md_pages:
        kept = []
        for line in page.splitlines():
            probe = line.strip().lstrip("#").strip().strip("*_").strip()
            if probe and not line.lstrip().startswith("|") and _is_header_footer(probe, hf_data):
                continue
            kept.append(line)
        out.append("\n".join(kept))
    return out


def _strip_hf_from_raw(raw: str, hf_data) -> str:
    """Drop detected repeating header/footer lines from a raw-text page."""
    if not raw or not hf_data or not (hf_data.get("header_texts") or hf_data.get("footer_texts")):
        return raw
    kept = [l for l in raw.splitlines() if not (l.strip() and _is_header_footer(l.strip(), hf_data))]
    return "\n".join(kept).strip()


def build_page_text_md4llm(md_pages, raw_pages, gaps, hf_data=None) -> str:
    """
    Body for .md output from a structured engine (opendataloader-pdf or
    pymupdf4llm). Preserves the CONTENT GAP flag contract and
    <!-- Page N of M --> markers; any page where the engine returned nothing
    falls back to that page's raw extracted text (headers/footers stripped).
    """
    total = len(raw_pages)
    gap_pages = {g["page"]: g for g in gaps}
    sections = []
    for i in range(1, total + 1):
        header = f"<!-- Page {i} of {total} -->"
        raw = raw_pages[i - 1] if i <= len(raw_pages) else ""
        raw = _strip_hf_from_raw(raw, hf_data)
        md = md_pages[i - 1] if i <= len(md_pages) else ""
        if i in gap_pages:
            gap = gap_pages[i]
            if gap["type"] == "IMAGE-ONLY":
                body = (f"[CONTENT GAP — PAGE {i}: No extractable text — image/scan/signature page]\n"
                        f"[→ Review original PDF for this page]")
            elif gap["type"] == "PARTIAL":
                content = md if _alnum_len(md) >= _alnum_len(raw) else raw
                body = (f"[CONTENT GAP — PAGE {i}: Partial extraction ({gap['chars']} chars). "
                        f"Content below may be incomplete]\n"
                        f"[→ Review original PDF for full content]\n\n{content}")
            elif gap["type"] == "OCR-LOW-CONFIDENCE":
                body = (f"[CONTENT GAP — PAGE {i}: {gap['detail']}]\n"
                        f"[→ Verify against original PDF image]\n\n{raw}")
            else:
                body = (f"[CONTENT GAP — PAGE {i}: Extraction failed]\n"
                        f"[→ Review original PDF for this page]")
        elif md.strip():
            body = md
        else:
            body = raw
        sections.append(f"{header}\n\n{body}")
    grand_total = max([total] + [g["page"] for g in gaps])
    for gap in gaps:
        if gap["page"] > total:
            header = f"<!-- Page {gap['page']} of {grand_total} -->"
            body = (f"[CONTENT GAP — PAGE {gap['page']}: Page exists in PDF but extraction failed]\n"
                    f"[→ Review original PDF for this page]")
            sections.append(f"{header}\n\n{body}")
    return "\n\n".join(sections)


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
    # pdftotext terminates EVERY page with \f, so the split leaves one extra
    # empty piece at the end — drop that artifact only. Leading empty pages
    # are real (image-only scans) and must be kept and flagged; skipping
    # them used to make an all-image production report "0 pages /
    # EXTRACTION FAILED" instead of N IMAGE-ONLY pages.
    if raw_pages and not raw_pages[-1].strip():
        raw_pages.pop()
    pages = []
    gaps = []

    for raw_page in raw_pages:
        stripped = raw_page.strip()
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

    # Trailing empty pages are real pages (image-only) and stay flagged;
    # only the split artifact was removed above.
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


def build_file_header(pdf_path: Path, method: str, validation: dict, output_format: str,
                      engine: str = None, extra_lines: list = None,
                      source_total: int = None) -> str:
    """Build the full header: file info + content gaps + notes."""
    total = max(validation["true_page_count"], validation["extracted_count"])
    format_label = "Markdown (.md)" if output_format == "md" else "Plain text (.txt)"
    lines = [
        SEPARATOR,
        f"FILE:              {pdf_path.name}",
        f"SOURCE:            {pdf_path}",
        f"EXTRACTION METHOD: {method}",
        f"OUTPUT FORMAT:     {format_label}",
        f"TOTAL PAGES:       {source_total if source_total else total}",
    ]
    for l in (extra_lines or []):
        lines.append(l)
    lines += [
        f"CONVERTED:         {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        SEPARATOR,
        "",
        build_content_gaps_block(validation, output_format),
        "",
        SEPARATOR,
    ]

    if output_format == "md" and engine == "odl":
        lines.append("NOTE: Structure (headings, lists, tables, ~~strikethrough~~) from opendataloader-pdf")
        lines.append("      (XY-Cut++ reading order, local Java mode, no OCR). Off-page/hidden text")
        lines.append("      filtered; repeating headers/footers stripped. Inline bold/italic NOT marked.")
        lines.append("      Bates numbers / confidentiality legends, when found, are written into each")
        lines.append("      page marker: <!-- Page N of M | Bates X | CONFIDENTIAL -->.")
        lines.append("      Image-only pages are OCR'd with tesseract (see OCR PAGES); [OCR-LOW-CONFIDENCE]")
        lines.append("      gaps carry text that should be checked against the image.")
        lines.append("      [CONTENT GAP] markers indicate pages requiring original PDF review.")
    elif output_format == "md":
        lines.append("NOTE: Headings, bold, and italic are inferred from PDF font metadata.")
        lines.append("      Caption blocks are rendered as blockquotes (> ).")
        lines.append("      [CONTENT GAP] markers indicate pages requiring original PDF review.")
    elif engine == "odl":
        lines.append("NOTE: Text in reading order from opendataloader-pdf (XY-Cut++): multi-column")
        lines.append("      condensed transcripts are linearized column by column, original line")
        lines.append("      breaks and running headers/footers kept. All content is verbatim.")
        lines.append("      Bates numbers / confidentiality legends, when found, are written into each")
        lines.append("      page marker: === PAGE N of M | Bates X | CONFIDENTIAL ===.")
        lines.append("      Image-only pages are OCR'd with tesseract (see OCR PAGES); [OCR-LOW-CONFIDENCE]")
        lines.append("      gaps carry text that should be checked against the image.")
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
            elif gap["type"] == "OCR-LOW-CONFIDENCE":
                body = f"[CONTENT GAP — PAGE {i}: {gap['detail']}]\n[→ Verify against original PDF image]\n\n{text}"
            else:
                body = f"[CONTENT GAP — PAGE {i}: Extraction failed]\n[→ Review original PDF for this page]"
        else:
            body = text
        sections.append(f"{header}\n\n{body}")

    grand_total = max([total] + [g["page"] for g in gaps])
    for gap in gaps:
        if gap["page"] > total:
            header = f"{SEPARATOR}\n=== PAGE {gap['page']} of {grand_total} ===\n{SEPARATOR}"
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
            elif gap["type"] == "OCR-LOW-CONFIDENCE":
                body = f"[CONTENT GAP — PAGE {i}: {gap['detail']}]\n[→ Verify against original PDF image]\n\n{text}"
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

    grand_total = max([total] + [g["page"] for g in gaps])
    for gap in gaps:
        if gap["page"] > total:
            header = f"<!-- Page {gap['page']} of {grand_total} -->"
            body = f"[CONTENT GAP — PAGE {gap['page']}: Page exists in PDF but extraction failed]\n[→ Review original PDF for this page]"
            sections.append(f"{header}\n\n{body}")

    return "\n\n".join(sections)


# ── Manifest ─────────────────────────────────────────────────────────────────

def update_manifest(manifest_path: Path, pdf_name: str, method: str, validation: dict, output_format: str,
                    ocr_pages=None):
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

    if ocr_pages:
        entry_lines.append(f"- OCR: {len(ocr_pages)} page(s) via tesseract ({compact_page_spec(sorted(ocr_pages))})")
    entry_lines.append(f"- Completeness: {validation['completeness_pct']}%")
    entry_lines.append("")

    entry_block = "\n".join(entry_lines)

    if manifest_path.exists():
        existing = manifest_path.read_text(encoding="utf-8")
        # Match the heading as a whole line so "## X.pdf" never swallows
        # "## X.pdf [pages 2-3]" (or vice versa).
        m = re.search(rf"^## {re.escape(pdf_name)}[ \t]*$", existing, re.M)
        if m:
            before = existing[:m.start()]
            after_rest = existing[m.end():]
            nm = re.search(r"^## ", after_rest, re.M)
            after = after_rest[nm.start():] if nm else ""
            new_content = before + entry_block + ("\n" if after else "") + after
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
    md_engine: str = "odl",
    txt_engine: str = "odl",
    password: str = None,
    pages_spec: str = None,
    odl_opts: dict = None,
) -> tuple[Path, dict]:
    """
    Convert a single PDF to .md or .txt with full validation.
    Returns (output_path, validation_report).
    """
    if not _win_path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Working copy: decrypt (--password) and/or subset (--pages) once, so every
    # extractor downstream reads a plain, complete-for-its-purpose PDF.
    src_path = pdf_path
    work_path, page_map, source_total, _tmp, decrypted = prepare_working_pdf(pdf_path, password, pages_spec)
    if work_path is not pdf_path:
        what = []
        if decrypted:
            what.append("decrypted")
        if page_map:
            what.append(f"pages {compact_page_spec(page_map)} of {source_total}")
        print(f"  Working copy: {', '.join(what) or 'prepared'}")
    pdf_path = work_path

    # Determine format
    output_format = determine_format(pdf_path, requested_format)
    suffix = f"_COWORK.{output_format}"
    if page_map:
        suffix = f"_p{compact_page_spec(page_map).replace(',', '_')}{suffix}"

    # Determine output location (always relative to the ORIGINAL file)
    if output_path:
        out_path = output_path
    elif cowork_subfolder:
        subfolder = src_path.parent / "_cowork_txt"
        subfolder.mkdir(exist_ok=True)
        out_path = subfolder / f"{src_path.stem}{suffix}"
    else:
        out_path = src_path.with_name(f"{src_path.stem}{suffix}")

    extra_header = []
    if page_map:
        extra_header.append(f"PAGE RANGE:        {compact_page_spec(page_map)} (of {source_total} in source PDF)")

    def _finish(full_text: str) -> str:
        return relabel_pages(full_text, page_map, source_total) if page_map else full_text

    # Get true page count (of the working copy)
    true_page_count = get_pdf_page_count(pdf_path)
    print(f"  PDF page count: {true_page_count}")
    print(f"  Output format: .{output_format}")

    # Prefer a DISCO native-text sidecar when present: it is letter-perfect
    # (extracted from the native file, not OCR of a scanned image).
    # (Sidecars are looked up next to the ORIGINAL file, whole-document only.)
    sidecar_path, sidecar_txt = (None, None) if page_map else find_native_txt_sidecar(src_path)
    if sidecar_txt is not None:
        pdf_path = src_path
        print(f"  Using DISCO native text sidecar: {sidecar_path.name}")
        validation = _build_native_validation(source_total or true_page_count)
        header = build_native_header(pdf_path, sidecar_path, source_total or true_page_count, output_format)
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
            try:
                import fitz  # noqa: F401
                print("SKIP (no text layer found — image-only PDF?)")
            except ImportError:
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

            # Remove duplicate text-layer doubling from the extracted text.
            # (pdftotext -layout emits both layers on each row; this also cleans
            #  the .txt path and the .md fallback.) No-op on non-doubled pages.
            _dbl = 0
            for _pi in range(len(pages)):
                _new, _ch = dedupe_page_text(pages[_pi])
                if _ch:
                    pages[_pi] = _new
                    _dbl += 1
            if _dbl:
                print(f"[text-layer dedup: {_dbl} pg]", end=" ", flush=True)
            print()

            # Bash OCR for image-only / partial pages (zero Claude tokens).
            _oo = odl_opts or {}
            pages, gaps, ocr_info = ocr_gap_pages(
                pdf_path, pages, gaps, mode=_oo.get("ocr", "auto"),
                lang=_oo.get("ocr_lang", "eng"), workers=int(_oo.get("ocr_workers", 2) or 2),
                true_page_count=true_page_count)
            ocr_pages = set(ocr_info["pages"])
            if ocr_pages:
                _via = " via PyMuPDF, no confidence scores" if OCR_BACKEND == "pymupdf" else ""
                method_name = f"{method_name} + tesseract OCR ({len(ocr_pages)} pg{_via})"

            # Validate
            validation = validate_extraction(pages, gaps, true_page_count)

            # Build output (body first so the structured engine can annotate
            # method_name). Engine chains:
            #   .md : opendataloader-pdf -> pymupdf4llm -> legacy font renderer -> plain
            #   .txt: opendataloader-pdf -> pdftotext -layout
            engine_used = None
            odl_cache = {}
            if output_format == "md":
                body = None
                if md_engine == "odl":
                    print("  Trying opendataloader-pdf Markdown engine...", end=" ", flush=True)
                    odl_pages = extract_pages_odl(pdf_path, true_page_count, "md", odl_opts, odl_cache)
                    if md_engine_quality_ok(odl_pages, pages, "opendataloader-pdf", ocr_pages):
                        odl_pages = _strip_hf_from_md(odl_pages, hf_data)
                        body = build_page_text_md4llm(odl_pages, pages, validation["gaps"], hf_data)
                        method_name = f"{method_name} + opendataloader-pdf {odl_version() or ''}".rstrip()
                        engine_used = "odl"
                        print("OK")
                    else:
                        print("FALLBACK -> pymupdf4llm")
                if body is None and md_engine != "legacy":
                    print("  Trying pymupdf4llm Markdown engine...", end=" ", flush=True)
                    md_pages = extract_md_pages_pymupdf4llm(pdf_path, true_page_count)
                    if md_engine_quality_ok(md_pages, pages, "pymupdf4llm", ocr_pages):
                        md_pages = _strip_hf_from_md(md_pages, hf_data)
                        body = build_page_text_md4llm(md_pages, pages, validation["gaps"], hf_data)
                        method_name = f"{method_name} + pymupdf4llm"
                        engine_used = "pymupdf4llm"
                        print("OK")
                    else:
                        print("FALLBACK -> legacy font-metadata renderer")
                if body is None and meta:
                    body = build_page_text_md(pages, validation["gaps"], meta, hf_data)
                    engine_used = "legacy"
                if body is None:
                    body = build_page_text_txt(pages, validation["gaps"])
            else:
                body = None
                if txt_engine == "odl":
                    print("  Trying opendataloader-pdf text engine...", end=" ", flush=True)
                    odl_pages = extract_pages_odl(pdf_path, true_page_count, "txt", odl_opts, odl_cache)
                    if md_engine_quality_ok(odl_pages, pages, "opendataloader-pdf", ocr_pages):
                        merged = [(o if o.strip() else r) for o, r in zip(odl_pages, pages)]
                        body = build_page_text_txt(merged, validation["gaps"])
                        method_name = f"{method_name} + opendataloader-pdf {odl_version() or ''}".rstrip()
                        engine_used = "odl"
                        print("OK")
                    else:
                        print("FALLBACK -> pdftotext -layout")
                if body is None:
                    body = build_page_text_txt(pages, validation["gaps"])

            # Bates / confidentiality stamps from the ODL element tree (bounding
            # boxes). Reuses the JSON from the engine run; if ODL was not the
            # engine, one JSON-only run is made when the engine is available.
            stamps = {}
            if not (odl_opts or {}).get("no_stamps"):
                run = odl_cache.get("odl_run")
                if run is None and "odl_run" not in odl_cache and odl_available()[0]:
                    print("  Reading element tree for stamps...", end=" ", flush=True)
                    run = run_odl(pdf_path, "txt", odl_opts)
                    print("OK" if run else "")
                if run and run.get("json"):
                    stamps = detect_stamps_from_odl(run["json"], true_page_count, pdf_path)
                if ocr_info["results"]:
                    ocr_stamps = detect_stamps_from_odl(ocr_result_to_odl_tree(ocr_info["results"]),
                                                        true_page_count, None)
                    for pg, e in ocr_stamps.items():
                        stamps.setdefault(pg, e)
                if stamps:
                    body = apply_stamps_to_body(body, stamps)
                    br = bates_range_line(stamps)
                    cs = confidentiality_summary(stamps)
                    if br:
                        extra_header.append(f"BATES RANGE:       {br}")
                    if cs:
                        extra_header.append(f"CONFIDENTIALITY:   {cs}")
                    print(f"  Stamps captured on {len(stamps)} page(s)"
                          + (f" — Bates {br}" if br else "") + (f" — {cs}" if cs else ""))

            if ocr_pages:
                extra_header.append(f"OCR PAGES:         {compact_page_spec(sorted(ocr_pages))}"
                                    f" ({'PyMuPDF built-in tesseract, no word confidences' if OCR_BACKEND == 'pymupdf' else 'tesseract'},"
                                    f" {OCR_DPI} dpi; text is OCR, not native)")
            header = build_file_header(src_path, method_name, validation, output_format,
                                       engine=engine_used, extra_lines=extra_header,
                                       source_total=source_total)

            full_text = _finish(header + body)

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

            # Update manifest (page-range runs get their own entry)
            manifest_path = out_path.parent / "MANIFEST.md"
            manifest_key = src_path.name + (f" [pages {compact_page_spec(page_map)}]" if page_map else "")
            update_manifest(manifest_path, manifest_key, method_name, validation, output_format,
                            ocr_pages=ocr_pages)

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
    md_engine: str = "odl",
    txt_engine: str = "odl",
    password: str = None,
    odl_opts: dict = None,
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
                md_engine=md_engine,
                txt_engine=txt_engine,
                password=password,
                odl_opts=odl_opts,
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
    parser.add_argument(
        "--md-engine",
        choices=["odl", "pymupdf4llm", "legacy"],
        default=os.environ.get("COWORK_MD_ENGINE", "odl"),
        help="Structured engine for .md output. Chain: odl (opendataloader-pdf, "
             "default) -> pymupdf4llm -> legacy font-metadata renderer. Each "
             "engine is quality-gated against the pdftotext reference and falls "
             "through automatically. Env override: COWORK_MD_ENGINE.",
    )
    parser.add_argument(
        "--txt-engine",
        choices=["odl", "pdftotext"],
        default=os.environ.get("COWORK_TXT_ENGINE", "odl"),
        help="Engine for .txt output. odl (default): opendataloader-pdf reading "
             "order, line breaks and headers/footers kept; pdftotext: the "
             "side-by-side -layout rendering. Env override: COWORK_TXT_ENGINE.",
    )
    parser.add_argument(
        "--password", "-p",
        default=None,
        help="Password for an encrypted PDF (decrypted once into a temp working copy)",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Convert only these pages, e.g. '1,3,5-7' (single-file mode). Output "
             "is named <stem>_p<range>_COWORK.* and page markers keep the "
             "ORIGINAL page numbers.",
    )
    odl = parser.add_argument_group(
        "opendataloader-pdf options",
        "Pass-throughs to the local Java engine. The hybrid/AI backend (OCR, VLM) "
        "is deliberately not exposed — see the skill's No-OCR rule.",
    )
    odl.add_argument("--use-struct-tree", action="store_true",
                     help="Use the PDF's own structure tags (tagged PDFs) for reading order "
                          "and headings. Off by default: quality depends on the producer's tags.")
    odl.add_argument("--table-method", choices=["default", "cluster"], default=None,
                     help="Table detection: default (ruled borders) or cluster (borders + "
                          "whitespace clustering, catches borderless tables)")
    odl.add_argument("--sanitize", action="store_true",
                     help="Replace emails, phone numbers, IPs, credit-card numbers and URLs "
                          "with placeholders in the output (NOT verbatim — opt in only)")
    odl.add_argument("--content-safety-off", default=None, metavar="LIST",
                     help="Disable content-safety filters: all, hidden-text, off-page, tiny, "
                          "hidden-ocg, background (comma-separated). Default: all filters ON.")
    odl.add_argument("--threads", type=int, default=1,
                     help="Worker threads for per-page processing (default 1; >1 experimental)")
    odl.add_argument("--no-stamps", action="store_true",
                     help="Do not capture Bates numbers / confidentiality legends into page markers")
    ocr = parser.add_argument_group(
        "OCR (tesseract in bash — zero Claude tokens)",
        "Image-only and near-empty pages are OCR'd automatically. Text is marked as OCR in the "
        "header, MANIFEST and (when confidence is low) the CONTENT GAPS block.",
    )
    ocr.add_argument("--ocr", choices=["auto", "off", "force"], default=os.environ.get("COWORK_OCR", "auto"),
                     help="auto (default): OCR IMAGE-ONLY/PARTIAL pages; off: never; force: OCR every page "
                          "(replaces a garbage text layer). Env override: COWORK_OCR.")
    ocr.add_argument("--ocr-lang", default="eng", help="tesseract language(s), e.g. eng or eng+spa (default eng)")
    ocr.add_argument("--ocr-workers", type=int, default=2,
                     help="Parallel tesseract processes (default 2)")

    args = parser.parse_args()
    target = Path(args.input)
    use_subfolder = not args.no_subfolder
    odl_opts = {
        "use_struct_tree": args.use_struct_tree,
        "table_method": args.table_method,
        "sanitize": args.sanitize,
        "content_safety_off": args.content_safety_off,
        "threads": args.threads,
        "no_stamps": args.no_stamps,
        "ocr": args.ocr,
        "ocr_lang": args.ocr_lang,
        "ocr_workers": args.ocr_workers,
    }
    if args.pages and target.is_dir():
        print("Error: --pages applies to single-file mode only.", file=sys.stderr)
        sys.exit(2)

    if target.is_dir():
        results = convert_directory(
            target,
            force_method=args.method,
            skip_existing=args.skip_existing,
            cowork_subfolder=use_subfolder,
            requested_format=args.format,
            md_engine=args.md_engine,
            txt_engine=args.txt_engine,
            password=args.password,
            odl_opts=odl_opts,
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
            md_engine=args.md_engine,
            txt_engine=args.txt_engine,
            password=args.password,
            pages_spec=args.pages,
            odl_opts=odl_opts,
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
