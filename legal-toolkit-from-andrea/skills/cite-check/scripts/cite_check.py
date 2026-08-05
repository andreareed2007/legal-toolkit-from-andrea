"""
cite_check.py -- End-to-end cite-check orchestrator (Project: Isaacus
Integration, Plan v3).

Implements C1 (QA verification), C3 (enrichment brief parse), C4 (reranking
optional), and C7 (AI chunking if long).  C2 (analyst report surface) is in
cite_check_report.py.  C5 (jurisdiction auto-filter) is deferred.

Pipeline:
    1. Receive raw brief text + a callback to resolve opinion text for a
       citation (typically wired to caselaw-retriever's CourtListener layer).
    2. Chunk the brief only if it exceeds the AI threshold (C7).
    3. Enrich each chunk; pool ``external_documents`` across chunks with
       chunk-offset-adjusted spans (C3).
    4. For each citation, extract the proposition -- the sentence in the
       brief that the citation supports -- and call verify() against the
       resolved opinion text (C1).
    5. Return a list of CiteCheckResult dicts, one per citation.

The orchestrator is intentionally decoupled from CourtListener.  Callers
pass a ``resolve_opinion_text`` function that maps a citation dict to
opinion text.  That keeps the existing caselaw-retriever integration
untouched until ready.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

import isaacus_helpers as helpers
import isaacus_chunker as chunker_mod
import cc_proposition as ccprop   # consolidated reporter-agnostic extractor
import cc_detect_eyecite as cc_detect  # eyecite detection backbone (Phase 1, 2026.07.03)
import cc_quote_matcher  # graded quote fidelity (Part 3, 2026.07.09, from rlfordon/citation-verifier, MIT)
import cc_application  # application-sentence detector (2026.08.04, locked design)


# --------------------------------------------------------------------------
# Input doubling gate (2026.07.29, Session B)
# --------------------------------------------------------------------------
# Some source PDFs carry a duplicate text layer (an OCR-style copy over the
# original export layer). Both copies get extracted, so every page appears
# twice and the citation/instance count roughly doubles -- burning the
# resolve/gap budget, producing false "duplicate" cards, and crossing id-chains
# between copies (Brief D diagnosis, Finding 0). The converter
# (pdf-to-cowork-txt v2026.07.29+) now drops the duplicate layer, but this gate
# is the belt-and-suspenders: if a doubled document reaches build from ANY
# source, refuse rather than emit a corrupt report.
from difflib import SequenceMatcher as _DblSM

_DBL_QUOTE_MAP = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
                  "\u201e": '"', "\u201a": "'", "`": "'", "\u00b4": "'"}
_DBL_DASH_MAP = {"\u2014": "-", "\u2013": "-", "\u2212": "-", "\u2012": "-"}
_DOUBLING_REFUSE_THRESHOLD = 0.10   # refuse if >10% of page blocks are doubled


class DoubledInputError(Exception):
    """Raised when the brief text is page-doubled (duplicate text layer)."""


def _dbl_fold(text: str) -> str:
    for a, b in _DBL_QUOTE_MAP.items():
        text = text.replace(a, b)
    for a, b in _DBL_DASH_MAP.items():
        text = text.replace(a, b)
    text = text.replace("*", "").replace("_", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _dbl_block_is_doubled(block: str) -> bool:
    """True if one page/block's text is substantially internally duplicated,
    either line-level ('A A' on one row) or page-level (first half ~ second
    half)."""
    if len(block.strip()) < 200:
        return False
    lines = block.split("\n")

    def _line_doubled(ln: str) -> bool:
        folded = _dbl_fold(ln)
        if len(folded) < 20:
            return False
        w = folded.split(" ")
        n = len(w)
        if n < 6:
            return False
        m = n // 2
        return _DblSM(None, " ".join(w[:m]), " ".join(w[m:])).ratio() >= 0.90

    long_lines = [l for l in lines if len(_dbl_fold(l)) >= 20]
    if long_lines:
        frac = sum(1 for l in long_lines if _line_doubled(l)) / len(long_lines)
        if frac >= 0.40:
            return True
    ne = [i for i, l in enumerate(lines) if l.strip()]
    if len(ne) >= 6:
        best = 0.0
        lo = max(1, int(len(ne) * 0.35)); hi = min(len(ne) - 1, int(len(ne) * 0.65) + 1)
        for k in range(lo, hi):
            sidx = ne[k]
            top = _dbl_fold("\n".join(lines[:sidx])); bot = _dbl_fold("\n".join(lines[sidx:]))
            if len(top) < 50 or len(bot) < 50:
                continue
            r = _DblSM(None, top, bot).ratio()
            if r > best:
                best = r
        if best >= 0.85:
            return True
    return False


def detect_input_doubling(text: str) -> float:
    """Fraction of page blocks that are internally near-duplicated. Splits on
    the converter's page markers (<!-- Page N of M --> or === PAGE N of M ===);
    falls back to blank-line-delimited blocks when no markers are present."""
    blocks = re.split(r"<!--\s*Page \d+ of \d+\s*-->|=== PAGE \d+ of \d+ ===", text)
    blocks = [b for b in blocks if len(b.strip()) >= 200]
    if len(blocks) < 3:
        blocks = [b for b in re.split(r"\n\s*\n", text) if len(b.strip()) >= 200]
    if not blocks:
        return 0.0
    doubled = sum(1 for b in blocks if _dbl_block_is_doubled(b))
    return doubled / len(blocks)


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------
@dataclass
class Citation:
    """A citation discovered in a brief."""
    name: str                           # e.g., "Smith v. Jones"
    type: Optional[str] = None          # e.g., "case", "statute"
    jurisdiction: Optional[str] = None
    span_start: Optional[int] = None    # char offset in the original brief
    span_end: Optional[int] = None
    pinpoints: List[dict] = field(default_factory=list)
    proposition: str = ""               # sentence that cites this authority
    # True when no verifiable proposition could be extracted (a genuine string
    # cite with no recoverable governing assertion).  The report renders a
    # "review required" note instead of a blank.  Set in run() after refinement.
    proposition_review: bool = False
    # Application-sentence build (2026.08.04): structural kind of the
    # extracted proposition (cc_proposition extract() "kind").  The detector
    # exempts parenthetical kinds -- they describe the CITED case.
    prop_kind: str = "host"
    raw: dict = field(default_factory=dict)
    # TOA enrichment.  Populated by _to_citation() when the body name fuzzy-
    # matches an entry in the parsed Table of Authorities.  Shape:
    #   {"name": str, "cite": str, "pages": list[str], "key": str}
    toa_match: Optional[dict] = None
    # Per-instance expansion (locked spec items 5/6/9).  Set by
    # _expand_to_citations when one authority is cited at multiple pages:
    # each pincited page becomes its own checkable Citation.
    pincite: str = ""                   # page number for THIS instance
    cite_text: str = ""                 # full citation as written in the brief (eyecite full_span)
    reporter_cite: Optional[dict] = None  # {"volume","reporter","page"} parsed by eyecite
    pin_cite: str = ""                  # pincite as written (e.g. "852, n.1", "329-30")
    is_short_form: bool = False         # this instance is a short-cite, not the full form
    occurrence_index: int = 0
    occurrence_count: int = 1
    # Adverse-signal flag (locked spec item 5).  True when this instance is
    # introduced by a contrary signal (but see / contra / but cf.) or a
    # distinguish/reject treatment -- a low support score is then EXPECTED.
    adverse_signal: bool = False
    adverse_signal_token: str = ""
    # Fix 9 (Finding 5): True when this instance is a string-cite member
    # ("see also X") whose sentence carries a quotation placed with a
    # DIFFERENT authority. Such an instance is graded for support only and
    # is never branded FABRICATED for another authority's quote.
    quote_support_only: bool = False
    # B4 (2026.07.29): True when this instance's quoted span is nested inside a
    # "(quoting/citing X)" parenthetical -- graded support-only, never
    # fabricated against the citing case.
    quote_nested_attribution: bool = False


@dataclass
class CiteCheckResult:
    """Result of verifying one citation."""
    citation: Citation
    opinion_resolved: bool
    passage: str = ""
    score: float = 0.0
    inextractability_score: float = 1.0
    supports: bool = False
    notes: str = ""                     # human-readable status
    search_url: str = ""                # CourtListener search URL for manual follow-up
    search_detail: str = ""             # "Searched for X, found Y, rejected because Z"
    # True when the citation appears in the body but was NOT found in the
    # parsed Table of Authorities.  Forces a Flagged verdict in the report.
    body_only: bool = False
    opinion_url: str = ""       # resolvable link to the opinion actually used
    opinion_source: str = ""    # courtlistener|nycourts_reporter|justia|findlaw|txcourts
    # Chunk 4 verdict-layer inputs:
    verbatim_quote: str = ""        # quoted span from the brief's proposition, if any
    quote_matched: bool = False     # verbatim_quote located (near-verbatim) in the opinion
    # Graded quote fidelity (Part 3, 2026.07.09): additive reviewer note
    # for CLOSE/FABRICATED quotes; never changes the verdict taxonomy.
    quote_note: str = ""
    # Phase 1 of the report redesign (2026.07.14): per-quote fidelity results
    # (G3 -- ALL quoted spans checked, worst governs) and the B1 severity
    # signal. quote_fabricated=True means at least one quoted span graded
    # FABRICATED; the Phase 3 tier layer maps this to CRITICAL. Old pickles
    # lack these attrs -- read with getattr().
    quote_results: Optional[list] = None
    quote_fabricated: bool = False
    opinion_chars: int = 0          # length of opinion text verified (thin-text guard)
    name_cite_ok: Optional[bool] = None  # passed the name/cite identity gate
    # Pincite rule (locked spec #10; item 3).  pincite_given: the cite supplied
    # a pinpoint page.  pincite_found: True located / False given-but-not-located
    # / None no pincite.  pincite_note: human-readable detail for the report.
    pincite_given: bool = False
    pincite_found: Optional[bool] = None
    pincite_note: str = ""
    # Phase 2 (2026.07.04): CourtListener citation-lookup status for this
    # cite (200 exact / 300 ambiguous / 400 bad reporter / 404 not found;
    # None = no batched lookup covered it) + the reviewer note it generated.
    lookup_status: Optional[int] = None
    lookup_note: str = ""
    # Phase 4 (2026.07.04): where the supporting passage was located, and the
    # optional Answer Extractor second opinion on close-call verdicts.
    passage_page: str = ""                    # nearest preceding *N star page of verify()'s top chunk
    second_opinion: str = ""                  # kanon-answer-extractor answer (Flagged/Somewhat/DNS only)
    second_opinion_score: Optional[float] = None


def locate_passage_page(opinion_text, qa):
    """Phase 4 (audit 3.4): star page where verify()'s top chunk sits.

    ``qa["span"]`` carries the top chunk's char offsets into the SAME text
    verify() scored (the pincite-trimmed window), so the offset indexes
    correctly. Returns '' when the copy has no star pagination -- the card
    then simply omits the located-page line (honest, not an error).

    2026.07.04 (footnote fix): when the verification window carries an
    appended opinion-footnote tail (behind cl_resolver._FN_SENTINEL) and the
    supporting passage sits inside it, return the special value
    ``"footnotes"`` -- the nearest preceding star page would be misleading
    (endnotes follow the last page marker)."""
    try:
        import cl_resolver as _c
        span = (qa or {}).get("span")
        if span and opinion_text and span[0] is not None:
            fn_at = opinion_text.find(_c._FN_SENTINEL)
            if fn_at >= 0 and span[0] >= fn_at:
                return "footnotes"
            return _c.star_page_before(opinion_text, span[0])
    except Exception:  # noqa: BLE001
        pass
    return ""


_SECOND_OPINION_VERDICTS = ("flagged", "somewhat", "does_not_support")


def apply_second_opinion(r, opinion_text, client=None):
    """Phase 4 (optional leg): Kanon Answer Extractor second opinion.

    Runs ONLY on close-call verdicts (Flagged / Somewhat Supports / Does Not
    Support) and stores the extracted answer + score on the result for the
    report to show BESIDE the classifier verdict. Never changes the verdict
    itself -- verify() semantics are locked."""
    import cite_check_report as _rep  # deferred: avoids import cycle
    try:
        v = _rep._verdict(r)
    except Exception:  # noqa: BLE001
        return
    if v not in _SECOND_OPINION_VERDICTS:
        return
    prop = (getattr(r.citation, "proposition", "") or "").strip()
    if not prop or not opinion_text:
        return
    ans = helpers.extract_answer(
        "What does this opinion hold about: %s" % prop,
        opinion_text, client=client)
    if ans and ans.get("answer"):
        r.second_opinion = _sentence_bound_passage(ans["answer"])
        r.second_opinion_score = ans["score"]


ResolveOpinion = Callable[[Citation], Optional[str]]
# Mode A fallback: maps a Citation to {"text","opinion_url","source",...} or None,
# invoked only when resolve_opinion_text (CourtListener) returns nothing.
FallbackResolve = Callable[[Citation], Optional[dict]]


# --------------------------------------------------------------------------
# Chunk 4: verbatim-quote detection feeding the verdict layer.  A citing
# sentence that puts language in quotation marks is asserting that the opinion
# said exactly that; if we can locate the quote in the resolved opinion, that
# is confirmed support regardless of the model's confidence score (this is the
# Connaughton case, under-scored at 0.56 despite a verbatim quote).
# --------------------------------------------------------------------------
_VERBATIM_QUOTE_RE = re.compile(r'[\u201c"]([^\u201d"]{25,400})[\u201d"]')

_BACKTICK_QUOTE_RE = re.compile(r"`([^`\u2019']{2,400})['\u2019`]")

def _paired_quote_spans(proposition):
    """Pair quote marks in DOCUMENT ORDER (mark 0 opens, mark 1 closes, ...),
    so a closing mark pairs with ITS OWN opener -- never with the next
    quotation's opener.  Returns quoted spans (any length) in brief order.
    Shared by every quote extractor.  The old 25-400-char regex paired quote
    1's CLOSER with quote 2's OPENER and captured the unquoted connective
    between two short quotations (card 58's garbled quote; the bogus 139/146
    MISQUOTE).  Curly directionality is honored because marks are consumed
    left to right; the >=25-char floor is applied by callers to GRADING, not
    to pairing."""
    txt = proposition or ""
    marks = [i for i, ch in enumerate(txt) if ch in ('"', '“', '”')]
    spans = []
    for a, b in zip(marks[0::2], marks[1::2]):
        spans.append(txt[a + 1:b].strip())
    return spans


def _normalize_quote(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


_DANGLING_QUOTE_RE = re.compile(r'[\u201c"]([^\u201d"]{25,400})\s*$')


def _quotes_unbalanced(s):
    """True when the count of quote characters is ODD -- i.e. an opening quote
    lacks a closer (the truncated-proposition / magic-wand class). The dangling
    fallback below must fire ONLY here; a BALANCED short quote (e.g. \"gap[]\")
    otherwise makes the fallback grab the tail after the closing mark as if it
    were a fresh opener (2026.07.14, Brief C Cit 17 false-fabrication)."""
    return (s.count('"') + s.count('\u201c') + s.count('\u201d')) % 2 == 1


def extract_verbatim_quote(proposition):
    """Longest quoted span (>=25 chars) in a citing proposition, or ''."""
    best = ""
    for q in _paired_quote_spans(proposition):
        if len(q) >= 25 and len(q) > len(best):
            best = q
    if best:
        return best
    if not _quotes_unbalanced((proposition or "").strip()):
        return best
    # Fallback (2026.07.13, Brief C magic-wand): a proposition truncated at a
    # sentence break can carry an OPENING quote with no closing mark --
    # American-style punctuation puts the period INSIDE the quote, so
    # sentence segmentation drops the closing mark into the next sentence.
    # Without recovery, extract_verbatim_quote returns '' and the FABRICATED
    # quote-fidelity check is skipped entirely, so a fabricated "quotation"
    # rides thematic support to a false Verified. Recover the dangling tail
    # so verify_quote() still runs against the opinion.
    m = _DANGLING_QUOTE_RE.search((proposition or "").strip())
    if m:
        return m.group(1).strip()
    return best


def extract_verbatim_quotes(proposition):
    """ALL quoted spans (>=25 chars) in a citing proposition, in brief order.

    G3 (2026.07.14): the legacy extract_verbatim_quote() returned only the
    LONGEST span, so a fabricated second quotation next to a genuine longer
    one was never checked. Falls back to the dangling-tail recovery when no
    closed span exists (the Brief C magic-wand class)."""
    spans = [q for q in _paired_quote_spans(proposition) if len(q) >= 25]
    if not spans and _quotes_unbalanced((proposition or "").strip()):
        m = _DANGLING_QUOTE_RE.search((proposition or "").strip())
        if m:
            spans = [m.group(1).strip()]
    return spans


# Phase 6 (B1, 2026.07.14) -- quote DETECTION layer.
# --------------------------------------------------------------------------
_STARTS_BRACKET_CAP_RE = re.compile(r'^\s*\[[A-Za-z]\][a-z]')


def _has_quote_char(s):
    s = s or ""
    return ('"' in s) or ('“' in s) or ('”' in s)


def extract_short_quotes(proposition):
    """BALANCED short quoted spans (1-24 chars) the >=25-char extractor
    skips -- e.g. the "gap[]" in Brief C Cit 17. Quote marks are paired in
    document order (mark 0 opens, mark 1 closes, ...) so the connective
    BETWEEN two real quotes (e.g. the " and " joining two long quotations) is
    never captured as a short quote. Balanced pairs only, so this never grabs a
    sentence tail and the unbalanced dangling fallback stays suppressed (the
    Cit 17 guard). Requires >=2 alphanumeric chars. Deduped, brief order."""
    spans = []
    for content in _paired_quote_spans(proposition):
        if (1 <= len(content) <= 24
                and sum(c.isalnum() for c in content) >= 2
                and content not in spans):
            spans.append(content)
    return spans


def recover_sentence_quotation(proposition, opinion_text, *,
                               license_signal=False):
    """Recover a whole-sentence quotation whose OPENING quote mark was dropped
    during proposition extraction (Brief C Cits 2 & 5).

    Fires ONLY when a positive quotation signal survives that
    extract_verbatim_quotes() could not capture -- a stray/trailing quote mark,
    or a Bluebook bracket-capitalized opener ([I]n, [T]he) -- and ONLY returns
    a quote when the whole candidate matches the opinion verbatim (exact or
    permitted bracket/ellipsis alterations; similarity >= 0.98). A paraphrase
    that was never marked as a quote never matches verbatim, so this can NEVER
    manufacture a FABRICATED. It does not use the dangling-tail grab the Cit 17
    balanced guard suppresses."""
    prop = (proposition or "").strip()
    if not prop or not opinion_text:
        return ""
    if not (_has_quote_char(prop) or _STARTS_BRACKET_CAP_RE.match(prop)):
        return ""
    cand = prop
    for qch in ('"', '“', '”'):
        cand = cand.replace(qch, "")
    cand = cand.strip()
    if len(cand) < 25:
        return ""
    qv = cc_quote_matcher.verify_quote(cand, opinion_text,
                                       license_signal=license_signal)
    if qv.result.value == "VERBATIM" and qv.similarity >= 0.98:
        return cand
    return ""


_REPORTER_CITE_RE = re.compile(r'\d{1,4}\s+[A-Z][\w.]*\s+\d{1,4}')


def _citationish(seg):
    """A segment dominated by a reporter citation (few prose words)."""
    seg = (seg or "").strip()
    if not seg:
        return True
    if _REPORTER_CITE_RE.search(seg):
        return len(re.findall(r'\b[a-z]{4,}\b', seg)) <= 3
    return False


def _sentence_bound_passage(passage, max_chars=600):
    """Phase 6 (B6): make a QA passage material and sentence-bounded.

    Conservative: if the passage OPENS with a run of reporter-citation strings
    ("264 S.W. 576; ... 74 S.W.2d 1046; ...") before any substantive clause,
    advance to the first real prose sentence after that run; otherwise return
    the passage unchanged. Always falls back to the input (never empties it)."""
    if not passage:
        return passage
    s = passage.strip()
    lines = s.split("\n")
    while len(lines) > 1 and _citationish(lines[0]):
        lines = lines[1:]
    s = "\n".join(lines).strip()
    clauses = s.split(";")
    while len(clauses) > 1 and _citationish(clauses[0]):
        clauses = clauses[1:]
    s = ";".join(clauses).strip()
    m = re.match(r'^[^.]*\d{1,4}\s+[A-Z][\w.]*\s+\d{1,4}[^.]*\.\s+(?=[A-Z])', s)
    if m and m.end() < len(s):
        s = s[m.end():].strip()
    return s[:max_chars].strip()


def quote_in_opinion(quote, opinion_text):
    """True if a verbatim quote appears (near-verbatim) in the opinion text.
    Normalizes whitespace/punctuation; tolerates minor elision via an 8-word
    leading shingle so a brief's '...' omissions still match."""
    if not quote or len(quote) < 25 or not opinion_text:
        return False
    nq = _normalize_quote(quote)
    no = _normalize_quote(opinion_text)
    if not nq:
        return False
    if nq in no:
        return True
    words = nq.split()
    if len(words) >= 8:
        return " ".join(words[:8]) in no
    return False


# --------------------------------------------------------------------------
# Non-case citation filter
# --------------------------------------------------------------------------
_CASE_TYPES = {"case_law", "case", "judicial_decision", "decision"}


# Document/instrument keywords that signal a NON-case reference even when the
# enricher mistypes it as a "decision" (e.g., "February 2026 Cayman Order",
# "Cayman Consent Order").  A real case carries a versus / "In re" / "Matter
# of" structural signal; these instrument references do not.
_NON_CASE_DOC_KEYWORDS = (
    "order", "agreement", "exhibit", "affidavit", "affirmation",
    "declaration", "stipulation", "consent", "complaint", "memorandum",
    "notice of", "amendment", "indenture", "deed", "lease", "deposition",
)
_CASE_STRUCTURE_RE = re.compile(
    r"\bv\.?\s|\bvs\.?\s|\bin re\b|\bmatter of\b|\bex parte\b|\bestate of\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Adverse-signal detection (locked spec item 5).  A citation introduced by a
# Bluebook contrary signal -- "but see", "contra", "but cf." -- or a treatment
# verb marking the case as distinguished/rejected is cited AS CONTRARY: a low
# support score is then the EXPECTED, correct outcome, not a citation error.
# Reporter- and case-agnostic: keys only on the generic signal phrase preceding
# the citation token, never on any case name or reporter (prime directive).
# --------------------------------------------------------------------------
_ADVERSE_SIGNAL_RE = re.compile(
    r"(?:^|[;.,(—]\s*)"                         # clause / parenthetical start
    r"(but\s+see(?:\s*,?\s*e\.g\.)?|but\s+cf\.?|contra|see\s+contra)"
    r"[\s,.]*$",                                     # signal sits just before the cite
    re.IGNORECASE,
)
_ADVERSE_TREATMENT_RE = re.compile(
    r"\b(distinguish(?:ed|ing|es)?|reject(?:ed|ing|s)?|"
    r"criticiz(?:ed|ing|es)?|overrul(?:ed|ing|es)?|abrogat(?:ed|ing|es)?)\b",
    re.IGNORECASE,
)


def _adverse_signal(chunk_text, in_chunk_start):
    """Return the contrary signal/treatment token governing the citation at
    ``in_chunk_start`` within ``chunk_text``, or "" if the cite is not adverse.

    Two reporter-agnostic triggers:
      1. a Bluebook contrary signal ("but see", "contra", "but cf.") sitting
         immediately before the citation token; and
      2. a treatment verb ("distinguishing", "rejecting", "overruled") in the
         short clause that directly introduces the cite.
    Conservative by design (locked spec #8): it inspects only the immediate
    left window and stops at the nearest clause boundary, so an ordinary
    supporting cite -- or a treatment verb in a PRIOR sentence -- is never
    mislabeled.
    """
    if not chunk_text or in_chunk_start is None or in_chunk_start <= 0:
        return ""
    left = chunk_text[max(0, in_chunk_start - 60):in_chunk_start]
    m = _ADVERSE_SIGNAL_RE.search(left)
    if m:
        return m.group(1).strip()
    clause = re.split(r"[;.—]", left)[-1]
    m2 = _ADVERSE_TREATMENT_RE.search(clause)
    if m2:
        return m2.group(1).strip()
    return ""


def _resolve_ext_name(ext: dict, chunk_text: str = "") -> str:
    """Return the plain-text name for an external_document, resolving a span
    dict against ``chunk_text`` when needed."""
    name_raw = ext.get("name") or ext.get("title") or ""
    if isinstance(name_raw, dict):
        n_start = name_raw.get("start")
        n_end = name_raw.get("end")
        if (
            n_start is not None
            and n_end is not None
            and 0 <= n_start < n_end <= len(chunk_text)
        ):
            return chunk_text[n_start:n_end]
        return ""
    return name_raw if isinstance(name_raw, str) else ""


def _looks_like_non_case_doc(name: str) -> bool:
    """True if the name reads like a litigation instrument or order rather than
    a reported decision: carries a document keyword and no case-structure
    signal (no versus / "In re" / "Matter of")."""
    if not name:
        return False
    if _CASE_STRUCTURE_RE.search(name):
        return False
    low = name.lower()
    return any(kw in low for kw in _NON_CASE_DOC_KEYWORDS)


def _is_case_citation(ext: dict, chunk_text: str = "") -> bool:
    """Return True if this external_document is a case law citation.

    Order of checks:
    1. Hard negative -- instrument/order references (e.g. "Cayman Consent
       Order") are never cases, even when the enricher mistypes them as
       "decision".
    2. The enricher's ``type`` field (most reliable when present).
    3. Heuristic -- case names contain "v." or "v " (fallback when type is
       absent or generic).
    """
    name_text = _resolve_ext_name(ext, chunk_text)

    # 1. Hard negative: litigation instruments / orders are not cases.
    if name_text and _looks_like_non_case_doc(name_text):
        return False

    # 2. Trust an explicit enricher type.
    doc_type = (ext.get("type") or "").lower().strip()
    if doc_type in _CASE_TYPES:
        return True
    if doc_type and doc_type not in ("", "unknown"):
        # Has a non-case type explicitly set
        return False

    # 3. Fallback: check the name for a "v." pattern.
    if not name_text:
        return False
    return " v. " in name_text or " v " in name_text


def _classify_non_case(ext: dict) -> str:
    """Classify a non-case reference for the report section."""
    name = str(ext.get("name") or ext.get("title") or "").lower()
    doc_type = (ext.get("type") or "").lower()

    if any(kw in name for kw in ["r. civ. p.", "r. app. p.", "r. evid.", "rule"]):
        return "procedural rule"
    if any(kw in name for kw in ["local rule"]):
        return "local rule"
    if any(kw in name for kw in ["§", "code", "u.s.c.", "stat."]):
        return "statute"
    if any(kw in name for kw in ["consent order", "order"]):
        return "court order"
    if any(kw in name for kw in [
        "agreement", "contract", "notice", "deposition", "msa",
        "exhibit", "affidavit", "affirmation", "declaration",
        "stipulation", "amendment", "indenture", "deed", "lease",
    ]):
        return "document reference"
    if doc_type:
        return doc_type
    return "reference"


# --------------------------------------------------------------------------
# Brief preprocessor -- blockquote-prefix strip
# --------------------------------------------------------------------------
_BLOCKQUOTE_LINE_RE = re.compile(r"(?m)^>\s?")


def _strip_blockquote_prefixes(text: str) -> str:
    """Remove Markdown blockquote prefixes (``> ``) from every line.

    The ``pdf-to-cowork-txt`` converter wraps Table-of-Authorities entries in
    Markdown blockquote lines.  The ``> `` is a presentational artifact, not
    semantic, and it defeats every heading-pattern regex downstream.  Strip
    it once at the top of the pipeline so every subsequent regex sees plain
    text.
    """
    if not text:
        return text
    return _BLOCKQUOTE_LINE_RE.sub("", text)


# --------------------------------------------------------------------------
# Emphasis / unicode normalization (INPUT hygiene)
#
# The pdf-to-cowork-txt converter wraps each font span in Markdown emphasis
# markers.  When an italic case name contains a glyph in a different font run
# (a curly apostrophe) or wraps across a line break, this produces artifacts
# like ``Int'l*,*Inc.*`` and ``Alden Glob. Value*\n*Recovery ...`` that defeat
# the CourtListener resolver and inflate duplicate counts.
#
# CRITICAL: never run these over the WHOLE document before the TOA is parsed.
# The line-rejoin rule fires across the ``**Cases Page(s)**\n*100 & 130 ...*``
# boundary and glues the header onto the first authority (regressing the TOA
# from 29 -> 28).  Apply only to (a) the post-TOA body text and (b) individual
# citation name strings.
# --------------------------------------------------------------------------
def _fold_unicode_punct(text: str) -> str:
    return (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("–", "-").replace("—", "-")
                .replace(" ", " ").replace(" ", " "))


def _normalize_body_text(text: str) -> str:
    """Scrub emphasis artifacts from BODY text (after the TOA is parsed).

    Safe here because the body has no TOA structure to corrupt.  Cleans the
    names the enricher emits and the sentences proposition-extraction sees.
    """
    if not text:
        return text
    text = _fold_unicode_punct(text)
    # Repair italic runs split by an interior punctuation bridge: ``*,*`` -> ``,``.
    text = re.sub(r"\*([^\w*\n]{1,3})\*", r"\1", text)
    # Rejoin a name split across a line break inside an italic span.
    text = re.sub(r"\*[ \t]*\n[ \t]*\*", " ", text)
    # Strip residual emphasis markers (bold / italic / bold-italic).
    text = re.sub(r"\*{1,3}", "", text)
    return text


# --------------------------------------------------------------------------
# Line-stitch (INPUT hygiene) — Chunk 1 detection fix (2026.06.25)
#
# pdfplumber / pdf-to-cowork-txt emit one md line per visual PDF line, so case
# citations are routinely split across line breaks ("38 N.Y.3d" | "1, 12") and,
# at page boundaries, across page furniture ("7 A.D.3d" | page-no | FILED/NYSCEF
# | "352, 355").  _normalize_body_text only rejoins *italic-span* breaks, so the
# enricher never sees these cites as contiguous and silently drops them (e.g.
# SNS Bank, Donohue, half of Beal in the Brief A gold set).  Stitch strips page
# furniture + bare page numbers and joins the body into one continuous stream so
# every citation instance is detectable.  Run AFTER the TOA is excised.
# --------------------------------------------------------------------------
_FURNITURE_LINE_RE = re.compile(r"""(?ix)^\s*(
    \*{0,2}FILED:.*  | NYSCEF\s+DOC\..*  | RECEIVED\s+NYSCEF:.*  | INDEX\s+NO\..*  |
    <!--\s*Page\s+\d+.*  | ={5,}.*  | FILE:.*  | SOURCE:.*  | EXTRACTION\ METHOD:.*  |
    OUTPUT\ FORMAT:.*  | TOTAL\ PAGES:.*  | CONVERTED:.*  | CONTENT\ GAPS:.*
)$""")


def _stitch_wrapped_lines(text: str) -> str:
    """Strip page furniture + bare page numbers, then rejoin ONLY true
    mid-sentence / mid-citation / page-break line wraps with a space, while
    PRESERVING blank-line paragraph breaks as a double newline.

    An earlier version flattened the entire brief to a single line.  That
    destroyed every paragraph boundary, so downstream sentence segmentation
    (which keys on ".!?" and blank-line breaks) had only ".!?" to work with;
    in citation-dense text -- reporter periods masked to "_", abbreviations
    everywhere -- it found NO boundary and returned multi-thousand-char
    "sentences" that swallowed many citations, giving them all the same
    garbled proposition (Brief B [158], cites 13-20).

    This version keeps structure:
      * a blank line between two text lines is a PARAGRAPH break -> "\n\n";
      * a line gap that spanned page furniture / a bare page number is a PAGE
        break -- the sentence continues across it -> joined with a space, so a
        citation split across a page break stays contiguous (recovers the
        Brief A page-split instances);
      * an ordinary line wrap inside a paragraph is joined with a space, so a
        citation or case name split across a line wrap stays contiguous.
    Paragraph breaks survive for the segmenter; citations stay contiguous for
    the enricher and the de-citing regexes (which do not cross "\n").
    """
    if not text:
        return text
    # Walk raw lines, classifying the gap that precedes each content line.
    content = []  # list of (line, gap) where gap in {None, "para", "page", "wrap"}
    saw_blank = saw_furniture = False
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            saw_blank = True
            continue
        if _FURNITURE_LINE_RE.match(s) or re.fullmatch(
            r"[ivxlcdm]+|\d{1,3}", s, re.IGNORECASE
        ):
            # Page furniture / standalone page number: a PAGE break.  The
            # surrounding sentence continues across it -- do not let it read as
            # a paragraph boundary.
            saw_furniture = True
            continue
        if not content:
            gap = None
        elif saw_furniture:
            gap = "page"   # soft join: sentence continues across the page break
        elif saw_blank:
            gap = "para"   # true paragraph break
        else:
            gap = "wrap"   # mid-paragraph line wrap
        content.append((s, gap))
        saw_blank = saw_furniture = False
    parts = []
    for i, (s, gap) in enumerate(content):
        if i == 0:
            parts.append(s)
        elif gap == "para":
            parts.append("\n\n" + s)
        else:
            parts.append(" " + s)
    out = "".join(parts)
    out = re.sub(r"[ \t]+", " ", out)     # collapse intra-line whitespace only
    out = re.sub(r" *\n *", "\n", out)    # trim spaces around newlines
    out = re.sub(r"\n{3,}", "\n\n", out)  # at most one blank line between paras
    return out.strip()


def _dedupe_by_span(cits: "Sequence[Citation]", window: int = 8) -> "List[Citation]":
    """Cite-check dedup: merge ONLY re-detections of the same physical citation
    (overlapping/adjacent spans from chunk overlap).  Distinct spans are distinct
    instances and are KEPT — for a cite-check, every in-text citation is its own
    checkable item.  Replaces _dedupe_citations, which collapsed all occurrences
    of a case to one (dropping 17 of 47 instances in the Brief A gold set).
    """
    kept: List[Citation] = []
    for c in sorted(cits, key=lambda x: (x.span_start if x.span_start is not None else 0)):
        s = c.span_start if c.span_start is not None else 0
        is_dup = False
        for k in kept:
            ks = k.span_start if k.span_start is not None else 0
            same = (getattr(c, "name", "") or "").lower() == (getattr(k, "name", "") or "").lower()
            if same and abs(s - ks) <= window:
                is_dup = True
                break
        if not is_dup:
            kept.append(c)
    return kept


def _clean_citation_name(name: str) -> str:
    """Normalize a single citation NAME for resolution / dedup / display.

    Defensive twin of :func:`_normalize_body_text` for the name string alone:
    folds unicode punctuation, removes emphasis markers, and collapses the
    line breaks that the converter left inside wrapped case names.
    """
    if not name:
        return name
    name = _fold_unicode_punct(name)
    name = re.sub(r"\*([^\w*\n]{1,3})\*", r"\1", name)
    name = re.sub(r"\*[ \t]*\n[ \t]*\*", " ", name)
    name = re.sub(r"\*{1,3}", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# --------------------------------------------------------------------------
# Proposition refinement (P4)
#
# The enricher span usually points AT the citation.  When a citation follows
# its proposition as a string cite ("... must plead specific facts.  Beal
# Sav. Bank v. Sommer, 8 N.Y.3d 318 (2007)."), the sentence at the span is the
# bare cite, which verify() cannot match -> false "does not support".  When the
# extracted sentence is essentially just a citation, fall back to the preceding
# sentence (the assertion the authority supports).
# --------------------------------------------------------------------------
def _is_bare_proposition(s: str) -> bool:
    """True when ``s`` has too little substantive prose to verify against
    (fewer than 4 lowercase prose words of >=3 letters)."""
    if not s:
        return True
    return len(re.findall(r"\b[a-z]{3,}\b", s)) < 4


_RUNAWAY_PROP_CHARS = 700   # a real proposition is essentially never longer


# Leading citation furniture sometimes survives sentence segmentation when an
# abbreviation (e.g. "Credit Agr.") is misread as a sentence boundary, leaving a
# candidate proposition that opens with a cross-reference ("§ 12.2, a plaintiff
# must plead ...") or a Bluebook signal.  Strip such a leading run so the
# governing assertion -- not the furniture -- is what we ship.
_LEAD_FURNITURE = re.compile(
    rf"^\s*(?:{ccprop._SIGNAL}|{ccprop._RECORD})[\s,;:.]*"
)


def _strip_leading_furniture(s: str) -> str:
    prev = None
    while s and s != prev:
        prev = s
        s = _LEAD_FURNITURE.sub("", s, count=1).lstrip(" ,;:.")
    return s


# "X cites/relies on <Case>, for the proposition that Z" -- the governing
# assertion is Z.  When a long case name is mis-split at an abbreviation period
# the candidate can open with a dangling "v. <Name>, for the proposition that ..."
# tail.  Gated on a citing-furniture prefix (cites / relies on / a "v."
# case-name fragment) within the lead-in so a normal sentence that merely
# contains the phrase is left untouched.
_FOR_PROP_LEADIN = re.compile(
    r"^.{0,180}?(?:\bcit(?:es?|ing)\b|\breli(?:es|ed)\s+on\b|\brelying\s+on\b|\bv\.)\s"
    r".{0,120}?\bfor the proposition that\s+",
    re.I | re.S,
)


def _strip_citing_leadin(s: str) -> str:
    return _FOR_PROP_LEADIN.sub("", s, count=1).strip()


def _preceding_substantive_sentence(brief: str, start: int, max_hops: int = 4) -> str:
    """Walk backward sentence by sentence from ``start`` to the first substantive
    (non-bare, non-runaway) sentence -- the governing assertion a string-cite
    cluster supports.  In a string-cite cluster the sentence immediately before
    the cite is often ANOTHER cite, so a single look-back is not enough; this
    keeps walking until it finds real prose, stopping at a paragraph/heading
    break or after ``max_hops`` hops.  Returns "" if none is substantive."""
    # Do not cross the paragraph break preceding the citation: the supporting
    # assertion lives in the same paragraph as its string-cite cluster.
    floor = brief.rfind("\n\n", 0, start)
    pos = start
    for _ in range(max_hops):
        boundary = brief.rfind(". ", 0, pos)
        if boundary <= 0 or boundary <= floor:
            return ""
        cand = _strip_leading_furniture(_sentence_at(brief, max(0, boundary - 2)))
        if (cand and len(cand) <= _RUNAWAY_PROP_CHARS
                and not _is_bare_proposition(cand)):
            return cand
        pos = boundary          # step back past this (bare) sentence and retry
    return ""


def _refine_proposition(brief: str, prop: str, start: Optional[int]) -> str:
    """Finalize the proposition.  A SUBSTANTIVE proposition (real prose survives
    de-citing) is returned unchanged.  A proposition that is pure citation
    furniture -- a string cite, bare case name, or Rule/record cite -- is never
    shipped (it would hand the verify step a meaningless score, "garbage in,
    garbage out"): we try the preceding sentence, and if that is also furniture
    we return "" so the report honestly shows no proposition rather than a
    confident-looking citation string."""
    # A runaway (> _RUNAWAY_PROP_CHARS) is a segmentation failure -- typically
    # the legacy _sentence_at fallback merging several sentences and a leaked
    # section heading across a boundary cc_proposition's backstop would have
    # clamped.  Treat it, like a bare cite, as not-shippable.
    if prop:
        prop = _strip_citing_leadin(prop)
    runaway = bool(prop) and len(prop) > _RUNAWAY_PROP_CHARS
    if not runaway and not _is_bare_proposition(prop):
        return prop
    # prop is a bare citation string / case name, or a runaway -- attempt
    # preceding-sentence recovery; if that is also bare/runaway, return "" so
    # the report honestly shows NO proposition rather than a confident-looking
    # citation string the verify step would score as meaningless garbage.
    if start is not None:
        recovered = _preceding_substantive_sentence(brief, start)
        if recovered:
            return _strip_citing_leadin(recovered)
    return ""


def _verify_and_correct_span(
    text: str,
    name: str,
    span_start: Optional[int],
    span_end: Optional[int],
    window: int = 150,
) -> tuple:
    """Verify that ``text[span_start:span_end]`` contains the citation name.

    If it does, return the span unchanged.  If not, search a ±``window``
    character region for the citation's key tokens and re-anchor the span
    to the actual name location.  This corrects the enricher's offset
    drift (Root Cause 2) so proposition extraction lands on the right
    sentence.

    Returns ``(span_start, span_end)`` — corrected or original.
    """
    if span_start is None or span_end is None:
        return span_start, span_end
    if span_start < 0 or span_end > len(text):
        return span_start, span_end

    # Build key tokens from the citation name for matching.
    # For "Beal Sav. Bank v. Sommer" we want at least the surname tokens
    # ("Beal", "Sommer").  For short-cites ("Beal") the single token.
    name_tokens = [
        t for t in re.sub(r"[^\w\s]", " ", name.lower()).split()
        if len(t) >= 3 and t not in _TOA_STOP_TOKENS
    ]
    if not name_tokens:
        return span_start, span_end

    # Check whether the current span already contains the key tokens.
    span_text = text[span_start:span_end].lower()
    if all(t in span_text for t in name_tokens[:2]):
        return span_start, span_end  # span is fine

    # Span is misaligned — search the surrounding window.
    search_start = max(0, span_start - window)
    search_end = min(len(text), span_end + window)
    search_region = text[search_start:search_end]

    # Try to find the first key token (typically the lead surname).
    target = name_tokens[0]
    # Case-insensitive word-boundary search.
    pat = re.compile(re.escape(target), re.IGNORECASE)
    for m in pat.finditer(search_region):
        candidate_abs = search_start + m.start()
        # Verify: does the surrounding text also contain the other key
        # tokens (if any)?  Check a narrow window around the match.
        ctx_start = max(0, candidate_abs - 20)
        ctx_end = min(len(text), candidate_abs + len(name) + 60)
        ctx = text[ctx_start:ctx_end].lower()
        if len(name_tokens) <= 1 or any(t in ctx for t in name_tokens[1:3]):
            corrected_start = candidate_abs
            corrected_end = min(len(text), candidate_abs + len(target))
            logging.debug(
                "cite_check: span corrected for '%s': [%d:%d] -> [%d:%d]",
                name, span_start, span_end, corrected_start, corrected_end,
            )
            return corrected_start, corrected_end

    # Could not find the name in the window — return original span.
    logging.warning(
        "cite_check: span misaligned for '%s' at [%d:%d] but could not "
        "re-anchor within ±%d chars.",
        name, span_start, span_end, window,
    )
    return span_start, span_end


def _sentence_at(brief: str, pos: int) -> str:
    """Extract the sentence surrounding ``pos`` using boundary walking."""
    left = pos
    for i in range(pos - 1, max(pos - 2000, -1), -1):
        ch = brief[i]
        if ch in ".!?" and i + 1 < len(brief) and brief[i + 1].isspace():
            # Skip abbreviations: period followed by a single uppercase
            # letter and another period (e.g., "S.W.2d", "Inc.") — check
            # if the character before the period is a single letter.
            if i >= 1 and brief[i - 1].isalpha():
                # Could be an abbreviation — check if preceded by another
                # period within 3 chars (e.g., "S.W." "U.S.").
                if i >= 2 and brief[i - 2] == ".":
                    continue
                # Single-letter followed by period (e.g., "v.") — skip.
                if i >= 2 and not brief[i - 2].isalpha():
                    # "v." or similar single-letter abbreviation
                    if brief[i - 1].islower():
                        continue
            left = i + 2
            break
        if ch == "\n" and i > 0 and brief[i - 1] == "\n":
            left = i + 1
            break
    else:
        left = max(pos - 400, 0)

    right = pos
    for i in range(pos, min(pos + 2000, len(brief))):
        ch = brief[i]
        # Paragraph boundary — always a sentence boundary.
        if ch == "\n" and i + 1 < len(brief) and brief[i + 1] == "\n":
            right = i
            break
        if ch in ".!?":
            # Check if this period is followed by a footnote number rather
            # than true end-of-sentence. If the next non-space chars are
            # digits followed by a newline, this is a mid-sentence footnote
            # marker — skip it and keep walking.
            rest = brief[i + 1:i + 10].lstrip()
            if rest and rest[0].isdigit():
                continue
            # Skip abbreviations (e.g., "v.", "S.W.", "Inc.").
            if i + 1 < len(brief) and brief[i + 1].isalpha():
                continue  # "Inc.v" or "S.W.2d" — not end of sentence
            # Single-letter abbreviation like "v. "
            if i >= 1 and brief[i - 1].isalpha() and (i < 2 or not brief[i - 2].isalpha()):
                if brief[i - 1].islower():
                    continue
            right = i + 1
            break
    else:
        right = min(pos + 400, len(brief))

    return brief[left:right].strip()


_FOOTS_CACHE: dict = {}


def _footnotes_for(brief: str):
    """find_footnotes(), memoized per brief text (43 cites -> 1 scan)."""
    key = (id(brief), len(brief))
    if key not in _FOOTS_CACHE:
        _FOOTS_CACHE.clear()  # one brief at a time; never let texts accumulate
        _FOOTS_CACHE[key] = ccprop.find_footnotes(brief)
    return _FOOTS_CACHE[key]


def _extract_proposition(brief: str, start: Optional[int], end: Optional[int],
                         is_id: bool = False,
                         meta: Optional[dict] = None) -> str:
    """Return the sentence containing the citation span.

    For footnote-style briefs, the enricher's span points into the
    footnote block.  This function detects that case, locates the
    corresponding body-text marker, and extracts the body sentence
    instead.  Falls back to direct boundary walking when footnote
    detection fails.
    """
    if start is None or end is None or start < 0 or end > len(brief):
        return ""

    # --- Primary: consolidated reporter-agnostic extractor ---
    # Handles string-cites (walk-back), trailing holding/quoting parentheticals,
    # de-citing, statutes, and page-furniture, for any jurisdiction.  Falls back
    # to the legacy footnote/sentence logic only if it returns nothing substantive.
    # 2026.07.04 (footnote fix): footnote blocks are computed and PASSED --
    # extract() was silently running with foots=[] on the live path, so a
    # citation inside a brief footnote pulled a (wrong) body sentence instead
    # of the footnote's own content (the Brief A Alliance Network card).
    try:
        _r = ccprop.extract(brief, start, foots=_footnotes_for(brief), is_id=is_id)
        _p = (_r or {}).get("proposition", "")
        if _p and not ccprop._is_bare(_p):
            if meta is not None:
                meta["kind"] = (_r or {}).get("kind", "host") or "host"
            return _p
    except Exception:
        logging.exception("cite_check: cc_proposition.extract failed; using legacy path")

    # --- Footnote detection (legacy fallback) ---
    fn_num = _find_footnote_number(brief, start)
    if fn_num is not None:
        marker_pos = _find_body_marker(brief, fn_num, start)
        if marker_pos is not None and marker_pos > 0:
            # marker_pos points at the footnote number (e.g., "19" in
            # "action.19\n").  The sentence ends just before the number.
            # Walk backward from the marker to find the sentence start;
            # use the marker position as the right boundary.
            sent_end = marker_pos  # exclude the footnote number
            # Strip any trailing period/punctuation that precedes the
            # footnote number — it belongs to the sentence.
            if sent_end > 0 and brief[sent_end - 1] in ".!?":
                sent_end_with_punct = sent_end
            else:
                sent_end_with_punct = sent_end
            # Walk backward for sentence start.
            left = 0
            for i in range(marker_pos - 1, max(marker_pos - 2000, -1), -1):
                ch = brief[i]
                if ch in ".!?" and i + 1 < len(brief) and brief[i + 1].isspace():
                    # Check if this is a footnote marker period (digit after)
                    rest_after = brief[i + 1:i + 10].lstrip()
                    if rest_after and rest_after[0].isdigit():
                        continue  # footnote marker, not sentence end
                    # Skip abbreviations
                    if i >= 1 and brief[i - 1].isalpha():
                        if brief[i - 1].islower() and (i < 2 or not brief[i - 2].isalpha()):
                            continue
                    left = i + 2
                    break
                if ch == "\n" and i > 0 and brief[i - 1] == "\n":
                    left = i + 1
                    break
            return brief[left:sent_end_with_punct].strip()

    # --- Fallback: direct boundary walking (original behavior) ---
    return _sentence_at(brief, start)


def _dedupe_citations(cits: Sequence[Citation]) -> List[Citation]:
    """Collapse duplicate citations, keying on ``(name.lower(), type)`` AND
    on ``toa_match["key"]`` when present.

    Two dedup dimensions:
    1. **Name-based:** same ``(name.lower(), type)`` — standard dedup.
    2. **TOA-key-based:** when two citations share the same ``toa_match``
       key (e.g., short-cite "Beal" and full-cite "Beal Sav. Bank v.
       Sommer" both match TOA key "beal sav bank sommer"), merge them.
       Keep the full-cite form (longer name) and fold the short-cite's
       proposition into it if the full-cite has a bare/empty proposition.

    The same authority is often detected in multiple chunks or at multiple
    citation points in the argument.  Keep the first occurrence (which
    carries the earliest proposition) and drop later duplicates.
    """
    # Pass 1: merge by toa_match key.  Map toa_key -> best Citation.
    toa_winners: dict = {}   # toa_key -> Citation
    non_toa: List[Citation] = []

    for c in cits:
        if c.toa_match and c.toa_match.get("key"):
            toa_key = c.toa_match["key"]
            if toa_key in toa_winners:
                existing = toa_winners[toa_key]
                # Keep the longer name (full-cite form).
                if len(c.name) > len(existing.name):
                    # New citation has the fuller name — swap, but
                    # preserve the better proposition.
                    if _is_bare_proposition(c.proposition) and not _is_bare_proposition(existing.proposition):
                        c.proposition = existing.proposition
                    toa_winners[toa_key] = c
                else:
                    # Existing has the fuller name — fold proposition
                    # if ours is better.
                    if _is_bare_proposition(existing.proposition) and not _is_bare_proposition(c.proposition):
                        existing.proposition = c.proposition
                # Either way, the duplicate is absorbed — skip it.
            else:
                toa_winners[toa_key] = c
        else:
            non_toa.append(c)

    # Recombine: toa winners first (in original order), then non-toa.
    merged_order: List[Citation] = []
    seen_toa_keys: set = set()
    for c in cits:
        if c.toa_match and c.toa_match.get("key"):
            toa_key = c.toa_match["key"]
            if toa_key not in seen_toa_keys:
                seen_toa_keys.add(toa_key)
                merged_order.append(toa_winners[toa_key])
        else:
            merged_order.append(c)

    # Pass 2: standard name-based dedup on the merged list.
    seen: set = set()
    unique: List[Citation] = []
    for c in merged_order:
        name = c.name if isinstance(c.name, str) else ""
        key = (name.lower().strip(), c.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def _qa_note(qa: dict) -> str:
    """Human-readable status line for a resolved citation.

    DISPLAY ONLY.  The report's ``_verdict()`` classifies purely from the
    numeric fields (score, inextractability_score, supports, body_only), so
    this wording does not affect any verdict.  Reconstructed 2026.06.16 after
    the TOA-Heading-Fix edit dropped the original definition; no intact source
    survived in any backup.
    """
    score = qa.get("score", 0.0) or 0.0
    inext = qa.get("inextractability_score", 1.0)
    if inext is None:
        inext = 1.0
    supports = bool(qa.get("supports"))
    if supports and score > 0.8 and inext < 0.5:
        return f"Opinion supports the proposition (confidence {score:.2f})."
    if inext >= 0.7:
        return f"Opinion does not support the cited proposition (inextractability {inext:.2f})."
    if inext >= 0.5 or score < 0.3:
        return ("Support is weak or the holding may be distinguishable "
                f"(confidence {score:.2f}, inextractability {inext:.2f}); review recommended.")
    if supports:
        return f"Opinion supports the proposition (confidence {score:.2f})."
    return (f"Support unclear (confidence {score:.2f}, inextractability {inext:.2f}); "
            "review recommended.")



# --------------------------------------------------------------------------
# TOA parser -- extract Table of Authorities as a keyed index
# --------------------------------------------------------------------------

# Stop tokens for case-name normalization.  Strip these before computing
# token overlap so "Beal" matches "Beal Sav. Bank v. Sommer" cleanly.
_TOA_STOP_TOKENS = {
    "v", "vs", "the", "of", "and", "in", "a", "an", "for",
    "co", "inc", "llc", "llp", "ltd", "corp", "n", "sa",
    # 2026.07.03 (known-bug 5.1): "In re Smith" vs "Matter of Smith" must
    # tokenize identically or the TOA fuzzy match misses. Aligned with
    # cl_resolver._NAME_STOPWORDS.
    "re", "matter",
}

# Match a trailing page list at the end of a TOA entry.  Page numbers can
# be comma-separated, range-separated (e.g., "12-15"), or include "passim".
_TOA_PAGE_TAIL_RE = re.compile(
    r"(\d+(?:\s*[-–]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-–]\s*\d+)?)*"
    r"|passim)\s*$",
    re.IGNORECASE,
)


def _normalize_case_name(name: str) -> str:
    """Lowercase + strip punctuation + drop stop tokens.  Used as TOA key
    and as the token bag for fuzzy matching."""
    if not name:
        return ""
    # Drop everything after the first comma -- that's the reporter cite
    # (e.g., "Beal Sav. Bank v. Sommer, 8 N.Y.3d 318" -> "Beal Sav. Bank v. Sommer").
    name = name.split(",", 1)[0]
    # Lowercase, replace non-word chars with spaces
    name = re.sub(r"[^\w\s]", " ", name.lower())
    # Collapse whitespace
    tokens = [t for t in name.split() if t and t not in _TOA_STOP_TOKENS]
    return " ".join(tokens)


def _token_overlap(a: str, b: str) -> float:
    """Token overlap ratio, denominated by the SHORTER token set.

    Using ``min(|A|, |B|)`` as the denominator means a short-cite ("Beal")
    that is a subset of a full cite ("Beal Sav. Bank v. Sommer") scores 1.0.
    Returns 0.0 when either side has no tokens after normalization.
    """
    ta = set(_normalize_case_name(a).split())
    tb = set(_normalize_case_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# Strict TOA section delimiter -- TOA starts at the heading and ends at
# the first body heading (Preliminary Statement, Introduction, etc.).
_TOA_HEADING_RE = re.compile(
    r"\b(?:(?:TABLE|INDEX)\s+OF\s+(?:AUTHORITIES|CITATIONS)"
    r"|AUTHORITIES\s+CITED)\b",
    re.IGNORECASE,
)

# Dotted-leader page tail (e.g., "......... 27" or ". . . . passim").  Used
# by the structural TOA fallback below when no heading text matches.
_TOA_LEADER_TAIL_RE = re.compile(r"\.(?:\s*\.){3,}\s*(?:\d|passim)", re.IGNORECASE)

# Non-adversary captions carry no " v. " (Estate of X, Matter of X, In re X,
# Ex parte X, ...). The old " v. "/"In re"-only gate silently dropped them
# from the TOA index, producing false "in body, not in TOA" flags -- the
# Brief C as-filed run reported Estate of Doe and In re H-Corp Holdings
# Mgmt. as body-only though both were in the brief's Table of Authorities.
_NON_ADVERSARY_CAPTION_RE = re.compile(
    r"\b(?:In re|In the Matter of|Matter of|In the Estate of|Estate of|"
    r"Ex parte|Application of|Petition of|In the Interest of|"
    r"Guardianship of|Conservatorship of|Marriage of)\b",
    re.IGNORECASE,
)


def _parse_toa(brief: str) -> tuple:
    """Extract the Table of Authorities and parse each entry.

    Returns ``(index, toa_span)`` where *index* is a dict keyed on
    normalized case name::

        {
            "beal sav bank sommer": {
                "name": "Beal Sav. Bank v. Sommer",
                "cite": "8 N.Y.3d 318 (2007)",
                "pages": ["5", "12", "18"],
                "key": "beal sav bank sommer",
            },
            ...
        }

    and *toa_span* is ``(start, end)`` character offsets of the winning
    TOA block (heading through the last entry before the first body
    heading), or ``None`` if no TOA was found.  The span is used by the
    caller to excise the TOA from the text before enrichment.

    The brief is assumed to already have blockquote prefixes stripped.
    Returns ``({}, None)`` if no TOA is found or if no entries parse.
    """
    if not brief:
        return {}, None

    # "TABLE OF AUTHORITIES" also appears in the Table of Contents (and
    # sometimes in back matter / a running footer).  Locking onto the FIRST
    # occurrence parses the TOC's dotted-leader lines and yields zero entries
    # -- the failure that poisoned the Brief A MTD run.  Instead, try every
    # occurrence and keep the block that produces the most parseable entries.
    best: dict = {}
    best_span: Optional[tuple] = None
    found = False
    for _m in _TOA_HEADING_RE.finditer(brief):
        found = True
        idx, block_end = _parse_toa_block(brief, _m.end())
        if len(idx) > len(best):
            best = idx
            best_span = (_m.start(), block_end)
    if not best:
        # Structural fallback: no recognizable heading matched (or the matched
        # heading yielded 0 entries), but a Table/Index of Authorities may
        # still be present as a dotted-leader case-name cluster near the front
        # (garbled or missing heading text).  Re-enables name-rescue etc.
        fb_start = _find_toa_structural_start(brief)
        if fb_start is not None:
            idx, block_end = _parse_toa_block(brief, fb_start)
            if idx:
                best = idx
                best_span = (fb_start, block_end)
    if found and not best:
        logging.warning(
            "cite_check._parse_toa: TOA heading found but 0 entries parsed"
        )
    return best, best_span


def _find_toa_structural_start(brief: str) -> Optional[int]:
    """Locate a dotted-leader case-name cluster near the front of the brief
    when no TOA/Index heading text matched.  Returns the character offset at
    which to begin TOA-block parsing, or ``None`` if no plausible cluster is
    found.  Conservative by design: it requires a genuine cluster of
    dotted-leader page-tail lines, at least one of which carries a case-name
    signal."""
    if not brief:
        return None
    front = brief[: max(len(brief) // 2, 4000)]
    leader_positions = [m.start() for m in _TOA_LEADER_TAIL_RE.finditer(front)]
    if len(leader_positions) < 4:
        return None
    for pos in leader_positions:
        line_start = brief.rfind("\n", 0, pos) + 1
        prev_start = (brief.rfind("\n", 0, line_start - 1) + 1
                      if line_start > 0 else 0)
        window = brief[prev_start: pos + 1]
        if (" v. " in window or " v " in window
                or _NON_ADVERSARY_CAPTION_RE.search(window)):
            return prev_start
    return None


def _parse_toa_block(brief: str, heading_end: int) -> tuple:
    """Parse TOA entries in the block beginning at ``heading_end`` and ending
    at the first body heading after it.  Returns ``(keyed_index, toa_end)``
    where *toa_end* is the character position of the first body heading
    after the TOA (or ``len(brief)`` if none found).  Empty dict if the
    block has no parseable entries."""
    # TOA ends at the first body heading after this heading occurrence.
    toa_end = len(brief)
    for heading in _BODY_START_HEADINGS:
        pat = re.compile(
            r"\n\s*(?:[#*]+\s*)?(?:[IVXLC]+\.\s+|[A-Z]\.\s+)?" + re.escape(heading),
            re.IGNORECASE,
        )
        for m in pat.finditer(brief):
            if m.start() > heading_end:
                toa_end = min(toa_end, m.start())
                break

    toa_block = brief[heading_end:toa_end]
    if not toa_block.strip():
        return {}, toa_end

    index: dict = {}

    # Heuristic: each TOA entry sits on its own line OR is separated by a
    # blank line.  Split on blank lines first; then for each chunk, look
    # for the "v." pattern that marks a case-name line.
    # Many TOAs use dotted leaders -- collapse runs of dots before parsing.
    cleaned = re.sub(r"\.{2,}", "  ", toa_block)
    # Lines that contain " v. " or " v " are candidate case entries.
    # Join wrapped lines: collapse line breaks inside an entry until the
    # entry terminates with a page tail.
    # Subsection headers ("Cases", "Statutes", "Rules", "Other Authorities")
    # flush the buffer and are skipped -- they should never attach to an
    # entry.
    _SUBHEADERS = {
        "cases", "statutes", "rules", "regulations",
        "other authorities", "secondary authorities", "treatises",
        "constitutional provisions",
    }
    raw_entries: List[str] = []
    buffer: List[str] = []

    def _flush():
        if buffer:
            raw_entries.append(" ".join(buffer).strip())
            buffer.clear()

    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            _flush()
            continue
        # Subsection header: flush, then skip.  Handle Markdown bold/italic
        # markers (**Cases**) and a trailing "Page(s)" / "Pages" column label
        # (e.g., "**Cases Page(s)**") so the header is recognized and does not
        # glue onto the first authority (the Brief A "100 & 130 Biscayne" miss).
        _hdr = re.sub(r"[*:]", "", stripped).strip().lower()
        _hdr = re.sub(r"\s*page\(s\)$|\s*pages?$", "", _hdr).strip()
        if _hdr in _SUBHEADERS:
            _flush()
            continue
        buffer.append(stripped)
        # If this line ends with a page tail, flush.
        if _TOA_PAGE_TAIL_RE.search(stripped):
            _flush()
    _flush()

    for entry in raw_entries:
        # Skip subsection headers like "Cases", "Statutes", "Rules".
        if len(entry) < 10:
            continue
        # Require a case-name signal: " v. ", " v ", or "In re ..." for
        # in-rem cases (common in bankruptcy and corporate matters).
        has_v = " v. " in entry or " v " in entry
        has_nonadversary = bool(_NON_ADVERSARY_CAPTION_RE.search(entry))
        if not has_v and not has_nonadversary:
            continue

        # Pages: trailing match.
        pages: List[str] = []
        page_match = _TOA_PAGE_TAIL_RE.search(entry)
        if page_match:
            tail = page_match.group(1)
            entry_without_pages = entry[:page_match.start()].rstrip()
            if tail.lower() == "passim":
                pages = ["passim"]
            else:
                pages = [p.strip() for p in re.split(r"\s*,\s*", tail) if p.strip()]
        else:
            entry_without_pages = entry

        # Split case name from reporter cite at the comma that PRECEDES the
        # volume number, e.g.:
        #   "SNS Bank N.V. v. Citibank, N.A., 7 A.D.3d 352" -> split at ", 7"
        #   "Alliance Network, LLC v. Sidley Austin LLP, 43 Misc. 3d 848" -> ", 43"
        # Falls back to last comma if no volume pattern is present.
        cite_split = re.search(r",\s+(?=\d)", entry_without_pages)
        if cite_split:
            name_part = entry_without_pages[:cite_split.start()].strip()
            cite_part = entry_without_pages[cite_split.end():].strip().rstrip(".").strip()
        elif "," in entry_without_pages:
            # No volume number found -- fall back to last comma.
            last = entry_without_pages.rfind(",")
            name_part = entry_without_pages[:last].strip()
            cite_part = entry_without_pages[last + 1:].strip().rstrip(".").strip()
        else:
            name_part = entry_without_pages.strip()
            cite_part = ""

        name_part = name_part.replace("*", "").strip()
        key = _normalize_case_name(name_part)
        if not key:
            continue
        # First entry wins on duplicate keys.
        if key in index:
            continue
        index[key] = {
            "name": name_part,
            "cite": cite_part,
            "pages": pages,
            "key": key,
        }

    return index, toa_end


def _find_toa_match_by_cite(reporter_cite: Optional[dict], toa_index: dict) -> Optional[dict]:
    """TOA rescue by reporter cite (2026.07.04). When the fuzzy NAME match
    misses (eyecite name truncated by a docket-number prefix, a repeat full
    cite, or a clause between name and cite), the volume-reporter-page group
    is a near-unique key into the TOA's cite strings. Comparison is
    punctuation- and whitespace-insensitive ("128 F. Supp. 3d 972" ==
    "128 F.Supp.3d 972")."""
    if not reporter_cite or not toa_index:
        return None
    if not reporter_cite.get("volume") or not reporter_cite.get("page"):
        return None
    key = "".join(str(reporter_cite.get(k) or "") for k in ("volume", "reporter", "page"))
    key = re.sub(r"[\s.,]", "", key.lower())
    if len(key) < 4:
        return None
    for entry in toa_index.values():
        cite = re.sub(r"[\s.,]", "", (entry.get("cite") or "").lower())
        if key and key in cite:
            return entry
    return None


def _find_toa_match(name: str, toa_index: dict, threshold: float = 0.7) -> Optional[dict]:
    """Fuzzy-match ``name`` against the TOA index by token overlap.

    Returns the best matching TOA entry dict, or None if no entry scores
    at or above ``threshold``.  Ties are broken by longer TOA name (more
    specific match).
    """
    if not toa_index or not name:
        return None
    best_score = 0.0
    best_entry: Optional[dict] = None
    best_len = 0
    for entry in toa_index.values():
        score = _token_overlap(name, entry["name"])
        if score < threshold:
            continue
        toa_len = len(entry["name"])
        if score > best_score or (score == best_score and toa_len > best_len):
            best_score = score
            best_entry = entry
            best_len = toa_len
    return best_entry


# --------------------------------------------------------------------------
# Brief preprocessor -- strip non-argument sections
# --------------------------------------------------------------------------

# Headings that mark the start of substantive body text (after front matter).
# Order matters: we take the first match found after the TOA.
_BODY_START_HEADINGS = [
    "PRELIMINARY STATEMENT",
    "INTRODUCTION",
    "STATEMENT OF THE CASE",
    "STATEMENT OF FACTS",
    "FACTUAL BACKGROUND",
    "BACKGROUND",
    "NATURE OF THE ACTION",
    "SUMMARY OF ARGUMENT",
    "ARGUMENT",
]

# Headings / markers that signal the end of substantive argument.
_BACK_MATTER_MARKERS = [
    r"\nDated:\s",                      # Signature block start
    r"\nRespectfully submitted",        # Signature block variant
    r"\nCERTIFICATE OF SERVICE",
    r"\nCERTIFICATE OF COMPLIANCE",
]


def _strip_non_argument_sections(brief: str) -> str:
    """Remove front/back matter, keeping body argument (incl. Preliminary
    Statement and Facts -- those sections cite cases too).

    Strategy (rewritten 2026.06.24 to fix over-stripping):
      1. Drop dotted-leader LINES (Table of Contents / Table of Authorities
         entries).  A line containing 4+ consecutive dots is, by definition,
         a TOC/TOA leader -- never substantive prose.  This removes the TOC
         that survives TOA excision, so the old "amputate everything before
         ARGUMENT" emergency fallback (which discarded the Preliminary
         Statement and its citations) is no longer needed or used.
      2. Start the body at the first real section heading (Preliminary
         Statement / Introduction / Facts / ... / Argument) that has
         substantial text after it -- a heading with little following text
         is a leftover stub, not the real section.
      3. End the body at the first back-matter marker (signature block,
         certificate of service/compliance) in the back half.
      4. Safety net: if the result is implausibly small, return the
         leader-stripped full text rather than a sliver.
    """
    if not brief or len(brief) < 500:
        return brief

    # (1) Drop TOC/TOA dotted-leader lines.
    # A TOC/TOA leader runs dots from a title to a trailing PAGE number
    # ("PRELIMINARY STATEMENT ....... 1").  A bare run of 4 dots is NOT, by
    # itself, a leader: a legal ellipsis at a sentence boundary is exactly four
    # dots ('....') and routinely starts a quote-continuation line that also
    # carries citations.  Dropping those silently deleted body citations (the
    # Perez / In re Tex. Petroleum short-cites in Brief B [158], 2026.06.30).
    # So treat a line as a leader ONLY when 4+ dots are followed by a trailing
    # page token, OR when the dot run is implausibly long (10+), which never
    # occurs in prose.
    _leader = re.compile(
        r"\.{4,}\s*(?:passim|[ivxlcdm]+|\d[\d,\s&\u2013-]*)\s*$",
        re.IGNORECASE,
    )
    txt = "\n".join(
        ln for ln in brief.splitlines()
        if not (_leader.search(ln) or re.search(r"\.{10,}", ln))
    )

    # (2) Body start: first body heading with substantial following content.
    body_start = None
    for heading in _BODY_START_HEADINGS:
        pat = re.compile(
            r"(?m)^\s*(?:[#*]+\s*)?(?:[IVXLC]+\.\s+|[A-Z]\.\s+)?"
            + re.escape(heading),
            re.IGNORECASE,
        )
        for m in pat.finditer(txt):
            if len(txt) - m.start() > 3000:  # real section, not a TOC stub
                body_start = m.start()
                break
        if body_start is not None:
            break
    if body_start is None:
        arg = re.search(r"(?m)^\s*ARGUMENT\b", txt, re.IGNORECASE)
        body_start = arg.start() if arg else 0

    # (3) Back-matter trim: first signature/COS marker in the back half.
    tail = txt[body_start:]
    for marker_pat in _BACK_MATTER_MARKERS:
        m = re.search(marker_pat, tail, re.IGNORECASE)
        if m and m.start() > len(tail) * 0.5:
            tail = tail[:m.start()]
            break
    stripped = tail.strip()

    # (4) Safety net: never return a sliver.
    if len(stripped) < len(brief) * 0.15:
        return txt.strip()

    return stripped


def build_citations(brief_text: str) -> dict:
    """Shared preprocessing + detection + proposition phase (2026.07.04).

    Single source of truth for BOTH cite_check() and cite_check_runner --
    the two parallel pipelines had drifted (audit 3.6): the runner was still
    running enricher detection after Phase 1 moved cite_check() to eyecite.
    Returns everything the resolve/verify phases need."""
    # --- Doubling gate (2026.07.29, Session B) -----------------------------
    # Refuse a page-doubled brief before any processing: a duplicate text layer
    # roughly doubles the instance count and cross-links id-chains between the
    # two copies (Brief D diagnosis, Finding 0). Point the user at the
    # converter, which drops the duplicate layer.
    _dbl_frac = detect_input_doubling(brief_text)
    if _dbl_frac > _DOUBLING_REFUSE_THRESHOLD:
        raise DoubledInputError(
            f"Input appears to be page-doubled: {_dbl_frac:.0%} of page blocks "
            f"are internally near-duplicated. The source PDF almost certainly "
            f"carries a duplicate text layer that was extracted twice, which "
            f"corrupts citation detection. Re-convert the PDF with the "
            f"pdf-to-cowork-txt skill (v2026.07.29 or later) -- it drops the "
            f"duplicate layer -- then re-run the cite-check on the clean output.")
    # --- Preprocessing -----------------------------------------------------
    # (1) Strip Markdown blockquote prefixes globally.  The pdf-to-cowork-txt
    # converter wraps TOA lines in "> " blockquotes; that artifact defeats
    # every heading regex if left in.  Strip once at the top.
    brief_text = _strip_blockquote_prefixes(brief_text)
    # (2) Parse the Table of Authorities into a keyed index BEFORE the TOA
    # is removed from the analyzable text.  The TOA is the key, not noise.
    # _parse_toa also returns the character span of the winning TOA block
    # so we can excise it before enrichment.
    toa_index, toa_span = _parse_toa(brief_text)
    # Application-sentence build (2026.08.04): harvest the instant-case actor
    # roster ONCE, from the pre-strip text (the caption lives in front matter
    # that _strip_non_argument_sections removes) plus brief-wide defined
    # aliases, with the TOA name-collision downweight.
    application_roster = cc_application.harvest_roster(brief_text, toa_index)
    # (2b) Excise the TOA block from the brief before section-stripping.
    # This guarantees the TOA is gone even if _strip_non_argument_sections
    # mis-detects its boundaries.  The TOA parser already identified the
    # authoritative span; delete it.
    if toa_span is not None:
        toa_start, toa_end = toa_span
        brief_text = brief_text[:toa_start] + brief_text[toa_end:]
    # (3) Strip non-argument sections (cover, TOC, signature, COS).
    # The TOA is already excised above; this handles remaining front/back matter.
    argument_text = _strip_non_argument_sections(brief_text)
    # (3a) Generic, case-agnostic furniture strip: remove running headers
    # (repeated across pages, page-number-insensitive), page-mark comments,
    # converter banners, and bare page numbers BEFORE chunking so they cannot
    # leak into propositions or split page-spanning sentences/quotes.
    argument_text, _furniture = ccprop.strip_furniture(argument_text)
    if _furniture:
        logging.info('cite_check: stripped %d furniture line(s)', len(_furniture))
    # (3b) Stitch wrapped lines: strip per-page furniture and rejoin citations
    # split across line/page breaks, so the enricher sees every cite contiguous.
    # Without this, ~17 of 47 Brief A instances were dropped before detection.
    argument_text = _stitch_wrapped_lines(argument_text)
    # (4) Normalize the BODY only (TOA already parsed above).  Removes the
    # converter's emphasis artifacts so the enricher sees clean case names
    # (fewer unresolved cites, fewer asterisk-fragment duplicates) and
    # proposition extraction sees clean sentences.  NOT applied document-wide
    # -- that would corrupt the TOA structure (see _normalize_body_text).
    argument_text = _normalize_body_text(argument_text)

    # --- Detection on eyecite (Phase 1, 2026.07.03) -------------------------
    # Structural citation detection replaces the Isaacus enricher on this path
    # (2026.07.03 audit Part 4).  get_citations runs on argument_text EXACTLY
    # as preprocessed above -- no further length-changing transforms (span
    # invariant, build journal Part 2).  Per-instance counting, the id. quote
    # rule, and supra folding live in cc_detect_eyecite.detect().
    det = cc_detect.detect(argument_text)
    jurisdiction = det["jurisdiction"]
    non_case_refs: List[dict] = list(det["non_case"])
    citations: List[Citation] = []
    for inst in det["instances"]:
        name = _clean_citation_name(inst["name"])
        cit = Citation(
            name=name,
            type="decision",
            jurisdiction=jurisdiction,
            span_start=inst["span_start"],
            span_end=inst["span_end"],
            pincite=inst["pincite"],
            is_short_form=inst["is_short_form"],
            occurrence_index=inst["occurrence_index"],
            occurrence_count=inst["occurrence_count"],
            cite_text=inst["cite_text"],
            reporter_cite=inst["reporter_cite"],
            pin_cite=inst["pin_cite"],
        )
        # TOA fuzzy match.  Matched on a comma-free variant of the name first:
        # _normalize_case_name splits at the first comma (built for enricher
        # names that carried reporter tails), which would amputate an
        # eyecite-truncated corporate name like "Co., LP v. Nationstar" down
        # to a single "co" token and invite a false TOA hit.
        tm = _find_toa_match(name.replace(",", ""), toa_index)
        if tm is None:
            tm = _find_toa_match(name, toa_index)
        if tm is None:
            tm = _find_toa_match_by_cite(inst["reporter_cite"], toa_index)
        cit.toa_match = tm
        # If eyecite's name is junk (a bare cite / docket string, no case
        # structure) and the TOA knows the real name, adopt it for display;
        # the resolver already prefers toa_match["name"] for searches.
        if tm and re.search(r"\d", name) and not re.search(
                r"\bv\.?\s|\bin re\b|\bmatter of\b|\bex parte\b", name, re.I):
            cit.name = _clean_citation_name(tm.get("name") or name)
        # Adverse-signal engine (kept per handoff keep-list).
        sig = _adverse_signal(argument_text, cit.span_start)
        if sig:
            cit.adverse_signal = True
            cit.adverse_signal_token = sig
        # G4 (2026.07.14): '(cleaned up)'-style license parenthetical
        # adjacent to the cite relaxes quote-fidelity grading (see
        # cc_quote_matcher.detect_license_signal).
        _lic_sig = cc_quote_matcher.detect_license_signal(
            argument_text[cit.span_end:cit.span_end + 200])
        if _lic_sig:
            cit.quote_license = _lic_sig
        # 2c groundwork (2026.07.14): quoted/citing-source linkage for the
        # Phase 3 report layer -- a cite living only inside a parent's
        # "(quoting X)" / "(citing X)" parenthetical attaches under the
        # parent instead of surfacing as a standalone adverse identity card.
        if inst.get("nested_parenthetical"):
            cit.nested_parenthetical = inst["nested_parenthetical"]
            cit.nested_parent_span = inst.get("parent_span_start")
        # eyecite spans are exact, so no _verify_and_correct_span re-anchoring
        # (that correction loop was root cause 3.1.1 of wrong propositions).
        _pk: dict = {}
        cit.proposition = _refine_proposition(
            argument_text,
            _extract_proposition(argument_text, cit.span_start, cit.span_end,
                                 is_id=(inst.get("kind") == "IdCitation"),
                                 meta=_pk),
            cit.span_start,
        )
        cit.prop_kind = _pk.get("kind", "host")
        cit.proposition_review = not cit.proposition.strip()
        citations.append(cit)

    # Chunking/enrichment no longer run on this path; meta reflects that.
    mode, chunk_engine, chunks = "eyecite", "eyecite", [argument_text]

    citations = _dedupe_by_span(citations)

    # Fix 9 (Finding 5): per-quote attribution. A quoted span belongs to the
    # citation whose span immediately FOLLOWS the quote's closing mark
    # (Bluebook placement). Any FURTHER member of that same string cite -- a
    # cite joined to the owner by a semicolon and/or a subordinate signal
    # ("see also", "accord", "cf.", "citing") -- is a support-only member and
    # must never be graded FABRICATED for the owner's quote (cards 4/8: McLane,
    # a "see also" member, was branded fabricated for Lipsky's quote). The
    # quote and its string cite frequently sit in DIFFERENT sentences (the
    # quote-sentence ends at the closing mark; the citation string is its own
    # sentence), so attribution is computed globally, not per sentence.
    _marks_all = [i for i, ch in enumerate(argument_text)
                  if ch in ('"', "“", "”")]
    _q_closes = [b for _a, b in zip(_marks_all[0::2], _marks_all[1::2])]
    _starts = sorted(((c.span_start, c) for c in citations), key=lambda x: x[0])
    _STRING_SIGNAL = re.compile(
        r"see also|accord|\bcf\b|\bciting\b|\bsee\b|but see|but cf|"
        r"e\.g\.|compare|contra", re.IGNORECASE)
    for _qc in _q_closes:
        _after = [(st, c) for st, c in _starts if st >= _qc and st - _qc <= 300]
        if len(_after) < 2:
            continue
        _after.sort(key=lambda x: x[0])
        _owner_start, _owner = _after[0]
        _prev_end = _owner.span_end
        for _st, _c in _after[1:]:
            _conn = argument_text[_prev_end:_st]
            if len(_conn) <= 160 and (";" in _conn or _STRING_SIGNAL.search(_conn)):
                _c.quote_support_only = True
                _prev_end = _c.span_end
            else:
                break

    # B4 (2026.07.29): nested-parenthetical quote attribution. A quoted span
    # inside a "(quoting X ...)" / "(citing X ...)" parenthetical belongs to
    # the nested source X, not the citing case -- grade the instance for
    # support only (never fabrication) for that quote.
    for _c in citations:
        if getattr(_c, "quote_support_only", False):
            continue
        _cprop = getattr(_c, "proposition", "") or ""
        for _cq in extract_verbatim_quotes(_cprop):
            if _quote_is_nested_attribution(_cprop, _cq):
                _c.quote_support_only = True
                try:
                    _c.quote_nested_attribution = True
                except Exception:  # noqa: BLE001
                    pass
                break

    # Deduplicate non-case refs by name
    seen_nc = set()
    unique_nc = []
    for nc in non_case_refs:
        key = nc["name"].lower().strip()
        if key not in seen_nc:
            seen_nc.add(key)
            unique_nc.append(nc)
    non_case_refs = unique_nc
    return {
        "argument_text": argument_text,
        "toa_index": toa_index,
        "citations": citations,
        "non_case_refs": non_case_refs,
        "jurisdiction": jurisdiction,
        "mode": mode,
        "chunk_engine": chunk_engine,
        "n_chunks": len(chunks),
        "application_roster": application_roster,
    }



# --------------------------------------------------------------------------
# Shared phase functions (Phase 6, 2026.07.04): ONE implementation for both
# the one-shot cite_check() path and the checkpointed Cowork runner.  The
# runner must never re-implement pipeline logic (audit 3.6).
# --------------------------------------------------------------------------
# Fix 8 (Finding 1): completeness attestation. A stored opinion copy may
# support a CONFIRMED "quote absent" verdict (the only path to CRITICAL /
# FABRICATED) ONLY if it plausibly runs to the END of the opinion. The old
# gate compared len(full_text) >= len(opinion_text) -- a length comparison
# between two copies of the SAME possibly-truncated text, which cannot detect
# truncation. A chrome-heavy or token-capped fetch therefore produced false
# CRITICAL fabrications (MacFarland 43/46, Pack Props 102/103, Raphael 58/59).
# B2 (2026.07.29): DISPOSITION markers only. The old _END_OPINION_MARKERS
# accepted a bare "/s/" signature, "chief justice", "circuit judge",
# "opinion by", so a certificate-of-service signature block in a 3.9k RECAP
# filing (Gensetix [19/20]) false-attested COMPLETE and let a fabrication
# render CRITICAL against the wrong, short document. Completeness now requires
# a genuine disposition / opinion-delivered marker near the tail; a lone
# signature no longer suffices.
_END_OPINION_MARKERS = re.compile(
    r"(?:it is (?:hereby |therefore |so )*ordered"
    r"|so ordered"
    r"|we (?:therefore |accordingly |thus )?"
    r"(?:affirm|reverse|remand|vacate|dismiss|deny|grant|render|modify|"
    r"conclude|hold)\b"
    r"|(?:judgment|order|petition|motion|conviction|appeal) (?:is |are )?"
    r"(?:affirmed|reversed|vacated|remanded|dismissed|denied|granted|"
    r"modified|rendered)"
    r"|affirmed in part"
    r"|reversed and remanded"
    r"|delivered the opinion of the court"
    r"|per curiam)",
    re.IGNORECASE,
)

# A tail dominated by a certificate of service / e-file receipt is a FILING,
# not an opinion ending -- exclude it (B2).
_CERT_OF_SERVICE_RE = re.compile(
    r"certificate of service"
    r"|i (?:hereby )?certify that"
    r"|true and correct copy"
    r"|was (?:served|e-?served|filed and served) (?:on|upon|via|by)"
    r"|electronically (?:filed|served) (?:with|via|through)",
    re.IGNORECASE,
)

# B3 (2026.07.29): a body far larger than any single appellate opinion is
# almost certainly a consolidated record or the wrong document (Berry [72]
# resolved to a 176,808-char doc, and the discovery-rule quote that should
# verify was absent -> false fabrication). Above this bound, completeness is
# NOT attested, so an absent quote degrades to review rather than CRITICAL.
_MAX_SINGLE_OPINION_CHARS = 150_000


def _opinion_is_complete(text):
    """Attest that a stored opinion copy plausibly runs to the END of a single
    opinion. Requires (a) a plausible body length, (b) a genuine disposition /
    opinion-delivered marker near the tail (NOT a bare signature -- B2), (c) a
    tail that is not a certificate-of-service / e-file receipt (B2), and (d) a
    body not wildly oversized for a single appellate opinion (B3). A trimmed
    pincite window, a truncated/chrome-heavy fetch, a docket filing, or a
    consolidated record fails this, so a "quote absent" result on such a copy
    degrades to "review" rather than a CRITICAL fabrication (Finding 1)."""
    if not text:
        return False
    if len(text) < 1200:
        return False
    if len(text) > _MAX_SINGLE_OPINION_CHARS:
        return False
    tail = text[-6000:]
    if not _END_OPINION_MARKERS.search(tail):
        return False
    if _CERT_OF_SERVICE_RE.search(tail) and not re.search(
            r"(?:affirm|revers|remand|render|vacate|per curiam"
            r"|delivered the opinion)", tail, re.IGNORECASE):
        return False
    return True


def _has_opinion_disposition(text):
    """B1 helper (2026.07.29): True if `text` reads like a court opinion that
    reached a disposition -- a genuine disposition / opinion-delivered marker
    appears and the body is opinion-length. Used by the resolver's RECAP
    acceptance guard to reject docket filings that are not the cited opinion."""
    if not text or len(text) < 1200:
        return False
    return bool(_END_OPINION_MARKERS.search(text))


_NESTED_QUOTING_SIGNAL = re.compile(r"\b(?:quoting|citing)\b", re.IGNORECASE)


def _quote_is_nested_attribution(prop, quote):
    """B4 (2026.07.29): True when `quote` occurs inside a parenthetical that
    opens with a 'quoting'/'citing' signal, so the quoted words belong to the
    nested source, not the citing case (M&M [88]: a nested (quoting Riverside)
    quote was branded fabricated against Marcus & Millichap)."""
    if not prop or not quote:
        return False
    key = quote.strip()[:40]
    idx = prop.find(key)
    if idx < 0:
        prop = re.sub(r"\s+", " ", prop)
        idx = prop.find(re.sub(r"\s+", " ", quote.strip())[:40])
        if idx < 0:
            return False
    # Scan each "(quoting ...)" / "(citing ...)" parenthetical; the quote is
    # nested-attributed if it falls within one such paren's span (the quote may
    # sit in a further-nested paren, so we test the outer paren's full extent).
    for m in re.finditer(r"\((?:[^()]*?\b(?:quoting|citing)\b)", prop, re.IGNORECASE):
        open_pos = m.start()
        depth, close_pos = 0, len(prop)
        for j in range(open_pos, len(prop)):
            if prop[j] == "(":
                depth += 1
            elif prop[j] == ")":
                depth -= 1
                if depth == 0:
                    close_pos = j
                    break
        if open_pos < idx < close_pos:
            return True
    return False


def verify_citation(cit, opinion_text, *, client=None, opinion_url="",
                    opinion_source="", nc_ok=None, search_url="",
                    search_detail="", lookup_status=None, lookup_note=None,
                    full_text=None, full_text_complete=None):
    """Verify ONE citation against its resolved opinion text.

    Encapsulates the post-resolution pipeline: the no-text and
    no-proposition short-circuits, verify(), the verbatim-quote override
    input, the identity gate, source-gated pincite logic, the Phase 4
    star-page mapping and Answer Extractor second opinion, and the
    citation-lookup status fold.  Returns a CiteCheckResult.

    full_text (E1/G2, 2026.07.14): the UNTRIMMED opinion text, when the
    runner checkpoint carries it. Quote fidelity re-checks any non-VERBATIM
    span against it, so a pincite-trim window can never produce a false
    FABRICATED (an incorrect public accusation of fabrication) and a block
    quote spanning a page break matches in full. verify() itself still runs
    on the trimmed window (M3: token cost).
    """
    try:
        import cl_resolver as _clr
    except Exception:  # noqa: BLE001
        _clr = None

    def _fold(r):
        r.lookup_status = (lookup_status if lookup_status is not None
                           else getattr(cit, "_lookup_status", None))
        r.lookup_note = (lookup_note if lookup_note is not None
                         else (getattr(cit, "_lookup_note", "") or ""))
        r.search_url = r.search_url or search_url
        r.search_detail = r.search_detail or search_detail
        return r

    if not opinion_text:
        return _fold(CiteCheckResult(
            citation=cit, opinion_resolved=False,
            notes="Opinion text could not be resolved (CourtListener opinions + RECAP + free-source fallback)."))

    _op_url = opinion_url or getattr(cit, "_recap_url", "") or ""
    _op_src = opinion_source or getattr(cit, "_recap_source", "") or ""

    if not (cit.proposition or "").strip():
        # Never verify a bare case name (audit 3.1.3 / known-bug 5.2).
        return _fold(CiteCheckResult(
            citation=cit, opinion_resolved=True,
            notes="Proposition not extracted \u2014 review required.",
            opinion_url=_op_url, opinion_source=_op_src,
            opinion_chars=len(opinion_text or "")))

    qa = helpers.verify(cit.proposition, opinion_text, client=client)
    if qa is None:
        return _fold(CiteCheckResult(
            citation=cit, opinion_resolved=True,
            notes="Verify call failed; no result."))

    vq = extract_verbatim_quote(cit.proposition or "")
    # Graded quote fidelity (Part 3, 2026.07.09, citation-verifier import).
    # The old boolean quote_in_opinion false-negatived on legitimate legal
    # alterations ([T]he openings, [bracketed substitutions], ellipses), so
    # the Connaughton override silently failed on faithful-but-altered
    # quotes; and it carried NO negative signal, so a fabricated "quotation"
    # over an accurate paraphrase rendered Verified with no warning.
    # verify_quote() normalizes those alterations and grades the match:
    # VERBATIM keeps the existing override; FABRICATED/CLOSE add an additive
    # reviewer note (taxonomy locked -- notes only).
    q_matched, quote_note = False, ""
    quote_results, any_fab = [], False
    _lic = bool(getattr(cit, "quote_license", "") or "")
    _support_only = bool(getattr(cit, "quote_support_only", False))
    # Fix 8: completeness attestation for the copy behind any CONFIRMED
    # (CRITICAL) fabrication. Prefer an attestation recorded at patch time;
    # otherwise attest from the best available copy's own end-of-opinion
    # markers -- never from a length comparison.
    if full_text_complete is not None:
        _complete = bool(full_text_complete)
    else:
        _complete = _opinion_is_complete(full_text or opinion_text)
    vqs = extract_verbatim_quotes(cit.proposition or "")
    # Phase 6 (B1 Cits 2 & 5): when NO >=25-char quoted span survived
    # extraction, try to recover a whole-sentence quotation whose opening mark
    # was dropped. Evidence-gated (verbatim match required), so a paraphrase
    # can never be turned into a FABRICATED here.
    if not vqs and opinion_text:
        _rec = recover_sentence_quotation(cit.proposition or "", opinion_text,
                                          license_signal=_lic)
        if _rec:
            vqs = [_rec]
            vq = _rec
    _long_results = []
    if vqs and opinion_text:
        # G3 (2026.07.14): every quoted span is verified; the worst result
        # governs. q_matched (the Connaughton override input) requires ALL
        # long spans VERBATIM -- a fabricated span must never ride a sibling
        # quote's override to Verified.
        _rank = {"VERBATIM": 2, "CLOSE": 1, "FABRICATED": 0}
        for _q in vqs:
            _qv = cc_quote_matcher.verify_quote(_q, opinion_text,
                                                license_signal=_lic)
            _full_checked = False
            # E1 + G2 (2026.07.14): a non-VERBATIM window result is re-run
            # against the FULL opinion text before it stands. The pincite
            # trim can exclude a block quote's tail or a quote at another
            # page; FABRICATED may stand only after the full-text recheck.
            if _qv.result.value != "VERBATIM":
                if full_text and len(full_text) > len(opinion_text or ""):
                    _qv2 = cc_quote_matcher.verify_quote(_q, full_text,
                                                         license_signal=_lic)
                    if (_rank[_qv2.result.value], _qv2.similarity) > \
                            (_rank[_qv.result.value], _qv.similarity):
                        _qv = _qv2
                # Fix 8: a FABRICATED may be CONFIRMED (-> CRITICAL) only when
                # the copy checked is attested COMPLETE, not merely as long as
                # the pincite window.
                _full_checked = _complete
            _long_results.append({
                "quote": _q,
                "result": _qv.result.value,
                "similarity": _qv.similarity,
                "alterations_only": _qv.alterations_only,
                "license_applied": _qv.license_applied,
                "full_text_checked": _full_checked,
                # Phase 7: a positive match needs no confirmation; an
                # adverse one stands only after the complete-text check.
                "confirmed": (_qv.result.value == "VERBATIM"
                              or _full_checked),
                "passage": cc_quote_matcher.clean_passage(
                    _qv.matched_passage or "")[:300],
                "window": getattr(_qv, "matched_window", "") or "",
                "diff": getattr(_qv, "diff", None),
                "clean_alterations": getattr(_qv, "clean_alterations", None),
                "short": False,
            })
        q_matched = bool(_long_results) and all(
            qr["result"] == "VERBATIM" for qr in _long_results)
    # Phase 6 (B1 Cit 17): strict short-quote checking. Balanced short spans
    # (e.g. "gap[]") are verified by normalized-exact substring only, and are
    # kept OUT of the Connaughton support override (q_matched) -- a 3-char word
    # match must never flip a low support score to Verified. A fabricated SHORT
    # quote produces a reviewer note but does NOT set quote_fabricated (the
    # CRITICAL signal stays driven by substantial quotations only).
    _short_results = []
    if opinion_text:
        _srank = {"VERBATIM": 2, "CLOSE": 1, "FABRICATED": 0}
        for _sq in extract_short_quotes(cit.proposition or ""):
            _sv = cc_quote_matcher.verify_quote(_sq, opinion_text,
                                                license_signal=_lic,
                                                strict=True)
            _s_checked = False
            # Phase 7 confirmation gate: a short quote is re-checked
            # against the COMPLETE opinion before any adverse result may
            # stand -- the pincite window can exclude the very page the
            # phrase sits on (Brief C cit 8, "innocent stakeholder").
            if _sv.result.value != "VERBATIM":
                if full_text and len(full_text) > len(opinion_text or ""):
                    _sv2 = cc_quote_matcher.verify_quote(
                        _sq, full_text, license_signal=_lic, strict=True)
                    if (_srank[_sv2.result.value], _sv2.similarity) > \
                            (_srank[_sv.result.value], _sv.similarity):
                        _sv = _sv2
                # Fix 8: confirm only against an attested-complete copy.
                _s_checked = _complete
            _short_results.append({
                "quote": _sq,
                "result": _sv.result.value,
                "similarity": _sv.similarity,
                "alterations_only": _sv.alterations_only,
                "license_applied": _sv.license_applied,
                "full_text_checked": _s_checked,
                "confirmed": (_sv.result.value == "VERBATIM"
                              or _s_checked),
                "passage": cc_quote_matcher.clean_passage(
                    _sv.matched_passage or "")[:300],
                "clean_alterations": getattr(_sv, "clean_alterations", None),
                "short": True,
            })
    quote_results = _long_results + _short_results
    # Phase 7 confirmation gate (the attorney 2026.07.15). The CRITICAL signal
    # (quote_fabricated) fires only on a CONFIRMED absence -- checked
    # against the complete opinion -- and covers EVERY quoted span, long
    # or short (a fabricated two-word quote is still a fabrication). An
    # unconfirmed FABRICATED (partial copy only) degrades to review and
    # enters the Step 6.6 must-verify loop; it never renders CRITICAL.
    _fabs_c = [qr for qr in quote_results
               if qr["result"] == "FABRICATED" and qr.get("confirmed")]
    _fabs_u = [qr for qr in quote_results
               if qr["result"] == "FABRICATED" and not qr.get("confirmed")]
    any_fab = bool(_fabs_c)
    if _support_only:
        # Fix 9 (Finding 5): a string-cite member never carries another
        # authority's quotation. Suppress fabrication; grade support only.
        any_fab = False
        q_matched = False
        for qr in quote_results:
            if qr.get("result") == "FABRICATED":
                qr["support_only"] = True
        if getattr(cit, "quote_nested_attribution", False):
            quote_note = ("Quoted language is nested inside a "
                          "\u201c(quoting/citing \u2026)\u201d parenthetical "
                          "and belongs to the cited source, not this case; "
                          "graded for support only, not quote fidelity.")
        else:
            quote_note = ("Quoted language in this sentence is placed with another "
                      "citation in the string cite; this instance is graded for "
                      "support only, not quote fidelity.")
    elif any_fab:
        _fq = _fabs_c[0]
        if _fq.get("short"):
            quote_note = (
                "A short quoted phrase (\u201c%s\u201d) is not in the "
                "opinion \u2014 confirmed absent from the complete "
                "opinion text." % _fq["quote"][:60])
        else:
            quote_note = (
                "Quoted language not located in the opinion \u2014 possible "
                "paraphrase presented as quotation.")
            if len(quote_results) > 1:
                quote_note += (" Affected quote: \u201c%s\u2026\u201d"
                               % _fq["quote"][:120])
    elif _fabs_u:
        quote_note = (
            "Quoted language (\u201c%s\u2026\u201d) was not located in the "
            "partial copy retrieved, and the complete opinion was "
            "unavailable to confirm absence \u2014 review required; not "
            "graded as fabricated." % _fabs_u[0]["quote"][:80])
    elif quote_results:
        _cl = [qr for qr in quote_results if qr["result"] == "CLOSE"]
        if _cl:
            quote_note = (
                "Quoted language matches the opinion only approximately "
                "(similarity %d%%)." % round(_cl[0]["similarity"] * 100))
            if _cl[0]["passage"]:
                quote_note += (" Closest passage: \u201c%s\u201d"
                               % _cl[0]["passage"])
        elif any(qr["alterations_only"] or qr["license_applied"]
                 for qr in quote_results):
            if any(qr["license_applied"] for qr in quote_results):
                quote_note = ("Quote verified (alterations licensed by "
                              "the brief's signal parenthetical, e.g. "
                              "\u201c(cleaned up)\u201d).")
            else:
                quote_note = "Quote verified (permitted alterations)."

    # Identity gate: prefer the resolver's verdict computed on the FULL
    # opinion (journal Part 2); recompute on the window only as fallback.
    _nc = nc_ok
    if _nc is None:
        _nc = getattr(cit, "_resolved_name_cite_ok", None)
    if _nc is None and _clr is not None:
        try:
            _nc = _clr._name_or_cite_match(cit, opinion_text)
        except Exception:  # noqa: BLE001
            _nc = None

    # Source-gated pincite rule (locked spec #10; 2026.06.30).
    pincite_given, pincite_found, pincite_note = False, None, ""
    _pin = ""
    if _clr is not None:
        try:
            _pin = _clr._pincite_from_citation(cit)
        except Exception:  # noqa: BLE001
            _pin = ""
    _nonrep = bool(_op_src)
    if _pin and _clr is not None:
        pincite_given = True
        if _clr._pincite_located(opinion_text, _pin):
            pincite_found = True
        elif _nonrep:
            pincite_found = False
            pincite_note = ("Pincite p. %s could not be checked \u2014 resolved from a non-reporter copy (PACER/free source) that does not carry Westlaw/reporter page numbers." % _pin)
        elif _clr._source_has_pagination(opinion_text):
            pincite_found = False
            pincite_note = "Pincite p. %s not found on the paginated source." % _pin
        else:
            pincite_found = False
            pincite_note = ("Pincite p. %s could not be checked \u2014 the retrieved copy has no reporter pagination." % _pin)

    r = CiteCheckResult(
        citation=cit, opinion_resolved=True,
        passage=_sentence_bound_passage(qa["passage"]), score=qa["score"],
        inextractability_score=qa["inextractability_score"],
        supports=qa["supports"], notes=_qa_note(qa),
        opinion_url=_op_url, opinion_source=_op_src,
        verbatim_quote=vq, quote_matched=q_matched, quote_note=quote_note,
        quote_results=quote_results or None, quote_fabricated=any_fab,
        opinion_chars=len(opinion_text or ""),
        name_cite_ok=_nc, pincite_given=pincite_given,
        pincite_found=pincite_found, pincite_note=pincite_note)
    # 2026.07.04 (footnote fix): tell the reviewer when the pincite targets
    # an opinion footnote -- the endnote text was pulled into the check.
    try:
        _fn_ref = _clr._pincite_footnote(cit) if _clr is not None else ""
    except Exception:  # noqa: BLE001
        _fn_ref = ""
    if _fn_ref:
        r.notes = ((r.notes + " ") if r.notes else "") + (
            "Pincite targets footnote %s of the opinion; the opinion's "
            "footnote text was included in the verification window." % _fn_ref)
    # Phase 4: star-page location + second opinion on close calls.
    r.passage_page = locate_passage_page(opinion_text, qa)
    try:
        apply_second_opinion(r, opinion_text, client=client)
    except Exception:  # noqa: BLE001
        pass
    return _fold(r)


def finalize_results(built, results):
    """TOA Coverage cross-check + the shared result dict (Phase 6).

    body_only: cited in the body, absent from the TOA (force-flagged).
    toa_only:  listed in the TOA, never cited in the body.
    """
    toa_index = built["toa_index"]
    # Application-sentence build (2026.08.04): detector + verified-sibling
    # cross-check over the FINAL propositions (agent-supplied ones included).
    # Idempotent; re-run at render so Step 6.6 overrides update siblings.
    cc_application.attach(results, built.get("application_roster"))
    body_only_cases, toa_only_cases = [], []
    if toa_index:
        matched_keys = set()
        for r in results:
            if r.citation.toa_match:
                matched_keys.add(r.citation.toa_match["key"])
                continue
            r.body_only = True
            body_only_cases.append({
                "name": r.citation.name,
                "proposition": (r.citation.proposition or "")[:200],
            })
        for key, entry in toa_index.items():
            if key not in matched_keys:
                toa_only_cases.append(entry)
    return {
        "jurisdiction": built["jurisdiction"],
        "citations": results,
        "non_case_references": built["non_case_refs"],
        "reranked_candidates": None,
        "chunking": {"mode": built["mode"], "engine": built["chunk_engine"],
                     "chunks": built["n_chunks"]},
        "toa_index": toa_index,
        "toa_only_cases": toa_only_cases,
        "body_only_cases": body_only_cases,
        "application_roster": built.get("application_roster"),
    }


def cite_check(
    brief_text: str,
    resolve_opinion_text: ResolveOpinion,
    *,
    fallback_resolve: Optional[FallbackResolve] = None,
    research_query: Optional[str] = None,
    candidate_opinions: Optional[Sequence[dict]] = None,
    chunk_size: int = chunker_mod.DEFAULT_CHUNK_SIZE,
    ai_threshold_chars: int = chunker_mod.DEFAULT_AI_THRESHOLD_CHARS,
) -> dict:
    """Run the full cite-check pipeline on a brief.

    Args:
        brief_text: Raw text of the brief being checked.
        resolve_opinion_text: Callback that accepts a Citation and returns
            opinion text (or None if the citation cannot be resolved).
            In production, this is wired into caselaw-retriever.
        research_query: Optional.  When provided alongside
            ``candidate_opinions``, triggers reranking (C4).
        candidate_opinions: Optional list of opinion dicts (each with at
            least ``{"excerpt": str, ...}``) to be reranked by Isaacus.
        chunk_size: Chunk size for AI chunking, in tokens.
        ai_threshold_chars: Character length above which the brief is
            AI-chunked before enrichment.

    Returns:
        {
            "jurisdiction": str | None,  # auto-detected from brief
            "citations": list[CiteCheckResult],
            "non_case_references": list[dict],  # non-case refs (rules, statutes, etc.)
            "reranked_candidates": list[dict] | None,
            "chunking": {"mode": "skip"|"fast"|"ai", "chunks": int},
            "toa_index": dict,            # parsed TOA, keyed by normalized name
            "toa_only_cases": list[dict], # listed in TOA but not found in body
            "body_only_cases": list[dict],# found in body but missing from TOA
        }
    """
    if not brief_text or not brief_text.strip():
        return {
            "jurisdiction": None,
            "citations": [],
            "non_case_references": [],
            "reranked_candidates": None,
            "chunking": {"mode": "skip", "chunks": 0},
            "toa_index": {},
            "toa_only_cases": [],
            "body_only_cases": [],
        }

    # Shared phase (2026.07.04): preprocessing + eyecite detection +
    # proposition extraction live in build_citations(); the runner calls the
    # SAME function (single source of truth, audit 3.6).
    built = build_citations(brief_text)
    toa_index = built["toa_index"]
    citations = built["citations"]
    non_case_refs = built["non_case_refs"]
    jurisdiction = built["jurisdiction"]
    mode, chunk_engine, n_chunks = built["mode"], built["chunk_engine"], built["n_chunks"]

    # --- Resolution + verification (shared implementations) ---------------
    # Phase 6 (2026.07.04): the one-shot path is routed through the SAME
    # batched citation-lookup primary resolver the runner uses.  When the
    # resolve callback is a CLResolver bound method (the production wiring),
    # lookup_chunks/batch_lookup_step run over the brief's unique reporter
    # cites BEFORE the per-citation loop, so per-cite statuses (200/300/400/
    # 404) and typo notes land on the results exactly as in the runner.
    # Pacing (60 valid cites/min) is inside batch_lookup_step; a large brief
    # belongs on the checkpointed runner regardless (SKILL shell-cap rule).
    _resolver = getattr(resolve_opinion_text, "__self__", None)
    if _resolver is not None and hasattr(_resolver, "batch_lookup_step"):
        try:
            _lk = {}
            while not _lk.get("complete"):
                _lk = _resolver.batch_lookup_step(citations, _lk, deadline=None)
        except Exception:  # noqa: BLE001
            pass

    results: List[CiteCheckResult] = []
    for cit in citations:
        opinion_text = None
        try:
            opinion_text = resolve_opinion_text(cit)
        except Exception:  # noqa: BLE001
            opinion_text = None

        # CourtListener gap -> free-source fallback (Mode A).
        fb_url, fb_source = "", ""
        if not opinion_text and fallback_resolve is not None:
            try:
                fb = fallback_resolve(cit)
            except Exception:  # noqa: BLE001
                fb = None
            if fb and fb.get("text"):
                opinion_text = fb["text"]
                fb_url = fb.get("opinion_url", "")
                fb_source = fb.get("source", "")

        _log = None
        if _resolver is not None and hasattr(_resolver, "get_log"):
            try:
                _log = _resolver.get_log(cit.name)
            except Exception:  # noqa: BLE001
                _log = None
        results.append(verify_citation(
            cit, opinion_text,
            opinion_url=fb_url, opinion_source=fb_source,
            search_url=(_log.search_url if _log else "") or "",
            search_detail=_log.build_detail() if _log else ""))

    _fin = finalize_results(built, results)
    toa_only_cases = _fin["toa_only_cases"]
    body_only_cases = _fin["body_only_cases"]

    # --- C4: rerank candidate opinions, if asked -------------------------
    reranked = None
    if research_query and candidate_opinions:
        texts = [c.get("excerpt", "") for c in candidate_opinions]
        ranked = helpers.rerank(research_query, texts)
        reranked = []
        for r in ranked:
            cand = dict(candidate_opinions[r["index"]])
            cand["isaacus_score"] = r["score"]
            reranked.append(cand)

    return {
        "jurisdiction": jurisdiction,
        "citations": results,
        "non_case_references": non_case_refs,
        "reranked_candidates": reranked,
        "chunking": {"mode": mode, "engine": chunk_engine, "chunks": n_chunks},
        "toa_index": toa_index,
        "toa_only_cases": toa_only_cases,
        "body_only_cases": body_only_cases,
        "application_roster": built.get("application_roster"),
    }


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _page_from_frag(frag: str) -> str:
    """Resolve the pincited PAGE (not the volume) from a pinpoint's literal text.

    Reporter-agnostic.  "556 U.S. at 679" -> 679 ; "at 680" -> 680 ;
    "550 U.S. 544, 570" -> 570 ; "p. 215" -> 215.  Prefers the page after
    "at"/comma so a reporter volume ("556") is never mistaken for the page.
    """
    if not frag:
        return ""
    # Strip court-date parentheticals ("(S.D. Tex. 2015)", "(N.D. Tex. Mar. 11,
    # 2025)") so a YEAR is never read as a page.  Reporter-agnostic.
    work = re.sub(r"\([^)]*\b(?:19|20)\d{2}\b[^)]*\)", " ", frag)
    # Explicit pinpoint markers ONLY.  A volume, opening page, docket number, or
    # WL/LEXIS year never becomes a pincite by itself.
    m = re.search(r"\bat\s+\*?(\d{1,5})\b", work)        # "at 679", "at *3"
    if m:
        return m.group(1)
    m = re.search(r"\b\d+\s*,\s*(\d{1,5})\b", work)     # "283, 290" = opening, pincite
    if m:
        return m.group(1)
    m = re.search(r"\bp{1,2}\.?\s*(\d{1,5})\b", work)    # "p. 215"
    if m:
        return m.group(1)
    # No explicit pinpoint -> NO pincite.  (Was nums[-1], which grabbed trailing
    # years and docket numbers off a full cite -- the 2026.06.29 year-as-page bug.)
    return ""


def _expand_to_citations(
    ext: dict,
    *,
    chunk_offset: int,
    chunk_text: str = "",
    toa_index: Optional[dict] = None,
) -> "List[Citation]":
    """Expand one enricher external_document (an AUTHORITY) into per-INSTANCE
    Citations.

    Locked spec, items 5/6/9: every in-text citation INSTANCE is its own
    checkable entry -- a case pincited at pp. 4, 5, 6 is THREE checks, not one.
    The instances are sourced from the enricher's own ``pinpoints``/``mentions``
    for this authority, NOT from a name re-search: the enricher already groups
    short forms and ``id.`` under the correct authority, so no jurisdiction- or
    case-specific string matching is introduced (prime directive).

    id. rule (item 6): the literal ``id.`` short-cite carries no case name and
    no new proposition, so a bare ``id.`` instance with no direct quote folds
    into the preceding entry rather than spawning a redundant authority check.
    Named short-cites ("Yates at p. 5") DO expand (item 9).
    """
    base = _to_citation(
        ext, chunk_offset=chunk_offset, chunk_text=chunk_text, toa_index=toa_index,
    )
    if base is None:
        return []

    def _spans(key):
        out = []
        for d in (ext.get(key) or []):
            if isinstance(d, dict) and d.get("start") is not None and d.get("end") is not None:
                try:
                    out.append((int(d["start"]), int(d["end"])))
                except (TypeError, ValueError):
                    continue
        return out

    # Prefer pincited pages (page-level checks); else distinct mentions; a lone
    # anchor needs no expansion -- keep the single base entry (existing behavior).
    anchors = _spans("pinpoints") or _spans("mentions")
    if len(anchors) <= 1:
        return [base]
    anchors.sort()
    # Governing mention starts -- the adverse signal introduces the CITE,
    # not the pinpoint page (which sits a few words later), so each instance
    # tests for a signal at its nearest preceding mention (item 5).
    _mention_starts = sorted(st for st, _e in _spans("mentions"))

    nm = ext.get("name")
    name_start = nm.get("start") if isinstance(nm, dict) else None

    expanded = []  # type: List[Citation]
    for j, (s, e) in enumerate(anchors):
        frag = chunk_text[s:e] if chunk_text else ""
        left = chunk_text[max(0, s - 24):s] if chunk_text else ""
        is_id = bool(re.match(r"\s*id\b\.?", frag, re.I)) or bool(
            re.search(r"\bid\.\s*$", left, re.I)
        )
        window = chunk_text[s:min(len(chunk_text), e + 200)] if chunk_text else ""
        has_quote = bool(_VERBATIM_QUOTE_RE.search(window))
        # item 6: a bare id. with no direct quote folds into the preceding entry.
        if is_id and not has_quote and expanded:
            continue
        c = copy.copy(base)
        c.pinpoints = [{"start": s + chunk_offset, "end": e + chunk_offset}]
        c.pincite = _page_from_frag(frag)
        c.span_start = s + chunk_offset
        c.span_end = e + chunk_offset
        c.occurrence_index = j
        c.occurrence_count = len(anchors)
        c.is_short_form = not (
            name_start is not None and s is not None and abs(s - name_start) <= 60
        )
        # Adverse-signal per instance (item 5): test at the governing
        # mention start (nearest mention at/before this anchor), since the
        # contrary signal introduces the cite, not the pinpoint page.
        _prev = [ms for ms in _mention_starts if ms <= s]
        _gov = _prev[-1] if _prev else s
        _isig = _adverse_signal(chunk_text, _gov)
        c.adverse_signal = bool(_isig)
        c.adverse_signal_token = _isig
        expanded.append(c)
    return expanded or [base]


def _to_citation(
    ext: dict,
    *,
    chunk_offset: int,
    chunk_text: str = "",
    toa_index: Optional[dict] = None,
) -> Optional[Citation]:
    """Convert an enricher external_document dict to a Citation.

    Applies ``chunk_offset`` so spans map back to the original brief text.
    Returns None if the entry has no usable name.

    The enricher may return ``name`` as either a string or a span dict
    (``{"start": int, "end": int}``).  When it is a span dict, the name
    is resolved by slicing ``chunk_text``.

    When ``toa_index`` is provided, the body name is fuzzy-matched (token
    overlap >= 0.7) against TOA entries.  A match populates ``toa_match``
    on the returned Citation so the report can show the full TOA cite and
    "Cited at pp." page references.
    """
    name_raw = ext.get("name") or ext.get("title") or ""
    if isinstance(name_raw, dict):
        n_start = name_raw.get("start")
        n_end = name_raw.get("end")
        if (
            n_start is not None
            and n_end is not None
            and 0 <= n_start < n_end <= len(chunk_text)
        ):
            name = chunk_text[n_start:n_end]
        else:
            name = ""
    elif isinstance(name_raw, str):
        name = name_raw
    else:
        name = ""
    # Defensive name hygiene: strip any emphasis markers / line breaks the
    # enricher carried through from the converter, so the resolver searches a
    # clean name and dedup collapses asterisk-fragment variants of one case.
    name = _clean_citation_name(name)
    if not name:
        return None

    # The enricher may provide the span via "span", or via the first
    # entry in "mentions" (the Isaacus enricher uses "mentions").
    span = ext.get("span") or {}
    if not isinstance(span, dict) or ("start" not in span):
        mentions = ext.get("mentions")
        if mentions and isinstance(mentions, list) and mentions:
            span = mentions[0] if isinstance(mentions[0], dict) else {}
    start = span.get("start") if isinstance(span, dict) else None
    end = span.get("end") if isinstance(span, dict) else None
    if start is not None:
        start = start + chunk_offset
    if end is not None:
        end = end + chunk_offset

    cit = Citation(
        name=name,
        type=ext.get("type"),
        jurisdiction=ext.get("jurisdiction"),
        span_start=start,
        span_end=end,
        pinpoints=list(ext.get("pinpoints") or []),
        raw=ext,
    )

    # Adverse-signal (item 5): inspect the text immediately before the
    # citation token (in-chunk coords, pre-offset).
    _raw_start = span.get("start") if isinstance(span, dict) else None
    _sig = _adverse_signal(chunk_text, _raw_start)
    if _sig:
        cit.adverse_signal = True
        cit.adverse_signal_token = _sig

    # Resolve a pincite PAGE from the first pinpoint span.  The enricher gives
    # pinpoints as {start,end} spans, which _pincite_from_citation cannot read
    # directly; resolving here means single-cited authorities also get page
    # targeting (item 3 / locked spec #10).  _expand_to_citations overrides this
    # per-instance when one authority is pincited at several pages.
    _pps = ext.get("pinpoints") or []
    if _pps and isinstance(_pps[0], dict) and chunk_text:
        _ps, _pe = _pps[0].get("start"), _pps[0].get("end")
        if _ps is not None and _pe is not None and 0 <= _ps < _pe <= len(chunk_text):
            cit.pincite = _page_from_frag(chunk_text[_ps:_pe])

    # TOA enrichment: fuzzy-match the body name against the TOA index.
    # If the body uses a short-cite ("Beal") and the TOA has the full form
    # ("Beal Sav. Bank v. Sommer, 8 N.Y.3d 318 (2007)"), token overlap >=
    # 0.7 will hit, and we attach the TOA entry without overwriting the
    # body name (the resolver still needs the body form to search CL).
    if toa_index:
        match = _find_toa_match(name, toa_index)
        if match is not None:
            cit.toa_match = match

    return cit


_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

# Superscript digit mapping (Unicode).  Used as one detection strategy for
# footnote markers in body text.  Plain-digit markers are also supported.
_SUPER_MAP = {
    "0": "\u2070", "1": "\u00B9", "2": "\u00B2", "3": "\u00B3",
    "4": "\u2074", "5": "\u2075", "6": "\u2076", "7": "\u2077",
    "8": "\u2078", "9": "\u2079",
}

# Pattern to detect the start of a footnote entry: newline, optional
# whitespace, 1-3 digit number, then a space.
_FOOTNOTE_ENTRY_RE = re.compile(r"\n\s*(\d{1,3})\s")


def _find_footnote_number(brief: str, start: int) -> Optional[int]:
    """If ``start`` falls inside a footnote entry, return its number.

    Looks backward up to 80 chars (and forward up to 10 chars, since
    ``start`` may land inside the footnote number itself) for a
    ``\\n {digits} `` pattern marking the beginning of a footnote entry.
    Returns the footnote number, or None if the span is not in a
    footnote block.
    """
    search_start = max(0, start - 80)
    # Extend the window forward a bit — the enricher span may start
    # inside the footnote number (e.g., at the "9" of "19").
    search_end = min(len(brief), start + 15)
    window = brief[search_start:search_end]
    # Find the last footnote-entry marker in the window.
    best = None
    for m in _FOOTNOTE_ENTRY_RE.finditer(window):
        best = m
    if best is None:
        return None
    # Verify the match is close to ``start`` (within the same line).
    marker_abs_end = search_start + best.end()
    # If there is a double-newline between marker and span, the span is
    # in a different paragraph — not this footnote entry.
    between_start = min(marker_abs_end, start)
    between_end = max(marker_abs_end, start)
    between = brief[between_start:between_end]
    if "\n\n" in between:
        return None
    return int(best.group(1))


def _find_body_marker(brief: str, fn_num: int, ceiling: int) -> Optional[int]:
    """Find the body-text position where footnote ``fn_num`` is referenced.

    Searches ``brief[:ceiling]`` for the footnote marker. Supports:
      1. Digits preceded by punctuation (period, quote, paren, etc.) and
         followed by whitespace or newline — the most common pattern in
         litigation briefs (e.g., ``performance.23\\n``, ``contract"21``).
      2. Unicode superscript digits (e.g., ``performance²³``).
      3. Bracketed Arabic (e.g., ``performance [23]``).

    Returns the character position of the first digit of the marker, or
    None if not found.
    """
    body = brief[:ceiling]
    num_str = str(fn_num)

    # Strategy 1: digits preceded by punctuation, not preceded by a digit,
    # followed by whitespace/newline/letter (body text continues).
    # Covers: ".23\n", ".20 To", '"21 and', "'"40 On", etc.
    pat1 = re.compile(
        r'(?<!\d)' + re.escape(num_str) + r'(?=[\s\n]|$)'
    )
    candidates = []
    for m in pat1.finditer(body):
        pos = m.start()
        # The character before the number must be punctuation or a quote
        # (not a letter, digit, or space — those would be page numbers,
        # dates, or paragraph numbers).
        if pos > 0:
            prev = body[pos - 1]
            if prev in '.!?"\'“”’)]:;':
                candidates.append(pos)
    if candidates:
        return candidates[-1]  # rightmost match

    # Strategy 2: Unicode superscript digits.
    sup_str = "".join(_SUPER_MAP[d] for d in num_str)
    idx = body.rfind(sup_str)
    if idx >= 0:
        return idx

    # Strategy 3: bracketed Arabic ``[N]``.
    bracketed = f"[{num_str}]"
    idx = body.rfind(bracketed)
    if idx >= 0:
        return idx + 1  # skip the bracket, point at the digit


# ---------------------------------------------------------------------------
# I3 (2026.07.29, Session E): statute-quote verification (Texas)
#
# A case card whose proposition BOTH quotes text AND cites a Texas code
# section gets a STATUTE CHECK note: the quoted passage is compared against
# the CURRENT statute text (statutes.capitol.texas.gov, Justia code mirror as
# fallback), and when the statute was amended AFTER the cited case's decision
# year the note flags that the case may quote the pre-amendment text (Lipsky
# 2015 quotes pre-2019 TCPA SS 27.003).  Notes only -- verdicts never change.
# The fetch is agent-driven (runner verbs `statutes` / `statute_check`), same
# contract as the gap loop.
# ---------------------------------------------------------------------------
_TX_STATUTE_REF_RE = re.compile(
    r"Tex(?:as|\.)?\s+"
    r"(Civ\.?\s*Prac\.?\s*&\s*Rem\.?|Bus\.?\s*&\s*Com(?:m)?\.?|"
    r"Gov(?:'t|t)?\.?|Prop\.?|Fam\.?|Lab\.?|Occ\.?|Penal|Tax|Ins\.?|"
    r"Elec\.?|Est(?:ates)?\.?|Health\s*&\s*Safety|Loc\.?\s*Gov(?:'t|t)?\.?)"
    r"\s*Code(?:\s*Ann\.?)?\s*(?:\u00a7{1,2}|Sec(?:tion|s)?\.?)\s*"
    r"(\d+[A-Za-z]?\.\d+)",
    re.IGNORECASE)

_TX_CODE_LETTERS = {
    "civ": "CP", "bus": "BC", "gov": "GV", "prop": "PR", "fam": "FA",
    "lab": "LA", "occ": "OC", "pen": "PE", "tax": "TX", "ins": "IN",
    "ele": "EL", "est": "ES", "hea": "HS", "loc": "LG",
}


def _tx_code_letters(code_txt):
    """statutes.capitol.texas.gov code letters from a cited code name."""
    key = re.sub(r"[^a-z]", "", (code_txt or "").lower())[:3]
    return _TX_CODE_LETTERS.get(key, "")


def statute_url_candidates(code_letters, section):
    """Candidate URLs for the CURRENT text of a Texas code section.  The
    capitol chapter page is authoritative but currently renders as a site
    shell via web_fetch (checked 2026.07.29) -- the Justia Texas-codes mirror
    is the working fallback; both are listed for the agent."""
    if not code_letters or "." not in (section or ""):
        return []
    chapter = section.split(".")[0]
    return [
        ("statutes.capitol",
         "https://statutes.capitol.texas.gov/Docs/%s/htm/%s.%s.htm"
         % (code_letters, code_letters, chapter)),
        ("justia_codes",
         "https://law.justia.com/codes/texas/"),
    ]


def statute_quote_targets(cits, context_text=None, window=600):
    """I3 targets: [{index, name, refs: [{code, section, candidates}],
    quotes}] for every citation whose proposition both quotes text and cites
    a Texas code section.  Proposition glue often drops the section number
    (the Brief D Lipsky instances render as a bare "TEX. CIV. PRAC. & REM.
    CODE" heading), so when `context_text` (the brief's argument text) is
    supplied, the +/-`window`-char span around the citation is scanned for
    the full code-plus-section reference as well."""
    out = []
    for i, c in enumerate(cits):
        prop = getattr(c, "proposition", "") or ""
        ctx = " ".join(filter(None, [
            prop, getattr(c, "cite_text", "") or ""]))
        s = getattr(c, "span_start", None)
        if context_text and s is not None:
            ctx += " " + context_text[max(0, s - window):s + window]
        refs = _TX_STATUTE_REF_RE.findall(ctx)
        if not refs:
            continue
        quotes = _VERBATIM_QUOTE_RE.findall(prop)
        if not quotes:
            continue
        seen, entries = set(), []
        for code_txt, section in refs:
            letters = _tx_code_letters(code_txt)
            key = (letters, section)
            if not letters or key in seen:
                continue
            seen.add(key)
            entries.append({
                "code": letters, "section": section,
                "candidates": [{"source": s, "url": u}
                               for s, u in statute_url_candidates(letters, section)]})
        if entries:
            out.append({"index": i, "name": getattr(c, "name", ""),
                        "refs": entries, "quotes": quotes})
    return out


def _statute_norm(t):
    """Normalize for statute-quote containment: unify quote marks, unwrap
    bracket edits ([h]ad -> had), drop ellipses, collapse whitespace."""
    t = (t or "").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"\[(\w+)\]", r"\1", t)
    t = re.sub(r"\.\s?\.\s?\.|\u2026", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def statute_check_note(citation, statute_text, url=""):
    """The STATUTE CHECK note for a citation given fetched statute text, or
    '' when the proposition has no quoted text.  Reports how many quoted
    passages appear in the CURRENT statute text and, when the section was
    amended after the cited case's decision year, flags the possible
    pre-amendment quotation.  Never changes a verdict."""
    quotes = _VERBATIM_QUOTE_RE.findall(getattr(citation, "proposition", "") or "")
    if not quotes:
        return ""
    body = _statute_norm(statute_text)
    checked = []
    for q in quotes:
        qn = _statute_norm(q)
        frags = [f.strip() for f in re.split(r"\.\s?\.\s?\.|\u2026", q)
                 if len(_statute_norm(f)) >= 15]
        if frags:
            ok = all(_statute_norm(f) in body for f in frags)
        else:
            ok = qn in body
        checked.append(ok)
    matched, total = sum(checked), len(checked)
    src = url or "statutes.capitol.texas.gov"
    if matched == total:
        note = ("STATUTE CHECK: the quoted passage(s) on this card track the "
                "CURRENT statute text (%s)." % src)
    else:
        note = ("STATUTE CHECK: %d of %d quoted passage(s) on this card do "
                "not appear in the CURRENT statute text (%s) -- expected when "
                "the quote is the cited case's own language rather than "
                "statutory text." % (total - matched, total, src))
    years = sorted({int(y) for y in re.findall(
        r"Acts\s+((?:19|20)\d{2})", statute_text or "")})
    cy = None
    yrs = re.findall(r"\b((?:19|20)\d{2})\b",
                     getattr(citation, "cite_text", "") or "")
    if yrs:
        cy = int(yrs[-1])
    later = [y for y in years if cy and y > cy]
    if later:
        note += (" Section amended by Acts %s AFTER the cited case (%d) -- if "
                 "the quote is offered as statutory text, verify it against "
                 "the historical version." % (", ".join(str(y) for y in later), cy))
    return note
