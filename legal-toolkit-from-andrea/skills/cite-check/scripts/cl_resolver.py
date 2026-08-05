"""
cl_resolver.py -- CourtListener opinion resolver for the cite-check pipeline
(Project: Isaacus Integration, Step 2).

Resolves Citation objects to full opinion text via the CourtListener API v4.
Designed as the production callback for cite_check()'s resolve_opinion_text
parameter, replacing the manual opinions JSON from Step 1.

Two modes:
    * Per-citation: resolve_opinion_text(citation) -- called inside the
      cite_check() loop for each Citation.
    * Batch pre-resolution: batch_resolve(brief_text) -- POSTs the full
      brief to the citation-lookup endpoint before the per-citation loop.
      Results are cached; per-citation calls check the cache first.

Credential discipline:
    * CL_CONFIG.txt is read via Path.read_text() only.
    * The token is never echoed, printed, logged, or written anywhere.
    * No shell commands touch the credential file.
"""
from __future__ import annotations

import html as html_mod
import logging
import os
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from cite_check import Citation, _has_opinion_disposition

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
BASE_URL = "https://www.courtlistener.com/api/rest/v4"
SEARCH_URL_TEMPLATE = "https://www.courtlistener.com/?q={query}&type=o"
WEB_BASE = "https://www.courtlistener.com"

# ---- Chunk 3: substantive sub-opinion selection (lower rank == preferred) ----
_OPINION_TYPE_RANK = {
    "010combined": 0, "015unamimous": 1, "020lead": 1, "025plurality": 2,
    "080onthemerits": 2, "030concurrence": 4, "035concurrenceinpart": 4,
    "040dissent": 5, "050addendum": 6, "060remittitur": 7, "070rehearing": 7,
    "090onmotiontostrike": 8,
}
_DEFAULT_TYPE_RANK = 3
_TRIM_THRESHOLD = 28000
_PINCITE_WINDOW = 9000
_HEAD_TRIM = 24000
_HEAD_KEEP = 1500  # caption+reporter slice prepended to a pincite window

# Rate limiting (mirrors cl_api.py)
_RATE_LIMIT = 5000
_RATE_WINDOW = 3600
_BACKOFF_THRESHOLD = 4800

# citation-lookup batch limits (documented API caps, Phase 2 2026.07.04)
_LOOKUP_MAX_CITES = 250     # cites per request
_LOOKUP_MAX_CHARS = 64000   # chars per request
_LOOKUP_PACE_CITES = 60     # valid cites per rolling minute

# --------------------------------------------------------------------------
# Jurisdiction mapping: Isaacus enricher codes -> CourtListener court IDs
# --------------------------------------------------------------------------
_JURISDICTION_TO_CL_COURTS: Dict[str, List[str]] = {
    # Texas
    "US-TX": (
        ["tex", "texcrimapp", "texjpml", "texreview", "texag"]
        + [f"texapp{i}" for i in range(1, 15)]
        # CourtListener's LIVE ids for the Texas Courts of Appeals are
        # txctapp1-14 (verified against the API 2026.08.04); the texapp*
        # spellings above never matched a real result and hard-rejected
        # every Texas COA name-tier candidate (Traweek/Cantrell/Smith).
        + [f"txctapp{i}" for i in range(1, 15)]
    ),
    # Federal circuit courts
    "US-CA1": ["ca1"],
    "US-CA2": ["ca2"],
    "US-CA3": ["ca3"],
    "US-CA4": ["ca4"],
    "US-CA5": ["ca5"],
    "US-CA6": ["ca6"],
    "US-CA7": ["ca7"],
    "US-CA8": ["ca8"],
    "US-CA9": ["ca9"],
    "US-CA10": ["ca10"],
    "US-CA11": ["ca11"],
    "US-CADC": ["cadc"],
    "US-CAFC": ["cafc"],
    # SCOTUS
    "US-SCOTUS": ["scotus"],
    "US": ["scotus"],  # Enricher sometimes gives bare "US" for federal
    # New York
    "US-NY": [
        "ny", "nyappdiv", "nysupct", "nyappterm", "nyfamct",
        "nysurct", "nycivct", "nycrimct",
    ],
    # Florida
    "US-FL": (
        ["fla", "fladistctapp"]
        + [f"flaapp{i}" for i in range(1, 6)]
    ),
    # California
    "US-CA": [
        "cal", "calctapp", "calag",
    ],
    # Delaware
    "US-DE": ["del", "delch", "delsuperct", "delctcompl", "delfamct"],
    # Federal district courts (common ones)
    "US-NDTX": ["txnd"],
    "US-SDTX": ["txsd"],
    "US-EDTX": ["txed"],
    "US-WDTX": ["txwd"],
    "US-SDNY": ["nysd"],
    "US-EDNY": ["nyed"],
    "US-NDIL": ["ilnd"],
    "US-CDCA": ["cacd"],
    "US-DDC": ["dcd"],
    "US-DDE": ["ded"],
}

# Reverse mapping: CL court_id -> set of enricher jurisdiction codes that match
_CL_COURT_TO_JURISDICTIONS: Dict[str, List[str]] = {}
for _jur, _courts in _JURISDICTION_TO_CL_COURTS.items():
    for _court in _courts:
        _CL_COURT_TO_JURISDICTIONS.setdefault(_court, []).append(_jur)


def jurisdiction_matches(enricher_jurisdiction: Optional[str], cl_court_id: str) -> bool:
    """Check if a CourtListener court_id is compatible with the enricher's jurisdiction code.

    Generous matching: "US-TX" matches tex, texapp1-14, texcrimapp, etc.
    If no enricher jurisdiction is provided, accept any court (can't filter).
    """
    if not enricher_jurisdiction:
        return True  # Can't filter without jurisdiction info

    jur = enricher_jurisdiction.upper().strip()

    # "US-FED" means any federal court — accept everything
    if jur in ("US-FED", "US-FEDERAL", "FEDERAL"):
        return True

    allowed_courts = _JURISDICTION_TO_CL_COURTS.get(jur, [])
    if not allowed_courts:
        # Unknown jurisdiction code -- try prefix matching.
        # E.g., "US-TX-APP" should still match Texas appellate courts.
        for known_jur, courts in _JURISDICTION_TO_CL_COURTS.items():
            if jur.startswith(known_jur) or known_jur.startswith(jur):
                allowed_courts.extend(courts)
        if not allowed_courts:
            return True  # Unknown jurisdiction, can't filter -- accept

    cl_id = cl_court_id.lower().strip().rstrip("/")
    # Extract just the court slug from a full URL if needed
    # e.g., "https://www.courtlistener.com/api/rest/v4/courts/tex/" -> "tex"
    if "/" in cl_id:
        cl_id = cl_id.rstrip("/").rsplit("/", 1)[-1]

    if cl_id in allowed_courts:
        return True
    # 2026.08.04 (Traweek/Cantrell/Smith class): this map is hand-typed and
    # provably incomplete -- CL's real Texas COA ids (txctapp*) were absent,
    # so every correct candidate was HARD-rejected into an UNABLE. An id
    # this map has never seen must not be treated as a cross-jurisdiction
    # mismatch: fail OPEN and let the name-overlap and cite-address
    # identity gates decide. An id the map DOES know (i.e., mapped to some
    # jurisdiction, just not this one) still rejects.
    _known = set()
    for _courts in _JURISDICTION_TO_CL_COURTS.values():
        _known.update(_courts)
    return cl_id not in _known


# --------------------------------------------------------------------------
# Case name parsing
# --------------------------------------------------------------------------
def parse_case_name(name: str) -> Tuple[str, str]:
    """Split a case name into (party1, party2) on 'v.' or 'v'.

    Returns (full_name, "") if no versus separator is found.
    """
    # Try " v. " first, then " v "
    for sep in [" v. ", " v ", " vs. ", " vs "]:
        idx = name.lower().find(sep)
        if idx >= 0:
            p1 = name[:idx].strip().rstrip(",")
            p2 = name[idx + len(sep):].strip().rstrip(",")
            # Strip parenthetical and reporter from party2
            p2 = re.sub(r",\s+\d+\s+\S+\s+\d+.*$", "", p2)
            p2 = re.sub(r"\s*\([^)]*\)\s*$", "", p2)
            return p1.strip(), p2.strip()
    return name.strip(), ""


def _build_search_query(citation: Citation) -> str:
    """Build a CourtListener search query from a Citation object.

    Prefers the full TOA-matched case name over the raw body name so a
    short-cite ("Simmons") searches its full form ("Simmons v. Lightfoot")
    rather than the bare surname (locked spec, Resolver section; item 2).
    """
    src = ""
    if getattr(citation, "toa_match", None) and citation.toa_match.get("name"):
        src = citation.toa_match["name"]
    if not src:
        src = citation.name or ""
    src = src.replace("*", "").strip()
    p1, p2 = parse_case_name(src)
    if p2:
        # Use both party names for better precision
        return f"{p1} {p2}"
    return p1


def build_search_url(citation: Citation) -> str:
    """Build a human-clickable CourtListener search URL for a citation."""
    query = _build_search_query(citation)
    return SEARCH_URL_TEMPLATE.format(query=quote_plus(query))


# --------------------------------------------------------------------------
# Tiered structured-lookup helpers
#
# CourtListener's /search/ endpoint exposes structured fields that are far
# more precise than the fuzzy ``q=`` blob:
#   * ``citation=`` -- reporter-cite lookup (nearly unique; defeats surname
#     collisions like "Beal" -> "Rodriguez v. Beal").
#   * ``case_name=`` -- AND-of-tokens match on the party names.
# Because case_name is an AND of tokens, a single mismatched abbreviation
# ("Glob." vs the stored "Global") zeroes the whole query, so we also try a
# reduced query of just the most distinctive token from each party.
# --------------------------------------------------------------------------
_REPORTER_DATE_PAREN_RE = re.compile(r"\s*\([^)]*\d{4}\)\s*$")

# Tokens that carry no disambiguating signal in a party name.
_NAME_STOPWORDS = {
    "v", "vs", "the", "of", "in", "re", "matter", "and", "a", "an",
    "co", "cos", "inc", "llc", "lp", "llp", "na", "nv", "sa", "fsb",
    "corp", "ltd", "company", "bank", "assocs", "assoc", "associates",
    "serv", "servs", "services", "grp", "group", "holdings",
    "fund", "master", "intl", "intern", "international", "natl", "national",
}


def _reporter_only(cite: str) -> str:
    """Strip the trailing court/date parenthetical from a reporter cite,
    keeping any slip-op designator like '(U)'.

    "208 A.D.3d 423 (1st Dept. 2022)"             -> "208 A.D.3d 423"
    "2025 N.Y. Slip Op. 50534(U) (Sup. Ct. 2025)" -> "2025 N.Y. Slip Op. 50534(U)"
    """
    if not cite:
        return ""
    return _REPORTER_DATE_PAREN_RE.sub("", cite).strip()


def _clean_case_name(citation: Citation) -> str:
    """Return 'party1 party2' with parenthetical/reporter stripped.

    Prefers the TOA-matched name (cleaner than the raw body name).
    """
    if citation.toa_match and citation.toa_match.get("name"):
        src = citation.toa_match["name"]
    else:
        src = citation.name or ""
    src = src.replace("*", "").strip()
    p1, p2 = parse_case_name(src)
    return (f"{p1} {p2}".strip() if p2 else p1).strip()


def _name_tokens(name: str) -> set:
    """Lowercase alphanumeric tokens minus corporate/structural stopwords."""
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {t for t in toks if t not in _NAME_STOPWORDS and len(t) > 1}


def _tokens_subsumed(want: set, have: set) -> bool:
    """F2/M4 subset test (2026.07.14): every WANT token appears in HAVE,
    where an abbreviated token ('dev', 'assocs') counts as matched when it
    prefixes a HAVE token ('development', 'associates'). eyecite names carry
    Bluebook abbreviations; cluster captions are usually expanded, and the
    _RECAP_ABBREV map cannot enumerate every abbreviation. Empty WANT fails
    (nothing to confirm)."""
    if not want:
        return False
    for t in want:
        if t in have:
            continue
        if len(t) >= 3 and any(h.startswith(t) for h in have):
            continue
        return False
    return True


def _distinctive_name_query(citation: Citation) -> str:
    """Reduce the case name to the most distinctive token from each party.

    "Alden Glob. Value Recovery Master Fund, L.P. v. KeyBank N.A."
        -> "Alden KeyBank"
    This recovers cases that the full case_name query misses because one
    abbreviated token fails the AND match.  Returns "" if the name has no
    versus separator (single-party names are too ambiguous to reduce).
    """
    if citation.toa_match and citation.toa_match.get("name"):
        src = citation.toa_match["name"]
    else:
        src = citation.name or ""
    src = src.replace("*", "").strip()
    p1, p2 = parse_case_name(src)
    if not p2:
        return ""

    def first_token(party: str) -> str:
        for t in re.findall(r"[A-Za-z0-9]+", party):
            if t.lower() not in _NAME_STOPWORDS and len(t) > 1:
                return t
        return ""

    t1, t2 = first_token(p1), first_token(p2)
    if t1 and t2:
        return f"{t1} {t2}"
    return ""


def _legacy_query(citation: Citation) -> str:
    """Reproduce the pre-tiered (kitchen-sink) query for the last-resort tier."""
    if citation.toa_match:
        toa_name = (citation.toa_match.get("name", "") or "").replace("*", "").strip()
        toa_cite = citation.toa_match.get("cite", "")
        if toa_name:
            p1, p2 = parse_case_name(toa_name)
            q = f"{p1} {p2}".strip() if p2 else p1
            if toa_cite:
                q = f"{q} {toa_cite}"
            return q
    return _build_search_query(citation)


def _normalize_cite(cite: str) -> str:
    """Normalize a reporter cite for equality comparison (drop punctuation/case)."""
    c = (cite or "").lower().replace(".", "").replace(",", "")
    return re.sub(r"\s+", " ", c).strip()


def reporter_cite_str(citation) -> str:
    """Reporter cite for THIS citation as 'volume reporter page'.

    PRIMARY resolution key (Phase 2, 2026.07.04): the reporter cite parsed
    from the brief body by eyecite (citation.reporter_cite). The TOA-matched
    cite is only a fallback for instances that carry no parsed group.
    """
    rc = getattr(citation, "reporter_cite", None)
    if isinstance(rc, dict) and rc.get("volume") and rc.get("reporter") and rc.get("page"):
        return f"{rc['volume']} {rc['reporter']} {rc['page']}"
    if citation.toa_match:
        return _reporter_only(citation.toa_match.get("cite", ""))
    return ""


def _cite_key(citation) -> str:
    """Normalized reporter-cite cache/lookup key ('' when the citation has none)."""
    return _normalize_cite(reporter_cite_str(citation))


# --------------------------------------------------------------------------
# HTML tag stripping
# --------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Remove HTML/XML tags and unescape entities."""
    cleaned = _HTML_TAG_RE.sub("", text)
    return html_mod.unescape(cleaned)


# --------------------------------------------------------------------------
# Opinion text extraction
# --------------------------------------------------------------------------
_TEXT_FIELDS = [
    "plain_text",
    "html_with_citations",
    "html",
    "html_columbia",
    "html_lawbox",
    "xml_harvard",
]


def extract_opinion_text(opinion_data: dict) -> Optional[str]:
    """Extract opinion text from a CourtListener opinion API response.

    Checks fields in priority order. Returns stripped plain text, or None
    if all fields are empty.
    """
    for field_name in _TEXT_FIELDS:
        val = opinion_data.get(field_name)
        if val and isinstance(val, str) and val.strip():
            if field_name == "plain_text":
                return val.strip()
            # All other fields are HTML/XML -- strip tags
            stripped = strip_html(val).strip()
            if stripped:
                return stripped
    return None


# --------------------------------------------------------------------------
# Chunk 3: opinion-URL construction, substantive selection, pincite trimming,
# and the Justia / NY-Reporter fallback for CourtListener gaps.
# --------------------------------------------------------------------------
def make_opinion_url(absolute_url):
    """Turn a CourtListener absolute_url into a fully-qualified, clickable link."""
    if not absolute_url:
        return ""
    if absolute_url.startswith("http"):
        return absolute_url
    return WEB_BASE + "/" + absolute_url.lstrip("/")


def _opinion_rank(op_type, length):
    """Sort key (type_rank, -length): controlling opinions and longest text first."""
    rank = _OPINION_TYPE_RANK.get((op_type or "").lower(), _DEFAULT_TYPE_RANK)
    return (rank, -length)


_SLIP_OP_RE = re.compile(r"(\d{4})\s+N\.?Y\.?\s+Slip\s+Op\.?\s+(\d{4,6})\s*\(?U\)?", re.I)


def _pincite_from_citation(citation):
    """Extract a pincite page number from a Citation, if the brief supplied one.

    Sources, in order: an explicit ``pincite`` attr; the enricher ``pinpoints``
    list (body short-cites like "Simmons, 105 Tex. at 215" carry the pincite
    there, not in the TOA cite); then the TOA cite's second page number
    ("84 N.Y.2d 430, 438" -> 438).  Reporter-agnostic throughout (item 3).
    """
    pin = getattr(citation, "pincite", "") or ""
    if pin:
        m = re.search(r"\d+", str(pin))
        if m:
            return m.group(0)
    # Enricher pinpoints: scan each entry for a page-like integer.
    for pp in (getattr(citation, "pinpoints", None) or []):
        cand = ""
        if isinstance(pp, dict):
            for k in ("page", "pincite", "pin", "value", "text", "label"):
                if pp.get(k):
                    cand = str(pp[k])
                    break
        elif isinstance(pp, (str, int)):
            cand = str(pp)
        m = re.search(r"\d{1,5}", cand)
        if m:
            return m.group(0)
    cite = ""
    if getattr(citation, "toa_match", None):
        cite = citation.toa_match.get("cite", "") or ""
    # "84 N.Y.2d 430, 438" -> 438 ; reporter tokens may contain digits ("N.Y.3d").
    # Strip parentheticals FIRST (Phase 4, 2026.07.04): a date parenthetical
    # like "(N.D. Tex. Aug. 7, 2009)" otherwise matches "7, 2009" and ships the
    # YEAR as a phantom pincite (the card read "Pincite p. 2009").  Same class
    # as the 06.29 year-as-page bug in _page_from_frag.
    cite_no_paren = re.sub(r"\([^)]*\)", " ", cite)
    m = re.search(r"\b\d+\s*,\s*(\d+)\b", cite_no_paren)
    return m.group(1) if m else ""


_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


_RECAP_ABBREV = {
    "Nat'l": "National", "Natl": "National", "Int'l": "International",
    "Intl": "International", "Ins": "Insurance", "Liab": "Liability",
    "Assur": "Assurance", "Cas": "Casualty", "Mut": "Mutual", "Co": "Company",
    "Cos": "Companies", "Corp": "Corporation", "Inc": "Incorporated",
    "Ass'n": "Association", "Assn": "Association", "Bros": "Brothers",
    "Mfg": "Manufacturing", "Mgmt": "Management", "Svcs": "Services",
    "Serv": "Services", "Inv": "Investment", "Fin": "Financial",
    "Sav": "Savings", "Bancorp": "Bancorp", "Tech": "Technologies",
    "Pharm": "Pharmaceuticals", "Labs": "Laboratories", "Ry": "Railway",
    "RR": "Railroad", "Petroleum": "Petroleum",
}


def _cite_date_iso(citation):
    """Parse the decision date out of a citation's reporter cite, e.g.
    '2026 WL 638658 (S.D. Tex. Mar. 6, 2026)' -> '2026-03-06'.  None if absent.
    Used to disambiguate which RECAP docket entry is the cited order."""
    cite = ""
    if getattr(citation, "toa_match", None):
        cite = citation.toa_match.get("cite", "") or ""
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s+((?:19|20)\d{2})", cite)
    if not m:
        return None
    mon = _MONTH_NUM.get(m.group(1).lower())
    if not mon:
        return None
    try:
        return "%04d-%02d-%02d" % (int(m.group(3)), mon, int(m.group(2)))
    except (TypeError, ValueError):
        return None


_FN_SENTINEL = "[OPINION FOOTNOTES]"
_FN_TAIL_MAX = 20000  # bound the appended footnote tail (token cost)

_PIN_FOOTNOTE_RE = re.compile(r"\bn+\.?\s*(\d{1,3})\b")


def _pincite_footnote(citation):
    """The footnote number a pincite targets ('852, n.1' -> '1'), or ''.

    2026.07.04 (footnote fix): a brief citing 'at 852 n.1' is citing the
    OPINION'S footnote.  CL plain_text renders footnotes as endnotes after
    the body (often without their numbers), so the pincite window used to
    silently exclude the very text the brief relies on -- the Brief A
    Alliance Network false "Does Not Support"."""
    for src in ((getattr(citation, "pincite", "") or ""),
                (getattr(citation, "pin_cite", "") or ""),
                (getattr(citation, "cite_text", "") or "")):
        m = _PIN_FOOTNOTE_RE.search(src)
        if m:
            return m.group(1)
    return ""


# Endnote-block leads, strongest first.  CL plain_text renders endnotes as
# paragraphs opening ". " (the number is dropped) or under a "Footnotes"
# heading -- distinctive forms that never open a body paragraph.  The
# numbered form "N. " IS a plausible body paragraph opener (numbered lists),
# so it is only trusted as a weak fallback.
_FN_LEAD_STRONG_RE = re.compile(r"\n\s*\n(?:\.\s|Footnotes?\b|FOOTNOTES?\b)")
_FN_LEAD_WEAK_RE = re.compile(r"\n\s*\n\d{1,2}\.\s")


def _footnote_tail(text):
    """The opinion's endnote-bearing tail.

    Find the endnote block by its own LEAD pattern, searching the last
    _FN_TAIL_MAX chars.  Star-page markers are deliberately NOT used as the
    anchor: footnote text itself often carries star-pincites in its case
    citations ("... 2011 NY Slip Op 51691[U], *4 ..."), so the "last star"
    can sit INSIDE a footnote and skip earlier footnotes (observed live on
    Alliance Network -- n.1 was cut out of its own tail).  Weak (numbered)
    leads are only trusted when no strong lead exists.  Falls back to the
    raw tail slice so the footnote text is never silently dropped."""
    start0 = max(0, len(text) - _FN_TAIL_MAX)
    m = _FN_LEAD_STRONG_RE.search(text, start0)
    if m is None:
        m = _FN_LEAD_WEAK_RE.search(text, start0)
    start = m.start() if m is not None else start0
    return text[start:], start


def _trim_to_pincite(text, pincite, footnote_ref=""):
    """Trim a long opinion to a window around the cited page; else head-trim.

    When the pincite window does not already start at the top of the opinion,
    prepend a head slice (caption + reporter cite) so the identity gate
    (_name_or_cite_match) and verify() still see the case name -- otherwise a
    mid-opinion window false-fails identity as "Unconfirmed" (the Duenez bug,
    2026.06.30).

    2026.07.04 (footnote fix): when the pincite carries a footnote component
    (``footnote_ref``), the opinion's endnote tail is APPENDED to the window
    behind a sentinel line -- CL (and free-source) copies render footnotes
    after the body, so a page-window would otherwise exclude exactly the text
    the brief cites.  The sentinel lets the report say "located in the
    opinion's footnotes" instead of a misleading star page."""
    def _with_tail(trimmed, hi_end):
        if not footnote_ref or hi_end >= len(text):
            return trimmed
        tail, tstart = _footnote_tail(text)
        if tstart <= hi_end:
            tail = text[hi_end:]
        if not tail.strip():
            return trimmed
        return trimmed + "\n[...]\n" + _FN_SENTINEL + "\n" + tail

    if len(text) <= _TRIM_THRESHOLD:
        return text, False
    if pincite:
        for pat in (r"\*\s*%s(?!\d)" % re.escape(pincite),
                    r"\[\s*%s\s*\]" % re.escape(pincite),
                    r"\bpage\s+%s\b" % re.escape(pincite)):
            m = re.search(pat, text, re.I)
            if m:
                lo = max(0, m.start() - _PINCITE_WINDOW)
                hi = min(len(text), m.end() + _PINCITE_WINDOW)
                window = text[lo:hi]
                if lo > _HEAD_KEEP:
                    return _with_tail(text[:_HEAD_KEEP] + "\n[...]\n" + window, hi), True
                return _with_tail(window, hi), True
    return _with_tail(text[:_HEAD_TRIM], _HEAD_TRIM), True


def _source_has_pagination(text):
    """True only if the text carries the CITED REPORTER'S pagination -- star
    pages ('*123'), bracketed reporter pages ('[123]'), or a bare 'page 123'
    that is NOT a PACER/ECF 'Page 123 of 456' filing stamp.  A pincite ('at *2')
    points to a Westlaw/reporter page; PACER stamps are the filing's own pages,
    a different numbering, so a copy whose only page marks are PACER stamps is
    treated as UNPAGINATED for pincite purposes -- a star-pincite can never be
    confirmed against it, so 'not found' there would be misleading.  (2026.06.30)"""
    if not text:
        return False
    if re.search(r"\*\s*\d+(?!\d)|\[\s*\d+\s*\]", text):
        return True
    for m in re.finditer(r"\bpage\s+\d+\b", text, re.I):
        if not re.match(r"\s+of\s+\d+", text[m.end():m.end()+10], re.I):
            return True
    return False


def _pincite_located(text, pincite):
    """True if the cited page is locatable in the opinion text.  Uses only the
    reliable page-marker forms ('*NNN', '[NNN]', 'page NNN') -- deliberately
    NOT a bare 'at NNN', which collides with ordinary prose.  Reporter- and
    jurisdiction-agnostic (locked spec #10; item 3)."""
    if not text or not pincite:
        return False
    pin = re.escape(str(pincite))
    for pat in (r"\*\s*%s(?!\d)" % pin,
                r"\[\s*%s\s*\]" % pin,
                r"\bpage\s+%s\b(?!\s+of\b)" % pin):
        if re.search(pat, text, re.I):
            return True
    return False


_STAR_PAGE_BEFORE_RE = re.compile(r"\*\s*(\d{1,5})(?!\d)")


def star_page_before(text, offset):
    """Nearest PRECEDING star-page marker (*N) before ``offset`` (Phase 4,
    audit 3.4). Star pages in CL Opinions copies of Westlaw-cited cases mark
    where reporter page N begins, so the last *N before the supporting
    passage's start offset is the page the passage sits on. Uses ``(?!\d)``
    after the number, never ``\b`` (star-page glue, journal Part 2). PACER
    'Page N of M' stamps never match this pattern. Returns '' when the copy
    carries no star pagination before the offset."""
    if not text or offset is None:
        return ""
    try:
        hi = max(0, min(int(offset) + 1, len(text)))
    except (TypeError, ValueError):
        return ""
    last = ""
    for m in _STAR_PAGE_BEFORE_RE.finditer(text, 0, hi):
        last = m.group(1)
    return last


def justia_search_url(citation):
    """Justia case-law search URL fallback for a CL gap (reported decisions)."""
    if getattr(citation, "toa_match", None) and citation.toa_match.get("name"):
        name = citation.toa_match["name"]
    else:
        name = citation.name or ""
    name = name.replace("*", "").strip()
    return "https://law.justia.com/search?q=" + quote_plus(name)


def nycourts_reporter_url(reporter_cite):
    """Official NYS Law Reporting Bureau URL for a NY Slip Op (U) decision."""
    if not reporter_cite:
        return ""
    m = _SLIP_OP_RE.search(reporter_cite)
    if not m:
        return ""
    year, num = m.group(1), m.group(2)
    # Validated against the NYS Law Reporting Bureau (June 2026): 3xxxx-series
    # (U) trial orders are served as PDFs under /reporter/pdfs/; 5xxxx-series
    # (Misc 3d table) decisions are HTML under /reporter/3dseries/.
    if num.startswith("5"):
        return "https://www.nycourts.gov/reporter/3dseries/%s/%s_%s.htm" % (year, year, num)
    return "https://www.nycourts.gov/reporter/pdfs/%s/%s_%s.pdf" % (year, year, num)


def justia_ny_slip_op_url(reporter_cite):
    """Deterministic Justia URL for a NY Slip Op (U) decision.  Validated June
    2026 across 2014/2021/2025 and the 3xxxx & 5xxxx ranges -- Justia hosts the
    whole (U) series under one uniform path (unlike nycourts.gov, which splits
    PDF vs 3dseries)."""
    if not reporter_cite:
        return ""
    m = _SLIP_OP_RE.search(reporter_cite)
    if not m:
        return ""
    year, num = m.group(1), m.group(2)
    return ("https://law.justia.com/cases/new-york/other-courts/"
            "%s/%s-ny-slip-op-%s-u.html" % (year, year, num))


# --------------------------------------------------------------------------
# Session E (2026.07.29): deterministic Texas builders (I1 / I2 / SCOTX).
# --------------------------------------------------------------------------
_TX_ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
}
_TX_COA_DOCKET_RE = re.compile(r"(0[1-9]|1[0-4])-(\d{2})-(\d{5})-(CV|CR)", re.I)
_SCOTX_DOCKET_RE = re.compile(r"\d{2}-\d{4}")
_TX_BC_DOCKET_RE = re.compile(r"\d{2}-BC\d{2}[A-Za-z]-\d{4}", re.I)


def justia_txcoa_urls(citation):
    """I1 (2026.07.29): deterministic Justia URLs for a Texas COA docket-cited
    case: law.justia.com/cases/texas/{ordinal}-court-of-appeals/{year}/{docket}
    (docket prefix 01-14 encodes the court).  Returns candidates for the docket
    year AND the following year (a decision often issues the year after
    filing).  Validated live 2026.07.29 (Raphael, 05-24-00053-CV -> full
    opinion via web_fetch; note law.justia.com bot-blocks plain requests)."""
    dkt = _docket_number(citation)
    m = _TX_COA_DOCKET_RE.fullmatch(dkt or "")
    if not m:
        return []
    ordinal = _TX_ORDINALS[int(m.group(1))]
    year = 2000 + int(m.group(2))
    dl = dkt.lower()
    out = []
    # Decision year can lag the docket year by up to two years (validated:
    # Kassab 01-24-00220-CV decided 2026).  The cases.justia.com PDF mirror
    # accepts plain requests (no bot block -- validated 2026.07.29), so list
    # it beside each HTML page URL.
    for y in (year, year + 1, year + 2):
        out.append(("justia",
                    "https://law.justia.com/cases/texas/%s-court-of-appeals/%d/%s.html"
                    % (ordinal, y, dl)))
        out.append(("justia",
                    "https://cases.justia.com/texas/%s-court-of-appeals/%d-%s.pdf"
                    % (ordinal, y, dl)))
    return out


def txcourts_case_search_url(citation):
    """SCOTX builder step 1 (2026.07.29): the official search.txcourts.gov CASE
    page for a Supreme Court of Texas docket (NN-NNNN, e.g. 21-0641).  The page
    lists the case's opinions with issue dates and SearchMedia PDF links, but
    the page itself is a NAVIGATION HINT, never opinion body -- the agent
    follows the opinion media link and patches THAT document (see
    _looks_like_case_search_page).  Fetchable via plain requests (validated
    2026.07.29, McLane 21-0641)."""
    dkt = _docket_number(citation)
    if dkt and _SCOTX_DOCKET_RE.fullmatch(dkt):
        return "https://search.txcourts.gov/Case.aspx?cn=%s&coa=cossup" % dkt
    return ""


def txcourts_bc_index_url(citation):
    """I2 (2026.07.29): the Texas Business Court opinions index for a Tex. Bus.
    Ct. docket (NN-BCnnX-NNNN).  CL 404s on this court; opinions live on
    txcourts.gov media pages linked from this index.  NAVIGATION HINT only."""
    dkt = _docket_number(citation)
    if dkt and _TX_BC_DOCKET_RE.fullmatch(dkt):
        return "https://www.txcourts.gov/businesscourt/opinions/"
    return ""


_CASE_SEARCH_PAGE_MARKERS = re.compile(
    r"Case Events|Appellate Briefs|Calendars|Trial Court Information"
    r"|Party Information|Set for Submission|SearchMedia\.aspx", re.I)


def _looks_like_case_search_page(text):
    """True if `text` reads like a txcourts case-search CASE page (docket/event
    index) rather than an opinion.  Guard for patch_gap: the docket-number
    identity branch would otherwise accept a case page (it prints the docket)
    as opinion text."""
    if not text:
        return False
    return len(_CASE_SEARCH_PAGE_MARKERS.findall(text[:20000])) >= 2


def fallback_candidates(citation):
    """Ordered list of (source, url) to try when CourtListener has no opinion.
    Order is convenience, not hierarchy -- ANY free source that returns the
    cited opinion is acceptable.  Only deterministically constructable URLs
    appear here (NY Slip Op (U) -> nycourts.gov + Justia).  Reported decisions
    needing a name/citation search (FindLaw, Justia reported, txcourts.gov) are
    resolved by the orchestrator's search step.  Validated live June 2026;
    case.law (Harvard) is dead and Google Scholar is discovery-only."""
    # Phase 2b (2026.07.04): the body reporter cite parsed by eyecite is the
    # primary source; the TOA cite is the fallback (pre-eyecite behavior).
    reporter = reporter_cite_str(citation) or ""
    out = []
    ny = nycourts_reporter_url(reporter)
    if ny:
        out.append(("nycourts_reporter", ny))
    jx = justia_ny_slip_op_url(reporter)
    if jx:
        out.append(("justia", jx))
    # Session E (2026.07.29): deterministic Texas candidates.  The two
    # txcourts entries are NAVIGATION HINTS for the agent-driven gap loop
    # (resolve_via_fallback skips them; patch_gap rejects a case page).
    out.extend(justia_txcoa_urls(citation))
    tx = txcourts_case_search_url(citation)
    if tx:
        out.append(("txcourts_case_page", tx))
    bc = txcourts_bc_index_url(citation)
    if bc:
        out.append(("txcourts_bc_index", bc))
    return out


def fallback_opinion_url(citation):
    """(url, source) for a CL gap: NY Reporter for Slip Op (U), else Justia search."""
    cands = fallback_candidates(citation)
    if cands:
        return cands[0][1], cands[0][0]
    return justia_search_url(citation), "justia"


# --------------------------------------------------------------------------
# Chunk 4: reported-gap search step.  For CL gaps whose opinion URL is NOT
# deterministically constructable (reported decisions, e.g. Franklin, 34 N.Y.3d
# 600 -- a Court of Appeals decision, not a Slip Op (U)), search the free legal
# sites by name+cite, fetch the top hit, extract the body, and gate on a
# name-or-cite match before accepting (guards the cross-database name mismatch,
# e.g. FindLaw labels Franklin "In re Franklin Street Realty Corp.").
# --------------------------------------------------------------------------
REPORTED_SEARCH_DOMAINS = [
    "law.justia.com", "cases.justia.com", "supreme.justia.com",
    "caselaw.findlaw.com", "www.txcourts.gov",
    # Session E (2026.07.29): CaseMine as a GENERAL free source (the attorney,
    # Session D closeout SS4).  JS-render check FAILED 2026.07.29: judgment
    # pages return EMPTY via web_fetch and the sandbox proxy blocks the
    # domain outright -- BEST-EFFORT discovery source only; a hit must be
    # copied/fetched by some other means before patch_gap can ingest it.
    "www.casemine.com",
]

# Generic party-name tokens that don't identify a case on their own.
_NAME_STOP = {
    "state", "states", "people", "matter", "city", "county", "town", "board",
    "department", "commissioner", "united", "corp", "corporation", "inc",
    "incorporated", "llc", "company", "the", "and", "ex", "rel", "et", "al",
    "versus", "estate", "trust", "trustees", "association", "partners",
    "holdings", "holdco", "bank", "national", "america", "american",
}

_JUSTIA_FOOTER_RE = re.compile(
    r"Some case metadata and case summaries were written.*", re.I | re.S)
_FINDLAW_FOOTER_RE = re.compile(r"Was this helpful\??.*", re.I | re.S)


def _collapse_text(t):
    """Collapse runaway whitespace without destroying paragraph breaks."""
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _source_for_url(url):
    u = (url or "").lower()
    if "justia.com" in u:
        return "justia"
    if "findlaw.com" in u:
        return "findlaw"
    if "casemine.com" in u:
        return "casemine"
    if "nycourts.gov" in u:
        return "nycourts_reporter"
    if "txcourts.gov" in u:
        return "txcourts"
    if "courtlistener.com" in u:
        return "courtlistener"
    return "web"


def extract_opinion_body(content, source="", url=""):
    """Extract the opinion body from a fetched page, stripping site chrome.
    HTML sources (Justia, FindLaw) are tag-stripped and footer-trimmed; PDF
    text (reporter/txcourts) is returned as-is (honors the no-reformat rule).
    Prefer HTML variants when fetching (see the SKILL's no-direct-PDF rule)."""
    if not content:
        return ""
    src = (source or _source_for_url(url)).lower()
    looks_html = "<" in content and ">" in content
    if src == "justia":
        body = strip_html(content) if looks_html else content
        body = _JUSTIA_FOOTER_RE.sub("", body)
        return _collapse_text(body)
    if src == "findlaw":
        body = strip_html(content) if looks_html else content
        body = _FINDLAW_FOOTER_RE.sub("", body)
        return _collapse_text(body)
    if src == "casemine":
        # Session E (2026.07.29): generic tag-strip; no stable footer marker
        # observed (fetch path currently returns empty -- best-effort source).
        body = strip_html(content) if looks_html else content
        return _collapse_text(body)
    # reporter / txcourts / unknown: strip tags if HTML, else (PDF text) as-is
    if looks_html:
        return _collapse_text(strip_html(content))
    return content.strip()


def _cite_core(reporter):
    """(volume, page) from a reporter cite like '34 N.Y.3d 600' or
    '84 N.Y.2d 430, 438'; page is the FIRST page of the opinion."""
    m = re.search(r"\b(\d+)\s+[A-Za-z][A-Za-z0-9.\s]*?\s+(\d+)", reporter or "")
    if not m:
        return "", ""
    return m.group(1), m.group(2)


_DOCKET_RE = re.compile(
    r"\b(\d{1,2}-\d{2}-\d{4,6}-[A-Za-z]{2}"
    r"|\d{2}-BC\d{2}[A-Za-z]-\d{4}"
    r"|\d{1,2}:\d{2}-[A-Za-z]{2,3}-\d{3,6}"
    r"|\d{2}-\d{3,4})\b")


def _docket_number(citation):
    """Best docket number for a citation (from cite_text / name / pin), or ''.
    Whitespace is stripped first because eyecite can split a docket fragment
    ("No. 05-24- 00053-CV")."""
    for src in (getattr(citation, "cite_text", "") or "",
                getattr(citation, "name", "") or "",
                getattr(citation, "pin_cite", "") or ""):
        m = _DOCKET_RE.search(re.sub(r"\s+", "", src))
        if m:
            return m.group(1)
    return ""


def _docket_in_body(dkt, text):
    """True if docket number `dkt` co-occurs in `text` (whitespace-insensitive)."""
    if not dkt or not text:
        return False
    return re.sub(r"\s+", "", dkt).lower() in re.sub(r"\s+", "", text).lower()


_DOCKET_SHEET_MARKERS = re.compile(
    r"represented by|LEAD ATTORNEY|ATTORNEY TO BE NOTICED|TERMINATED:"
    r"|Date Filed\s*#\s*Docket Text|PACER Service Center"
    r"|Docket (?:Text|Report)|Query\s+Reports", re.IGNORECASE)


def _looks_like_docket_sheet(text):
    """True if `text` reads like a PACER docket sheet / party-attorney index
    rather than an opinion (B1: [5] Temple v. Cortez resolved to a docket
    sheet whose body is a party-name index)."""
    if not text:
        return False
    return len(_DOCKET_SHEET_MARKERS.findall(text[:8000])) >= 3


def _recap_body_acceptable(citation, text):
    """B1 (2026.07.29): guard the RECAP fallback. The fallback exists for
    WL/LEXIS database cites whose opinions live only in the docket. When the
    brief cites a PUBLISHED reporter (S.W.3d, U.S., F.4th, ...), the opinion
    belongs in the Opinions DB; a RECAP hit is usually a docket sheet or an
    interlocutory filing, not the opinion ([5] Temple docket sheet; [19/20]
    Gensetix 3.9k federal filing). Accept for a published-reporter cite only
    when the body reads like the opinion (disposition marker, not a docket
    sheet) AND actually carries the cited reporter address or docket number."""
    if not text:
        return False
    fam = _reporter_family(reporter_cite_str(citation) or "")
    is_published = (bool(fam) and fam not in _DB_REPORTER_FAMS
                    and "lexis" not in fam)
    if not is_published:
        return True
    if len(text) < 2500 or _looks_like_docket_sheet(text):
        return False
    try:
        if not _has_opinion_disposition(text):
            return False
    except Exception:  # noqa: BLE001
        pass
    if _name_or_cite_match(citation, text) is True:
        return True
    dkt = _docket_number(citation)
    return bool(dkt and _docket_in_body(dkt, text))


def _name_or_cite_match(citation, text):
    """Gate a fetched opinion: accept only if the cited reporter (volume + first
    page co-occurring) is present, OR >=2 distinct party-name tokens appear.

    The reporter volume+page co-occurrence is the reliable signal.  The
    name-token branch requires at least TWO distinct tokens because a lone
    common surname false-accepts: a search for "Franklin, 34 N.Y.3d 6" surfaces
    "People v Franklin" and "Franklin v Daily Holdings", and accepting on the
    bare token "franklin" would let the wrong opinion through (observed live
    2026.06.25).  A single-token case name therefore must clear the cite gate."""
    if not text or len(text) < 200:
        return False
    low = re.sub(r"\s+", " ", text.lower())
    reporter = reporter_cite_str(citation) or ""
    vol, page = _cite_core(reporter)
    if vol and page:
        for vm in re.finditer(r"\b" + re.escape(vol) + r"\b", low):
            if re.search(r"\b" + re.escape(page) + r"\b", low[vm.start():vm.start() + 60]):
                return True
    name = ""
    if getattr(citation, "toa_match", None) and citation.toa_match.get("name"):
        name = citation.toa_match["name"]
    if not name:
        name = getattr(citation, "name", "") or ""
    # >=3 chars (was >3): a 3-letter surname like "Coe" is a real, distinctive
    # party token.  The >=2-distinct-token rule still rejects the lone-surname
    # false-accept the gate was hardened against (a single "franklin" stays 1).
    toks = {t.lower() for t in re.split(r"[^A-Za-z]+", name)
            if len(t) >= 3 and t.lower() not in _NAME_STOP}
    matched = sum(1 for t in toks if t in low)
    if matched >= 2:
        return True
    # Section B (2026.07.29): docket-number match branch. WL-only-cited COA
    # opinions whose eyecite name is a docket fragment ("No. 05-24-00053-CV")
    # or a single token pass neither the reporter branch (WL numbers never
    # appear in opinion bodies) nor the >=2-name-token branch, even with the
    # correct opinion in hand. Docket numbers ARE printed in opinions and are
    # highly distinctive, so a docket co-occurrence is an accept signal.
    dkt = _docket_number(citation)
    if dkt and _docket_in_body(dkt, text):
        return True
    return False


def _append_lookup_note(citation, note):
    """Append a reviewer note to citation._lookup_note (report-visible)."""
    try:
        prev = getattr(citation, "_lookup_note", "") or ""
        if note in prev:
            return
        citation._lookup_note = ((prev + " ") if prev else "") + note
    except Exception:  # noqa: BLE001
        pass


def _expand_abbrev(name):
    """Expand common party-name abbreviations (shared _RECAP_ABBREV map)."""
    out = name or ""
    for ab, full in _RECAP_ABBREV.items():
        out = re.sub(r"\b" + re.escape(ab) + r"\b\.?", full, out, flags=re.I)
    return out


def _lookup_name_check(citation, cluster_name):
    """Name-check a citation-lookup 200 hit (Charlotin Bug 1 fix, Part 2,
    2026.07.09, from rlfordon/citation-verifier).

    A 200 from citation-lookup proves the cite ADDRESS exists on
    CourtListener -- it does not prove the address belongs to the case the
    brief names (the hallmark hallucination: real cite, wrong case, e.g.
    "Hogan v. AT&T, 917 F. Supp. 1275" where that address is United States
    ex rel. Green v. Washington).  Compares distinctive name tokens after
    abbreviation expansion.  Returns:
      'match'     -- >=1 shared distinctive token (accept as before)
      'mismatch'  -- both sides comparable, ZERO shared tokens (reject)
      'nocompare' -- either side has no distinctive tokens (accept + warn)
    Zero-overlap-only mismatch is deliberately conservative: a false reject
    here loses a real citation, the regression the resolver gate's
    REAL-LOSS class exists to catch."""
    # 2026.08.04 (v16 Williams-short fix): a short form whose brief-side
    # "name" is the cite string itself ("789 S.W.2d at 265", "Id.") yields
    # only numeric/reporter tokens. Those are not NAME evidence -- comparing
    # them zero-overlap-REJECTED the lookup's own correct cluster and gap'd
    # a Texas Supreme Court case. Strip them (shared _case_name_tokens
    # hygiene); empty -> nocompare (accept + warn).
    want = {t for t in _name_tokens(_expand_abbrev(_clean_case_name(citation)))
            if not t.isdigit() and t not in _CITEISH_TOKENS}
    have = _name_tokens(_expand_abbrev(cluster_name or ""))
    if not want or not have:
        return "nocompare"
    return "match" if (want & have) else "mismatch"


_CITEISH_TOKENS = {"id", "at", "supra", "ante", "2d", "3d", "4th",
                   "5th", "6th", "7th", "wl", "lexis", "supp",
                   "cv", "cr", "no"}


def _case_name_tokens(citation) -> set:
    """Distinctive NAME tokens of the brief-side case name -- docket-number
    fragments ("No. 02-20-00311-CV" -> 02/20/00311/cv), reporter series, and
    cite furniture stripped. 2026.08.04: those junk tokens inflated the
    name-overlap coverage requirement in _pick_best_result, so "Traweek v.
    Long, No. 02-20-00311-CV" (6 tokens, coverage 3) could never be covered
    by the caption's real 2 shared name tokens -- every correct Texas COA
    candidate was rejected."""
    return {t for t in _name_tokens(_clean_case_name(citation))
            if not t.isdigit() and t not in _CITEISH_TOKENS}

_SERIES_SUFFIX_RE = re.compile(r"(?:2d|3d|4th|5th|6th|7th)$")


def _reporter_family(cite):
    """Normalized reporter FAMILY of 'vol Reporter page' -- series-agnostic.

    "917 F. Supp. 2d 1275" -> 'fsupp'; "104 N.E.3d 1" -> 'ne';
    "575 U.S. 320" -> 'us'; "135 S. Ct. 1378" -> 'sct' (U.S. != S. Ct.).
    Returns '' when no volume/reporter/page shape is present."""
    m = re.search(r"\b\d+\s+(.+?)\s+\d+", cite or "")
    if not m:
        return ""
    compact = re.sub(r"[^a-z0-9]", "", m.group(1).lower())
    return _SERIES_SUFFIX_RE.sub("", compact)


def _cite_address_check(reporter_cite, result):
    """Cite-address contradiction check on a name-tier win (Part 2,
    2026.07.09, the citation-verifier repo's "Check Cite" lane).

    A name-tier win never proved the cite ADDRESS.  Compare the cited
    reporter address against the matched search result's citation list.
    Returns (kind, record_cite):
      'on_record'     -- cited address appears on the record (clean)
      'contradicted'  -- same reporter family at a DIFFERENT address
      'not_on_record' -- record has cites, none in the cited family (soft)
      'no_data'       -- no cited reporter or no citation list on the record
    """
    norm_rep = _normalize_cite(reporter_cite)
    fam = _reporter_family(reporter_cite)
    if not norm_rep or not fam:
        return "no_data", ""
    cites = result.get("citation") or []
    if isinstance(cites, str):
        cites = [cites]
    cites = [c for c in cites if c]
    if not cites:
        return "no_data", ""
    same_family = []
    for c in cites:
        if _normalize_cite(c) == norm_rep:
            return "on_record", c
        if _reporter_family(c) == fam:
            same_family.append(c)
    if same_family:
        return "contradicted", same_family[0]
    return "not_on_record", ""


_YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
# Database/unpublished reporter families that legitimately live in RECAP and
# must be exempt from the published-reporter year guard (G12).
_DB_REPORTER_FAMS = {"wl", "lexis"}


def _cited_year(citation):
    """The 4-digit decision year from the citation's court/date parenthetical
    (e.g. \"(5th Cir. 2024)\" -> 2024), or None."""
    yrs = _YEAR_RE.findall(getattr(citation, "cite_text", "") or "")
    return int(yrs[-1]) if yrs else None


def _cluster_year(meta):
    """The 4-digit year from a fetched cluster's date_filed, or None."""
    m = re.match(r"(\d{4})", str((meta or {}).get("date_filed", "") or ""))
    return int(m.group(1)) if m else None


def reported_search_query(citation):
    """'<case name> <reporter cite>' query for the free-site search step."""
    name = ""
    if getattr(citation, "toa_match", None) and citation.toa_match.get("name"):
        name = citation.toa_match["name"]
    if not name:
        name = getattr(citation, "name", "") or ""
    name = name.replace("*", "").strip()
    reporter = reporter_cite_str(citation) or ""
    return (name + " " + reporter).strip() if reporter else name


# --------------------------------------------------------------------------
# Resolver class
# --------------------------------------------------------------------------
def _split_for_lookup(text: str, cap: int = _LOOKUP_MAX_CHARS) -> list:
    """Split brief text into <=cap chunks at line boundaries (§5.3)."""
    out = []
    while len(text) > cap:
        cut = text.rfind("\n", max(0, cap - 2000), cap)
        if cut <= 0:
            cut = cap
        out.append(text[:cut])
        text = text[cut:]
    if text:
        out.append(text)
    return out


@dataclass
class ResolutionLog:
    """Diagnostic info about a resolution attempt, for gray citation reports."""
    query: str = ""
    court_filter: str = ""
    num_results: int = 0
    rejection_reasons: List[str] = field(default_factory=list)
    search_url: str = ""
    search_detail: str = ""  # human-readable summary: "Searched for X, found Y, rejected because Z"
    success: bool = False
    # Chunk 3 retrieval metadata -----------------------------------------
    opinion_url: str = ""
    url_source: str = ""
    cluster_id: str = ""
    opinion_id: str = ""
    opinion_type: str = ""
    pincite: str = ""
    trimmed: bool = False
    name_cite_ok: Optional[bool] = None  # identity gate computed on FULL text
    # Phase 7 confirmation gate (2026.07.15): the UNTRIMMED opinion body,
    # set at every resolution success path so the fabrication verdict can
    # always be confirmed against the complete text. Never serialized to
    # search_detail.
    full_text: Optional[str] = None

    def build_detail(self) -> str:
        """Build a human-readable search_detail string from the log."""
        parts = []
        if self.query:
            court_desc = f' with court filter "{self.court_filter}"' if self.court_filter else ""
            parts.append(f'Searched CourtListener for "{self.query}"{court_desc}.')
        if self.num_results == 0 and self.query:
            parts.append("No results found.")
        elif self.num_results > 0 and not self.success:
            reasons = "; ".join(self.rejection_reasons) if self.rejection_reasons else "unknown reason"
            parts.append(
                f"{self.num_results} result(s) found, all rejected \u2014 {reasons}."
            )
        elif not self.query and self.rejection_reasons:
            parts.append(" ".join(self.rejection_reasons))
        return " ".join(parts)


class CLResolver:
    """CourtListener opinion resolver with batch pre-resolution cache.

    Usage:
        resolver = CLResolver()
        # Optional: pre-resolve from full brief text
        resolver.batch_resolve(brief_text)
        # Per-citation callback for cite_check()
        result = cite_check(brief_text, resolver.resolve_opinion_text)
    """

    def __init__(self, token: Optional[str] = None, offline_opinions: Optional[Dict[str, str]] = None):
        """Initialize the resolver.

        Args:
            token: CourtListener API token. If None, loaded from CL_CONFIG.txt.
            offline_opinions: Optional dict of {case_name: opinion_text} for
                offline/testing mode. When provided, skips all API calls.
        """
        self._token = token
        self._offline = offline_opinions
        self._cache: Dict[str, str] = {}  # normalized_name -> opinion_text
        self._urls: Dict[str, str] = {}   # normalized_name -> resolvable opinion_url
        self._identity: Dict[str, bool] = {}  # cite key -> identity confirmed (F2 relax, 2026.07.14)
        self._meta: Dict[str, dict] = {}  # citation.name -> retrieval metadata
        self._logs: Dict[str, ResolutionLog] = {}  # citation.name -> log
        self._last_cluster_meta: dict = {}
        self._lookup_map: Dict[str, dict] = {}  # normalized cite -> batch lookup entry
        self._request_count = 0
        self._window_start = time.time()

        if self._token is None and self._offline is None:
            self._token = self._load_token()

    @staticmethod
    def _load_token() -> str:
        """Load the CourtListener API token.

        Discovery order:
          1. COURTLISTENER_API_TOKEN environment variable
          2. api_keys.courtlistener in ~/.legal-skills/config.json
             (path overridable via LEGAL_SKILLS_CONFIG)
          3. a CL_CONFIG.txt file next to this module
        """
        import json as _json
        env = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()
        if env:
            return env
        cfg_path = os.environ.get("LEGAL_SKILLS_CONFIG") or os.path.join(
            os.path.expanduser("~"), ".legal-skills", "config.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            key = str((data.get("api_keys") or {}).get("courtlistener") or "").strip()
            if key:
                return key
        except (OSError, ValueError):
            pass
        local = Path(__file__).resolve().parent / "CL_CONFIG.txt"
        if local.exists():
            raw = local.read_text(encoding="utf-8")
            for line in raw.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped
        raise RuntimeError(
            "CourtListener API token not found. Set COURTLISTENER_API_TOKEN, "
            "add api_keys.courtlistener to ~/.legal-skills/config.json (run "
            "the environment-setup skill), or place CL_CONFIG.txt next to "
            "the scripts."
        )

    # --- Rate limiting ---------------------------------------------------

    def _check_rate(self) -> None:
        """Pause if approaching CourtListener's hourly rate limit."""
        now = time.time()
        elapsed = now - self._window_start
        if elapsed >= _RATE_WINDOW:
            self._request_count = 0
            self._window_start = now
            return
        if self._request_count >= _RATE_LIMIT:
            wait = _RATE_WINDOW - elapsed + 1
            time.sleep(wait)
            self._request_count = 0
            self._window_start = time.time()
        elif self._request_count >= _BACKOFF_THRESHOLD:
            time.sleep(0.5)

    # --- HTTP helpers ----------------------------------------------------

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """Authenticated GET request to CourtListener API."""
        if requests is None:
            return None
        self._check_rate()
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Token {self._token}"},
                params=params,
                timeout=30,
            )
            self._request_count += 1
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                except (TypeError, ValueError):
                    retry_after = 60
                time.sleep(retry_after)
                resp = requests.get(
                    url,
                    headers={"Authorization": f"Token {self._token}"},
                    params=params,
                    timeout=30,
                )
                self._request_count += 1
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    def _post(self, url: str, json_body: dict) -> Optional[dict]:
        """Authenticated POST request to CourtListener API."""
        if requests is None:
            return None
        self._check_rate()
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Token {self._token}"},
                json=json_body,
                timeout=60,
            )
            self._request_count += 1
            if resp.status_code == 429:
                try:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                except (TypeError, ValueError):
                    retry_after = 60
                time.sleep(retry_after)
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Token {self._token}"},
                    json=json_body,
                    timeout=60,
                )
                self._request_count += 1
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    # --- Cache helpers ---------------------------------------------------

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize a case name for cache lookups."""
        n = name.strip().lower()
        n = re.sub(r",\s+\d+\s+\S+\s+\d+.*$", "", n)
        n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
        return n.strip().rstrip(",")

    def _cache_get(self, name: str) -> Optional[str]:
        """Exact normalized-name cache lookup.

        2026.07.04 (known-bug 5.4): the 20-char prefix fuzzy match is GONE --
        it could false-hit across distinct cases sharing a long prefix (two
        "In re Marriage of S..." cases). Exact key only; the reporter-cite
        key in _cache_get_for is the primary lookup now.
        """
        return self._cache.get(self._normalize_name(name))

    def _cache_get_for(self, citation) -> Optional[str]:
        """Cache lookup for a citation: normalized reporter cite PRIMARY,
        exact normalized name secondary (§5.4)."""
        ck = _cite_key(citation)
        if ck and ck in self._cache:
            return self._cache[ck]
        return self._cache_get(citation.name)

    def _cache_set(self, name: str, text: str) -> None:
        """Store an opinion under a normalized case-name key."""
        self._cache[self._normalize_name(name)] = text

    def _cache_set_for(self, citation, text: str) -> None:
        """Store under the reporter-cite key (primary) AND the name key."""
        ck = _cite_key(citation)
        if ck:
            self._cache[ck] = text
        self._cache_set(citation.name, text)

    # --- Batch pre-resolution --------------------------------------------

    def batch_resolve(self, brief_text: str) -> int:
        """Pre-resolve citations by POSTing the brief to citation-lookup.

        Returns the number of citations successfully pre-resolved.
        """
        if self._offline is not None:
            return 0

        # 2026.07.04 (known-bug 5.3): no more silent 64k truncation. The
        # brief is split into <=64k chunks at line boundaries and each chunk
        # is POSTed separately (the API's documented per-request cap), so
        # citations past ~50 pages are pre-resolved too.
        url = f"{BASE_URL}/citation-lookup/?format=json"
        count = 0
        for text in _split_for_lookup(brief_text):
            data = self._post(url, {"text": text})
            if not data:
                continue
            count += self._ingest_lookup_clusters(data)
        return count

    def _ingest_lookup_clusters(self, data) -> int:
        """Cache opinion text for each 200-status citation-lookup item."""
        count = 0
        # citation-lookup returns a list of cluster objects or a dict with results
        results = data if isinstance(data, list) else data.get("results", [])
        if isinstance(results, list):
            for item in results:
                cluster_data = item if isinstance(item, dict) else {}
                case_name = (cluster_data.get("caseName") or cluster_data.get("case_name")
                             or cluster_data.get("case_name_full")
                             or cluster_data.get("caseNameFull") or "")
                if not case_name:
                    continue
                # Cache under the returned cite string(s) too (primary key).
                cite_keys = [_normalize_cite(c) for c in
                             ([cluster_data.get("citation") or ""] +
                              list(cluster_data.get("normalized_citations") or [])) if c]

                # Get the sub_opinions and fetch text
                sub_ops = cluster_data.get("sub_opinions", [])
                if not sub_ops:
                    # Try to get from the cluster ID
                    cluster_id = cluster_data.get("id") or cluster_data.get("cluster_id")
                    if cluster_id:
                        opinion_text = self._fetch_opinion_from_cluster(cluster_id)
                        if opinion_text:
                            self._cache_set(case_name, opinion_text)
                            # A1 (2026.07.14): keep the cluster's direct URL so a
                            # later cache hit carries a real opinion link instead
                            # of falling back to a search URL.
                            _url = (self._last_cluster_meta or {}).get("opinion_url", "")
                            if _url:
                                self._urls[self._normalize_name(case_name)] = _url
                            for ck in cite_keys:
                                self._cache[ck] = opinion_text
                                if _url:
                                    self._urls[ck] = _url
                            count += 1
                    continue

                for sub_op in sub_ops:
                    op_id = None
                    if isinstance(sub_op, dict):
                        op_id = sub_op.get("id")
                    elif isinstance(sub_op, str):
                        url_path = sub_op.split("?")[0].rstrip("/")
                        slug = url_path.rsplit("/", 1)[-1]
                        if slug.isdigit():
                            op_id = slug

                    if op_id:
                        opinion_text = self._fetch_opinion_text(str(op_id))
                        if opinion_text:
                            self._cache_set(case_name, opinion_text)
                            # A1 (2026.07.14): cluster URL for cache-hit linking.
                            _cid = cluster_data.get("id") or cluster_data.get("cluster_id")
                            _url = make_opinion_url(
                                cluster_data.get("absolute_url")
                                or (f"/opinion/{_cid}/" if _cid else ""))
                            if _url:
                                self._urls[self._normalize_name(case_name)] = _url
                            for ck in cite_keys:
                                self._cache[ck] = opinion_text
                                if _url:
                                    self._urls[ck] = _url
                            count += 1
                            break
        return count

    # --- Batched citation-lookup (Phase 2, 2026.07.04) --------------------

    @staticmethod
    def lookup_chunks(citations) -> list:
        """Deterministic chunking of the UNIQUE reporter cites in a citation
        list (<=250 cites and <=64k chars per request). Stable across runner
        windows because the citation list is stable in the checkpoint."""
        todo, seen = [], set()
        for c in citations:
            s = reporter_cite_str(c)
            k = _normalize_cite(s)
            if not k or k in seen:
                continue
            seen.add(k)
            todo.append(s)
        chunks, cur, cur_len = [], [], 0
        for s in todo:
            if cur and (len(cur) >= _LOOKUP_MAX_CITES
                        or cur_len + len(s) + 3 > _LOOKUP_MAX_CHARS):
                chunks.append(cur)
                cur, cur_len = [], 0
            cur.append(s)
            cur_len += len(s) + 3
        if cur:
            chunks.append(cur)
        return chunks

    def batch_lookup_step(self, citations, state=None, deadline=None):
        """Advance the batched citation-lookup within pacing + deadline.

        This is THE primary resolver entry (Phase 2). state is a picklable
        dict carried across runner windows:
          {"map": {normalized cite -> {"status","cite","normalized","clusters"}},
           "next": chunk index, "win_start": ts, "win_cites": n, "complete": bool}
        Pacing: <=60 valid cites per rolling minute (citation-lookup rule).
        When the required wait would blow the caller's deadline, we return
        with complete=False and the runner's resume loop retries next window
        (the checkpoint pattern -- do not invent another).
        """
        st = dict(state or {})
        st.setdefault("map", {})
        st.setdefault("next", 0)
        st.setdefault("win_start", 0.0)
        st.setdefault("win_cites", 0)
        st["complete"] = False
        if self._offline is not None:
            st["complete"] = True
            self._lookup_map = st["map"]
            return st
        chunks = self.lookup_chunks(citations)
        while st["next"] < len(chunks):
            chunk = chunks[st["next"]]
            now = time.time()
            if now - st["win_start"] >= 60:
                st["win_start"], st["win_cites"] = now, 0
            if st["win_cites"] > 0 and st["win_cites"] + len(chunk) > _LOOKUP_PACE_CITES:
                wait = 60 - (now - st["win_start"]) + 1
                if deadline is not None and now + wait > deadline:
                    self._lookup_map = st["map"]
                    return st  # resume in the next runner window
                time.sleep(max(wait, 0))
                st["win_start"], st["win_cites"] = time.time(), 0
            data = self._post(f"{BASE_URL}/citation-lookup/?format=json",
                              {"text": " ;\n".join(chunk)})
            st["win_cites"] += len(chunk)
            items = (data if isinstance(data, list)
                     else (data or {}).get("results", []) or [])
            for it in items:
                if not isinstance(it, dict):
                    continue
                raw = (it.get("citation") or "").strip()
                try:
                    status = int(it.get("status"))
                except (TypeError, ValueError):
                    status = None
                normalized = [n for n in (it.get("normalized_citations") or []) if n]
                clusters = []
                for cl in (it.get("clusters") or []):
                    cid = (cl.get("id") or cl.get("cluster_id")) if isinstance(cl, dict) else None
                    if cid:
                        clusters.append(str(cid))
                entry = {"status": status, "cite": raw,
                         "normalized": normalized, "clusters": clusters}
                for cand in [raw] + normalized:
                    k = _normalize_cite(cand)
                    if not k:
                        continue
                    prev = st["map"].get(k)
                    if prev is None or (prev.get("status") != 200 and status == 200):
                        st["map"][k] = entry
            st["next"] += 1
        st["complete"] = True
        self._lookup_map = st["map"]
        return st

    # --- Single-citation resolution --------------------------------------

    def resolve_opinion_text(self, citation: Citation) -> Optional[str]:
        """Resolve a citation to its full opinion text via CourtListener.

        Returns the plain-text opinion if found, or None if:
        - No matching cluster found
        - Cluster found but jurisdiction doesn't match
        - Opinion text fields are all empty
        - Any API error

        Never returns a snippet, summary, or partial text.
        """
        log = ResolutionLog()
        log.search_url = build_search_url(citation)

        # Non-case citations (rules, statutes, etc.) -- return None gracefully
        cit_type = (citation.type or "").lower()
        if cit_type and cit_type not in ("case_law", "case", "decision", "judicial_decision", ""):
            log.rejection_reasons.append(
                f"Non-case citation type: {cit_type}"
            )
            self._logs[citation.name] = log
            return None

        # Check offline opinions
        if self._offline is not None:
            text = self._offline.get(citation.name)
            if text is None:
                # Try normalized match
                norm = self._normalize_name(citation.name)
                for key, val in self._offline.items():
                    if self._normalize_name(key) == norm:
                        text = val
                        break
                    if (
                        norm.startswith(self._normalize_name(key)[:20])
                        or self._normalize_name(key).startswith(norm[:20])
                    ):
                        text = val
                        break
            if text:
                log.success = True
            else:
                log.rejection_reasons.append("Not found in offline opinions dict.")
            self._logs[citation.name] = log
            return text

        # Check cache: reporter-cite key primary, exact name secondary (§5.4)
        cached = self._cache_get_for(citation)
        if cached:
            log.success = True
            log.opinion_url = (self._urls.get(_cite_key(citation)) or
                               self._urls.get(self._normalize_name(citation.name), ""))
            if log.opinion_url:
                log.url_source = "courtlistener"
            # Re-attach the non-reporter source label on a cache hit so the
            # pincite layer still knows this came from RECAP/PACER, not a
            # reporter-paginated opinion (else a cache hit mislabels the page check).
            if log.opinion_url and "/docket/" in log.opinion_url:
                try:
                    citation._recap_url = log.opinion_url
                    citation._recap_source = "CourtListener (RECAP)"
                except Exception:  # noqa: BLE001
                    pass
            cached_pin = _pincite_from_citation(citation)
            try:
                nc = _name_or_cite_match(citation, cached)
            except Exception:  # noqa: BLE001
                nc = None
            # F2 (2026.07.14): an authority whose identity was confirmed by the
            # single-cluster subset relax stays confirmed on cache hits --
            # id-chain members re-enter here under the same cite key.
            if nc is not True and self._identity.get(_cite_key(citation)):
                nc = True
            log.name_cite_ok = nc
            try:
                citation._resolved_name_cite_ok = nc
            except Exception:  # noqa: BLE001
                pass
            log.full_text = cached  # Phase 7: untrimmed body, pre-trim
            cached, log.trimmed = _trim_to_pincite(
                cached, cached_pin, footnote_ref=_pincite_footnote(citation))
            log.pincite = cached_pin
            self._logs[citation.name] = log
            return cached

        # ------------------------------------------------------------------
        # Tiered structured lookup.  Build an ordered list of (field, value)
        # strategies, most precise first, and early-exit on the first tier
        # that yields a jurisdiction-matching cluster with real opinion text.
        #   Tier 1 -- reporter citation (citation= field).  Nearly unique.
        #   Tier 2 -- clean case_name (party1 + party2, no parenthetical).
        #   Tier 3 -- distinctive minimal case_name (one token per party);
        #             recovers cases an abbreviated token would otherwise
        #             zero out of the AND-of-tokens case_name match.
        #   Tier 4 -- legacy kitchen-sink q= blob.  Last resort.
        # ------------------------------------------------------------------
        # PRIMARY resolution key (Phase 2, 2026.07.04): the reporter cite as
        # written in the brief body (parsed by eyecite); TOA cite is fallback.
        reporter_cite = reporter_cite_str(citation)
        clean_name = _clean_case_name(citation)
        distinctive = _distinctive_name_query(citation)
        legacy_q = _legacy_query(citation)
        log.query = clean_name or legacy_q

        # Court filter for jurisdiction narrowing
        court_filter = ""
        if citation.jurisdiction:
            courts = _JURISDICTION_TO_CL_COURTS.get(
                citation.jurisdiction.upper().strip(), []
            )
            if courts:
                court_filter = " ".join(courts)
                log.court_filter = court_filter

        tiers: List[Tuple[str, str]] = []
        if reporter_cite:
            tiers.append(("citation", reporter_cite))
        if clean_name:
            tiers.append(("case_name", clean_name))
        if distinctive and distinctive.lower() != clean_name.lower():
            tiers.append(("case_name", distinctive))
        if legacy_q:
            tiers.append(("q", legacy_q))

        # Batched citation-lookup result (Phase 2, 2026.07.04). When the
        # batch ran, its per-cite status REPLACES the single-cite Tier 0 POST:
        #   200 -> fetch the matched cluster (authoritative);
        #   300 -> "Ambiguous citation" reviewer note, then name tiers;
        #   400 -> "Reporter not recognized -- possible typo" note, then tiers;
        #   404 -> straight to name tiers -> RECAP -> gap manifest.
        lk = None
        if reporter_cite and self._lookup_map:
            lk = self._lookup_map.get(_normalize_cite(reporter_cite))
        if lk is not None:
            st_code = lk.get("status")
            try:
                citation._lookup_status = st_code
            except Exception:  # noqa: BLE001
                pass
            if st_code == 200:
                mismatch = [n for n in (lk.get("normalized") or [])
                            if _normalize_cite(n) != _normalize_cite(lk.get("cite") or "")]
                if mismatch:
                    try:
                        citation._lookup_note = (
                            "CourtListener normalized this citation to "
                            f"'{mismatch[0]}' \u2014 check the cite as written "
                            "for a possible typo.")
                    except Exception:  # noqa: BLE001
                        pass
                for cid in (lk.get("clusters") or []):
                    text0 = self._fetch_opinion_from_cluster(str(cid))
                    if not text0:
                        continue
                    # Lookup-200 name check (Part 2, 2026.07.09 -- the
                    # citation-verifier repo's "Charlotin Bug 1" fix): a 200
                    # proves the cite ADDRESS exists, not that it belongs to
                    # the case the brief names.  A clear caption mismatch is
                    # the classic hallucination shape -- real address, wrong
                    # case -- so the cluster is REJECTED (fall through to the
                    # name tiers) with a reviewer note.
                    cl_name = (getattr(self, "_last_cluster_meta", None)
                               or {}).get("case_name", "")
                    check = _lookup_name_check(citation, cl_name)
                    if check == "mismatch":
                        _append_lookup_note(citation, (
                            "CourtListener's record at '" + reporter_cite +
                            "' is captioned '" + cl_name + "', which does not "
                            "match the cited case name. The printed cite "
                            "likely belongs to a different case; check the "
                            "cite as written."))
                        log.rejection_reasons.append(
                            "citation-lookup 200 rejected (caption mismatch): "
                            f"cluster {cid} is captioned {cl_name!r}.")
                        try:
                            # The address is KNOWN to belong to a different
                            # case; any later name-tier/RECAP win that lacks
                            # this address inherits identity treatment.
                            citation._lookup_addr_mismatch = True
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    if check == "nocompare":
                        _append_lookup_note(citation, (
                            "CourtListener's record for this cite could not "
                            "be name-checked against the cited case name."))
                    # F2/M4 (2026.07.14): identity relax on an exact single-
                    # cluster reporter hit. eyecite often truncates a caption
                    # ("Trust v. Commonwealth Dev. Corp."), which false-fails
                    # the token-overlap identity gate on a correctly-resolved
                    # case. When the cite address matched exactly ONE cluster
                    # AND every distinctive token of the cited name appears in
                    # the cluster caption (subset test), the address + caption
                    # carry identity. Disjoint-token captions (TIG / fake-06
                    # class) were already rejected above as 'mismatch'.
                    _relax = False
                    if check == "match" and len(lk.get("clusters") or []) == 1:
                        _want = _name_tokens(_expand_abbrev(_clean_case_name(citation)))
                        _have = _name_tokens(_expand_abbrev(cl_name))
                        _relax = bool(_have) and _tokens_subsumed(_want, _have)
                    log.query = reporter_cite
                    self._cache_set_for(citation, text0)
                    out = self._record_success(citation, log, text0)
                    if _relax:
                        _k = _cite_key(citation)
                        if _k:
                            self._identity[_k] = True
                        if not log.name_cite_ok:
                            log.name_cite_ok = True
                            try:
                                citation._resolved_name_cite_ok = True
                                citation._identity_relaxed = True
                            except Exception:  # noqa: BLE001
                                pass
                    self._logs[citation.name] = log
                    logger.debug("cl_resolver: resolved '%s' via batched "
                                 "citation-lookup %r", citation.name, reporter_cite)
                    return out
                # 200 but no usable / name-matching text -> fall through to
                # the name tiers.
            elif st_code == 300:
                try:
                    citation._lookup_note = (
                        "Ambiguous citation \u2014 CourtListener matched multiple "
                        "cases for this cite; resolution fell back to case-name search.")
                except Exception:  # noqa: BLE001
                    pass
            elif st_code == 400:
                try:
                    citation._lookup_note = (
                        "Reporter not recognized \u2014 possible typo in the "
                        "brief's citation.")
                except Exception:  # noqa: BLE001
                    pass

        # Tier 0 -- exact citation-lookup endpoint (single cite).  Only used
        # when NO batch lookup covered this cite (one-shot path); a reporter
        # cite is globally unique, so an exact hit is authoritative.
        if reporter_cite and lk is None:
            text0 = self._citation_lookup(reporter_cite, citation)
            if text0:
                log.query = reporter_cite
                self._cache_set_for(citation, text0)
                out = self._record_success(citation, log, text0)
                self._logs[citation.name] = log
                logger.debug("cl_resolver: resolved '%s' via citation-lookup %r",
                             citation.name, reporter_cite)
                return out

        tried: List[str] = []
        max_results = 0
        for field_name, value in tiers:
            results = self._search_tier(field_name, value, court_filter)
            tried.append(f"{field_name}={value!r}->{len(results)}")
            if not results:
                continue
            max_results = max(max_results, len(results))
            best = self._pick_best_result(results, citation, reporter_cite)
            cands = [best] if best is not None else []
            # Doe class (2026.07.14): a published opinion can exist on CL
            # with its reporter cite never indexed (citation-lookup 404), and
            # the case-name search then surfaces near-duplicate clusters
            # (order entries, a slip copy) alongside the substantive published
            # opinion. On a 404 ONLY, walk the ranked name candidates and
            # prefer the first substantive text that carries the pincite;
            # published opinions rank first. Any other lookup status keeps the
            # single-candidate behavior (and the recorded call sequence).
            if getattr(citation, "_lookup_status", None) == 404:
                for _r in self._rank_name_candidates(results, citation):
                    if _r not in cands:
                        cands.append(_r)
                cands = cands[:3]
            if not cands:
                continue
            _pin404 = _pincite_from_citation(citation) if len(cands) > 1 else ""
            _first = None
            _chosen = None
            for _cand in cands:
                _cid = _cand.get("cluster_id") or _cand.get("id")
                if not _cid:
                    continue
                _txt = self._fetch_opinion_from_cluster(str(_cid))
                if not _txt:
                    continue
                _snap = (_cand, _txt, dict(self._last_cluster_meta))
                if _first is None:
                    _first = _snap
                if not _pin404 or _pincite_located(_txt, _pin404):
                    _chosen = _snap
                    break
            _chosen = _chosen or _first
            if _chosen is None:
                continue
            best, opinion_text, self._last_cluster_meta = _chosen
            # G12 (2026.07.14): on a 404 name-walk, reject a match that
            # resolves by generic name to a DIFFERENT case -- the matched
            # record carries other-reporter-family cites (not_on_record) AND
            # its decision YEAR contradicts the cited year by more than one
            # (the Brief C "In re H-Corp Holdings" -> In re A-Co miss,
            # cited 111 F.4th 111 (5th Cir. 2024) but matched a ~2019 record).
            # Scoped to PUBLISHED reporters: WL/LEXIS database cites (the RECAP
            # class) and the Doe class (empty citation field -> no_data)
            # never fire, so the recorded gate call sequence is unchanged.
            if opinion_text and getattr(citation, "_lookup_status", None) == 404:
                _addr_kind, _ = _cite_address_check(reporter_cite, best)
                _fam = _reporter_family(reporter_cite)
                _cy = _cited_year(citation)
                _my = _cluster_year(self._last_cluster_meta)
                if (_addr_kind == "not_on_record"
                        and (_fam or "") not in _DB_REPORTER_FAMS
                        and "lexis" not in (_fam or "")
                        and _cy is not None and _my is not None
                        and abs(_my - _cy) > 1):
                    _append_lookup_note(citation, (
                        "A case-name search matched a " + str(_my) + " opinion, "
                        "but the brief cites a " + str(_cy) + " decision and the "
                        "printed reporter address (" + reporter_cite + ") is not "
                        "on that record \u2014 likely a different case that shares "
                        "the name. Not resolved from free sources; verify on "
                        "Westlaw or Lexis."))
                    citation._lookup_addr_mismatch = True
                    continue
            if opinion_text:
                log.query = value
                log.num_results = len(results)
                self._cache_set_for(citation, opinion_text)
                out = self._record_success(citation, log, opinion_text)
                # Cite-address contradiction check (Part 2, 2026.07.09 -- the
                # citation-verifier repo's "Check Cite" lane): a name-tier win
                # never proved the ADDRESS.  If the matched record lists a
                # same-reporter-family cite at a DIFFERENT address, either the
                # printed cite is wrong (transposed digits, wrong volume) or
                # the match is a different case entirely -- identity
                # treatment, never a clean pass.  Resolution is KEPT (the
                # locked taxonomy renders it Identity Unconfirmed; a located
                # verbatim quote still overrides downstream).
                kind, rec_cite = _cite_address_check(reporter_cite, best)
                # Transitive contradiction (fake-06 class): lookup-200 already
                # proved the cited address belongs to a DIFFERENT case, and
                # this name-tier match does not carry the address either --
                # the printed cite cannot belong to this case.
                if (kind in ("not_on_record", "no_data")
                        and getattr(citation, "_lookup_addr_mismatch", False)):
                    _append_lookup_note(citation, (
                        "The matched case's record does not list the printed "
                        "cite either \u2014 identity unconfirmed."))
                    kind = "contradicted"
                    rec_cite = rec_cite or "(not on the matched record)"
                if kind == "contradicted":
                    _append_lookup_note(citation, (
                        "The matched case's reporter citation is " + rec_cite +
                        "; the brief prints " + reporter_cite +
                        " \u2014 check the cite as written."))
                    log.name_cite_ok = False
                    try:
                        citation._resolved_name_cite_ok = False
                        citation._cite_contradicted = True
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "not_on_record":
                    _append_lookup_note(citation, (
                        "The cited reporter address (" + reporter_cite +
                        ") does not appear on the matched case's "
                        "CourtListener record \u2014 possibly a parallel or "
                        "unindexed cite."))
                self._logs[citation.name] = log
                logger.debug(
                    "cl_resolver: resolved '%s' via %s=%r",
                    citation.name, field_name, value,
                )
                return out

        log.num_results = max_results
        if max_results == 0:
            log.rejection_reasons.append(
                "Not found in CourtListener. Tried: " + "; ".join(tried) + "."
            )
        else:
            log.rejection_reasons.append(
                "Results found but none matched jurisdiction/name or yielded "
                "opinion text. Tried: " + "; ".join(tried) + "."
            )
        # --- RECAP fallback: district-court orders/opinions that live only in
        # the docket (PACER), not the Opinions DB -- the common home of "WL"
        # cites.  Matched on case name + filing date + order/opinion type, so a
        # wrong-docket pull is unlikely; accepted only with real plain_text.
        recap_text, recap_url = self._resolve_via_recap(citation)
        if recap_text:
            self._cache_set_for(citation, recap_text)
            log.success = True
            log.opinion_url = recap_url
            log.url_source = "courtlistener-recap"
            if recap_url:
                self._urls[self._normalize_name(citation.name)] = recap_url
            try:
                nc = _name_or_cite_match(citation, recap_text)
            except Exception:  # noqa: BLE001
                nc = None
            # Transitive contradiction (Part 2): the cited address is known
            # to belong to a different case (lookup-200 caption mismatch), so
            # a RECAP win cannot be a clean identity pass.
            if nc and getattr(citation, "_lookup_addr_mismatch", False):
                _append_lookup_note(citation, (
                    "The cited address belongs to a differently-captioned "
                    "case on CourtListener \u2014 identity unconfirmed."))
                nc = False
            log.name_cite_ok = nc
            try:
                citation._resolved_name_cite_ok = nc
                citation._recap_url = recap_url
                citation._recap_source = "CourtListener (RECAP)"
            except Exception:  # noqa: BLE001
                pass
            pin = _pincite_from_citation(citation)
            log.full_text = recap_text  # Phase 7: untrimmed body, pre-trim
            trimmed, log.trimmed = _trim_to_pincite(
                recap_text, pin, footnote_ref=_pincite_footnote(citation))
            log.pincite = pin
            self._logs[citation.name] = log
            return trimmed

        self._record_failure(citation, log)
        self._logs[citation.name] = log
        return None

    def _resolve_via_recap(self, citation):
        """Resolve a citation to a CourtListener RECAP docket document.

        Returns (plain_text, opinion_url) on a confident match, else (None, None).
        Confidence gate: the document description must read like an ORDER/OPINION/
        MEMORANDUM, >=2 distinctive case-name tokens must appear in the result's
        docket slug, and -- when the cite carries a decision date -- the entry's
        filing date must match it (this is what disambiguates the specific cited
        order from the rest of the docket).  plain_text must be substantive."""
        if self._token is None:
            return None, None
        import datetime
        name = _clean_case_name(citation) or (getattr(citation, "name", "") or "")
        name = name.replace("*", "").strip()
        # Drop docket clutter ("..., No. 4:25-CV-2192") that defeats RECAP search.
        name = re.sub(r",?\s*(?:No\.|Civ\.?\s*Action\s*No\.|Case\s*No\.)\s.*$", "", name, flags=re.I).strip()
        if not name:
            return None, None
        want_date = _cite_date_iso(citation)
        # A decision date is a strong disambiguator: window the RECAP search to
        # the filing date so an abbreviated case name (which hurts full-text
        # recall, e.g. "Nat'l Liab." vs "National Liability") still lands the
        # right order.  Try the clean name, then the lead surname, both windowed.
        date_params = {}
        if want_date:
            d = datetime.date.fromisoformat(want_date)
            date_params = {
                "entry_date_filed_after": (d - datetime.timedelta(days=1)).isoformat(),
                "entry_date_filed_before": (d + datetime.timedelta(days=1)).isoformat(),
            }
        # Expand the most common legal abbreviations -- RECAP is full-text, so
        # "Nat'l Liab. Ins. Co." must become "National Liability Insurance
        # Company" to match.  (Reporter-agnostic; party-name abbreviations only.)
        expanded = name
        for ab, full in _RECAP_ABBREV.items():
            expanded = re.sub(r"\b" + re.escape(ab) + r"\b\.?", full, expanded, flags=re.I)
        toks_all = [t for t in re.split(r"[^A-Za-z]+", name)
                    if len(t) >= 4 and t.lower() not in _NAME_STOP]
        longest = max(toks_all, key=len) if toks_all else ""
        queries = [name]
        if expanded.lower() != name.lower():
            queries.append(expanded)
        if longest and longest.lower() not in (name.lower(), expanded.lower()):
            queries.append(longest)
        name_toks = {
            t.lower() for t in re.split(r"[^A-Za-z]+", expanded)
            if len(t) >= 3 and t.lower() not in _NAME_STOP
        }
        # Pool results across queries, then pick the best-named order/opinion.
        # The entry-date WINDOW (above) already constrains to the cited day +/-1,
        # so we rank by name-token overlap and prefer an exact date match.
        pooled, seen_ids = [], set()
        for q in queries:
            params = {"type": "rd", "q": q, "format": "json"}
            params.update(date_params)
            data = self._get(f"{BASE_URL}/search/", params=params)
            for r in (data.get("results", []) if (data and "error" not in data) else []):
                rid = r.get("id")
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                pooled.append(r)
        best, best_score = None, 0
        for r in pooled:
            desc = r.get("description") or ""
            if not re.search(r"\b(ORDER|OPINION|MEMORANDUM|RULING)\b", desc, re.I):
                continue
            slug = (r.get("absolute_url") or "").lower()
            matched = sum(1 for t in name_toks if t in slug)
            if matched < 2:
                continue
            score = matched + (1 if r.get("entry_date_filed") == want_date else 0)
            if score > best_score:
                best, best_score = r, score
        if best is None:
            return None, None
        rid = best.get("id")
        if not rid:
            return None, None
        doc = self._get(
            f"{BASE_URL}/recap-documents/{rid}/",
            params={"fields": "plain_text"},
        )
        text = (doc or {}).get("plain_text") or ""
        if len(text) < 200:
            return None, None
        # B1 (2026.07.29): reject docket sheets / interlocutory filings that
        # are not the cited opinion (esp. for published-reporter cites).
        if not _recap_body_acceptable(citation, text):
            return None, None
        url = "https://www.courtlistener.com" + (best.get("absolute_url") or "")
        return text, url

    # --- Tiered search internals -----------------------------------------

    def _search_tier(
        self, field_name: str, value: str, court_filter: str
    ) -> List[dict]:
        """Run one structured search tier; retry without the court filter on 0."""
        url = f"{BASE_URL}/search/"
        params = {field_name: value, "type": "o", "format": "json"}
        if court_filter:
            params["court"] = court_filter
        data = self._get(url, params=params)
        results = data.get("results", []) if (data and "error" not in data) else []
        if not results and court_filter:
            params.pop("court", None)
            data = self._get(url, params=params)
            results = data.get("results", []) if (data and "error" not in data) else []
        return results

    def _pick_best_result(
        self, results: List[dict], citation: Citation, reporter_cite: str
    ) -> Optional[dict]:
        """Choose a confidently-matching jurisdiction result, or None.

        Accept only on (1) an exact reporter-cite match, or (2) strong
        case-name token overlap (>=2 shared distinctive tokens covering a
        majority of the cited name).  CourtListener's fuzzy ``citation=``
        search returns many unrelated results, so there is deliberately NO
        first-result fallback -- returning None ("unable") is correct when no
        result is confidently the cited case.
        """
        norm_rep = _normalize_cite(reporter_cite)
        want_tokens = _case_name_tokens(citation)

        juris_ok: List[dict] = []
        for r in results:
            court_id = r.get("court_id") or r.get("court", "")
            if isinstance(court_id, str) and "/" in court_id:
                court_id = court_id.rstrip("/").rsplit("/", 1)[-1]
            if jurisdiction_matches(citation.jurisdiction, court_id):
                juris_ok.append(r)
        if not juris_ok:
            return None

        # 1. Exact reporter-cite match (authoritative)
        if norm_rep:
            for r in juris_ok:
                cites = r.get("citation") or []
                if isinstance(cites, str):
                    cites = [cites]
                if any(_normalize_cite(c) == norm_rep for c in cites):
                    return r

        # 2. Strong case-name overlap.  Require >=2 shared distinctive tokens
        #    (or all of them when the name has only one), AND that the shared
        #    tokens cover a majority of the cited name -- this rejects
        #    unrelated cases that merely share one common word.
        if want_tokens:
            need = 2 if len(want_tokens) >= 2 else 1
            coverage = 0.5 * len(want_tokens)
            best, best_overlap = None, 0
            for r in juris_ok:
                cn = (r.get("caseName") or r.get("case_name")
                      or r.get("caseNameFull") or r.get("case_name_full") or "")
                shared = want_tokens & _name_tokens(cn)
                if len(shared) < need or len(shared) < coverage:
                    continue
                if len(shared) > best_overlap:
                    best, best_overlap = r, len(shared)
            if best is not None:
                return best

        # No confident match -- report "unable" rather than a wrong case.
        return None

    def _rank_name_candidates(self, results: List[dict], citation: Citation) -> List[dict]:
        """Ranked jurisdiction-matching name candidates (Doe class).

        Same acceptance criteria as _pick_best_result's name-overlap step
        (>=2 shared distinctive tokens covering a majority of the cited
        name), ranked by (published status first, then token overlap; result
        order breaks ties). Used ONLY on a citation-lookup 404 -- the
        'published cite not indexed' shape -- so near-duplicate clusters of
        the SAME case can be walked for the substantive published opinion.
        """
        want_tokens = _case_name_tokens(citation)
        if not want_tokens:
            return []
        need = 2 if len(want_tokens) >= 2 else 1
        coverage = 0.5 * len(want_tokens)
        scored = []
        for r in results:
            court_id = r.get("court_id") or r.get("court", "")
            if isinstance(court_id, str) and "/" in court_id:
                court_id = court_id.rstrip("/").rsplit("/", 1)[-1]
            if not jurisdiction_matches(citation.jurisdiction, court_id):
                continue
            cn = (r.get("caseName") or r.get("case_name")
                  or r.get("caseNameFull") or r.get("case_name_full") or "")
            shared = want_tokens & _name_tokens(cn)
            if len(shared) < need or len(shared) < coverage:
                continue
            pub = str(r.get("status") or r.get("precedential_status") or "").lower()
            is_pub = 1 if pub.startswith(("pub", "prec")) else 0
            scored.append(((-is_pub, -len(shared)), r))
        scored.sort(key=lambda t: t[0])
        return [r for _k, r in scored]

    def _citation_lookup(self, reporter_cite: str,
                         citation=None) -> Optional[str]:
        """Resolve a reporter cite via CourtListener's exact citation-lookup
        endpoint.  Returns opinion text only on an exact normalized-cite match
        with a populated cluster; otherwise None.  When ``citation`` is given,
        a 200 cluster whose caption clearly mismatches the cited case name is
        rejected (lookup-200 name check, Part 2)."""
        data = self._post(f"{BASE_URL}/citation-lookup/?format=json",
                           {"text": reporter_cite})
        if not data:
            return None
        items = data if isinstance(data, list) else data.get("results", [])
        norm_rep = _normalize_cite(reporter_cite)
        for it in items:
            if not isinstance(it, dict):
                continue
            status = it.get("status")
            if status not in (200, "200", None):
                continue
            cands = [it.get("citation") or ""] + list(it.get("normalized_citations") or [])
            if norm_rep and not any(_normalize_cite(c) == norm_rep for c in cands if c):
                continue
            for c in (it.get("clusters") or []):
                cid = c.get("id") or c.get("cluster_id")
                if not cid:
                    continue
                txt = self._fetch_opinion_from_cluster(str(cid))
                if not txt:
                    continue
                if citation is not None:
                    cl_name = (getattr(self, "_last_cluster_meta", None)
                               or {}).get("case_name", "")
                    if _lookup_name_check(citation, cl_name) == "mismatch":
                        _append_lookup_note(citation, (
                            "CourtListener's record at '" + reporter_cite +
                            "' is captioned '" + cl_name + "', which does "
                            "not match the cited case name. The printed cite "
                            "likely belongs to a different case; check the "
                            "cite as written."))
                        try:
                            citation._lookup_addr_mismatch = True
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                return txt
        return None

    # --- Internal: cluster -> opinion text pipeline ----------------------

    @staticmethod
    def _sub_opinion_id(sub_op):
        """Extract the numeric opinion id from a sub_opinion entry (dict or URL)."""
        if isinstance(sub_op, dict):
            oid = sub_op.get("id")
            return str(oid) if oid else None
        if isinstance(sub_op, str):
            url_path = sub_op.split("?")[0].rstrip("/")
            slug = url_path.rsplit("/", 1)[-1]
            if slug.isdigit():
                return slug
        return None

    def _fetch_opinion_from_cluster(self, cluster_id: str) -> Optional[str]:
        """Return the text of a cluster's MOST SUBSTANTIVE opinion.

        Chunk 3: rank every sub-opinion by type (controlling first) then length
        and return the winner, instead of the first sub-opinion with text.  The
        selected opinion's URL/id/type is stashed on self._last_cluster_meta.
        """
        self._last_cluster_meta = {}
        cluster_url = f"{BASE_URL}/clusters/{cluster_id}/?format=json"
        cluster_data = self._get(cluster_url)
        if not cluster_data or "error" in cluster_data:
            return None

        cluster_web_url = make_opinion_url(cluster_data.get("absolute_url", "")) \
            or make_opinion_url(f"/opinion/{cluster_id}/")

        candidates = []
        for sub_op in cluster_data.get("sub_opinions", []):
            op_id = self._sub_opinion_id(sub_op)
            if not op_id:
                continue
            data = self._get(f"{BASE_URL}/opinions/{op_id}/?format=json")
            if not data or "error" in data:
                continue
            text = extract_opinion_text(data)
            if not text:
                continue
            op_type = (data.get("type") or "").lower()
            candidates.append((_opinion_rank(op_type, len(text)), op_id, op_type, text))

        if not candidates:
            return None

        candidates.sort(key=lambda c: c[0])
        _, op_id, op_type, text = candidates[0]
        self._last_cluster_meta = {
            "cluster_id": str(cluster_id),
            "opinion_id": str(op_id),
            "opinion_type": op_type,
            "opinion_url": cluster_web_url,
            "url_source": "courtlistener",
            "n_sub_opinions": len(candidates),
            "date_filed": (cluster_data.get("date_filed")
                           or cluster_data.get("dateFiled") or ""),
            # Cluster caption, for the lookup-200 name check (Part 2).
            "case_name": (cluster_data.get("case_name")
                          or cluster_data.get("caseName")
                          or cluster_data.get("case_name_full")
                          or cluster_data.get("caseNameFull") or ""),
        }
        return text

    def _fetch_opinion_text(self, opinion_id: str) -> Optional[str]:
        """Fetch a single opinion and extract its text."""
        url = f"{BASE_URL}/opinions/{opinion_id}/?format=json"
        data = self._get(url)
        if not data or "error" in data:
            return None
        return extract_opinion_text(data)

    # --- Chunk 3: success/failure metadata recording ---------------------

    def _record_success(self, citation, log, text):
        """Fold cluster retrieval metadata into the log, apply pincite/length
        trimming, cache the URL, and return the (possibly trimmed) text."""
        meta = dict(self._last_cluster_meta)
        pincite = _pincite_from_citation(citation)
        trimmed_text, was_trimmed = _trim_to_pincite(
            text, pincite, footnote_ref=_pincite_footnote(citation))
        log.full_text = text  # Phase 7: untrimmed body, pre-trim
        log.success = True
        log.opinion_url = meta.get("opinion_url", "")
        log.url_source = meta.get("url_source", "courtlistener" if meta else "")
        log.cluster_id = meta.get("cluster_id", "")
        log.opinion_id = meta.get("opinion_id", "")
        log.opinion_type = meta.get("opinion_type", "")
        log.pincite = pincite
        log.trimmed = was_trimmed
        if log.opinion_url:
            self._urls[self._normalize_name(citation.name)] = log.opinion_url
            ck = _cite_key(citation)
            if ck:
                self._urls[ck] = log.opinion_url
        self._meta[citation.name] = {**meta, "pincite": pincite, "trimmed": was_trimmed}
        # Identity gate on the FULL (untrimmed) text -- a pincite window can omit
        # the caption/reporter cite and false-fail identity (the Blue Bird bug,
        # 2026.06.30).  Stash on the citation so cite_check reads it instead of
        # recomputing on the trimmed window.
        try:
            nc = _name_or_cite_match(citation, text)
        except Exception:  # noqa: BLE001
            nc = None
        log.name_cite_ok = nc
        try:
            citation._resolved_name_cite_ok = nc
        except Exception:  # noqa: BLE001
            pass
        return trimmed_text

    def _record_failure(self, citation, log):
        """On a CL miss, attach a best-effort fallback link (NY Reporter for
        Slip Op (U), else Justia search).  success stays False."""
        url, source = fallback_opinion_url(citation)
        log.opinion_url = url
        log.url_source = source
        self._meta[citation.name] = {"opinion_url": url, "url_source": source, "fallback": True}

    # --- Chunk 3.5: order-agnostic multi-source fallback fetch -----------

    @staticmethod
    def _looks_like_opinion(text):
        """Cheap guard that fetched text is a real opinion, not a 404/landing page."""
        if not text or len(text) < 600:
            return False
        low = text.lower()
        hits = sum(k in low for k in (
            "plaintiff", "defendant", "court", "ordered", "opinion",
            "motion", "j.s.c", "appeal", "decided"))
        return hits >= 2

    def resolve_via_fallback(self, citation, fetcher, searcher=None):
        """Resolve a CL gap by fetching opinion text from a free source.
        fetcher(url) -> Optional[str] is injected (web_fetch in Cowork; requests
        locally).  Tries each constructable candidate (nycourts.gov, Justia (U))
        and returns the first that yields real opinion text, with provenance, or
        None when none resolve."""
        for source, url in fallback_candidates(citation):
            if source in ("txcourts_case_page", "txcourts_bc_index"):
                # Session E: navigation hints for the agent-driven gap loop,
                # never opinion bodies -- skip in the automated fetch path.
                continue
            try:
                text = fetcher(url)
            except Exception:
                text = None
            if self._looks_like_opinion(text or ""):
                pincite = _pincite_from_citation(citation)
                trimmed, was_trimmed = _trim_to_pincite(
                    text, pincite, footnote_ref=_pincite_footnote(citation))
                self._meta[citation.name] = {
                    "opinion_url": url, "url_source": source,
                    "pincite": pincite, "trimmed": was_trimmed, "fallback": True}
                self._urls[self._normalize_name(citation.name)] = url
                return {"text": trimmed, "opinion_url": url, "source": source,
                        "pincite": pincite, "trimmed": was_trimmed,
                        "full_text": text}
        # No constructable candidate resolved.  If a searcher is injected, fall
        # through to the reported-gap free-site search (Chunk 4).
        if searcher is not None:
            return self.resolve_via_reported_search(citation, searcher, fetcher)
        return None

    def resolve_via_reported_search(self, citation, searcher, fetcher,
                                    max_candidates=4):
        """Resolve a CL gap whose URL is NOT deterministically constructable
        (reported decisions) via a free-site search.  searcher(query, domains)
        -> list of URLs or list of {"url": ...} dicts (WebSearch in Cowork;
        injected).  fetcher(url) -> Optional[str].  Fetches the top results
        restricted to the free legal sites, extracts the opinion body, and
        accepts the first that passes the name/cite gate AND looks like a real
        opinion.  Returns the provenance dict (same shape as resolve_via_fallback)
        or None."""
        query = reported_search_query(citation)
        try:
            results = searcher(query, REPORTED_SEARCH_DOMAINS) or []
        except Exception:
            results = []
        for item in results[:max_candidates]:
            url = item if isinstance(item, str) else (item.get("url") or "")
            if not url:
                continue
            try:
                raw = fetcher(url)
            except Exception:
                raw = None
            if not raw:
                continue
            source = _source_for_url(url)
            body = extract_opinion_body(raw, source, url)
            if not self._looks_like_opinion(body):
                continue
            if not _name_or_cite_match(citation, body):
                continue
            pincite = _pincite_from_citation(citation)
            trimmed, was_trimmed = _trim_to_pincite(
                body, pincite, footnote_ref=_pincite_footnote(citation))
            self._meta[citation.name] = {
                "opinion_url": url, "url_source": source,
                "pincite": pincite, "trimmed": was_trimmed,
                "fallback": True, "via": "reported_search"}
            self._urls[self._normalize_name(citation.name)] = url
            return {"text": trimmed, "opinion_url": url, "source": source,
                    "pincite": pincite, "trimmed": was_trimmed,
                    "full_text": body, "via": "reported_search"}
        return None

    # --- Diagnostics -----------------------------------------------------

    def get_log(self, citation_name: str) -> Optional[ResolutionLog]:
        """Get the resolution log for a citation (for gray citation reports)."""
        return self._logs.get(citation_name)

    def get_all_logs(self) -> Dict[str, ResolutionLog]:
        """Get all resolution logs."""
        return dict(self._logs)

    def get_opinion_url(self, citation_name):
        """Resolvable opinion URL for a resolved citation (or '')."""
        log = self._logs.get(citation_name)
        if log and log.opinion_url:
            return log.opinion_url
        return self._urls.get(self._normalize_name(citation_name), "")

    def get_meta(self, citation_name):
        """Retrieval metadata (cluster/opinion id, type, url, trimming)."""
        return dict(self._meta.get(citation_name, {}))


# --------------------------------------------------------------------------
# Module-level convenience function
# --------------------------------------------------------------------------
def create_resolver(
    *,
    offline_opinions: Optional[Dict[str, str]] = None,
) -> CLResolver:
    """Create a CLResolver instance."""
    return CLResolver(offline_opinions=offline_opinions)
