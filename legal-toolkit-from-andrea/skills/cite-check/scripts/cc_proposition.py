#!/usr/bin/env python3
"""Consolidated, reporter-agnostic proposition extractor for the cite-check
pipeline.  Replaces cc_proposition_v3 (general but reporter-list-based) and
cc_proposition_v4 (Gold-Set-A/NY-hardcoded).  NO case names or reporter names are
hardcoded; citations are recognized by STRUCTURE (volume-reporter-page,
year-WL/LEXIS, court-year parentheticals, Bluebook signals, record cites).

Public API:
    strip_furniture(raw_md)            -> (clean_text, dropped_lines)
    mask_citations(text)               -> masked copy (cites blanked, len preserved)
    find_citations(text)               -> [(start, end, cite_text)]  structural locator
    find_footnotes(text)               -> [{"spans":[(s,e)], "lead":str}]
    extract(text, pos, name="", foots=None, signal="", masked=None) -> dict
        dict: {proposition, kind, source, needs_attention, reason}
"""
from __future__ import annotations
import re
from typing import List, Optional, Tuple, Dict

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'",
          "–": "-", "—": "-", "…": " ", " ": " "}

def _fold(s: str) -> str:
    for k, v in _SMART.items():
        s = s.replace(k, v)
    return s

def _strip_md(s: str) -> str:
    return re.sub(r"\*{1,3}", "", s)

# Generic abbreviations (NOT reporters-by-name; only so the sentence splitter
# doesn't trip on ordinary legal prose).  Reporter detection is structural.
LEGAL_ABBREVS = {
    "inc","corp","co","ltd","llc","llp","lp","n.a","p.a","p.c","s.a","gmbh","ag",
    "mr","mrs","ms","dr","hon","prof","jr","sr","st","messrs",
    "u.s","s.w","s.e","n.w","n.e","so","p","a","f","fed","supp","s.ct","l.ed",
    "wl","lexis","b.r","cal","ill","tex","n.y","d.c","mass","fla","ga","la",
    "mich","ohio","pa","wis","va","md","mo","minn","wash","colo","ariz","ind",
    "app","cir","ct","civ","crim","ch","dist","div","bankr","e.d","w.d","c.d","m.d","n.d","s.d",
    "v","vs","e.g","i.e","etc","id","supra","infra","cf","accord","see","but",
    "no","nos","ed","vol","art","sec","para","pp","tit","mem","op","br","resp",
    "reply","pls","defs","mot","rev","rel","j","ann","rem","prac","dkt","ex","ph",
    "gen","ins","nat","natl","indus","mfg","bros","ry","rr","tel","elec","servs",
    "serv","sys","grp","hldgs","mgmt","assn","intl","comm","univ","inst","am","fin","cos","tech",
}

# --------------------------------------------------------------------------
# Structural citation patterns (reporter-agnostic)
# --------------------------------------------------------------------------
# Reporter cite: <volume> <reporter token(s)> <page>[, pincite].  A reporter
# token starts uppercase and may carry internal periods/letters and a series
# suffix glued on (F.3d, S.W.2d, N.Y.3d, B.R., U.S., F. Supp. 3d).
_VOL = r"\d{1,4}"
_RPT_TOK = r"(?:[A-Z][A-Za-z.]*(?:\d+(?:st|nd|rd|th|d)?)?\.?|\d+(?:st|nd|rd|th|d))"
_PAGE = r"\d{1,4}(?:,\s*\*?\d{1,4}(?:[-–]\d{1,4})?)?"
_ATPIN = r"at\s+\*?\d{1,4}(?:[-–]\d{1,4})?"  # short-form pincite anchor (e.g. '2d at 401–02')
_REPORTER = rf"\b{_VOL}\s+(?:{_RPT_TOK}\s+){{1,4}}(?:{_PAGE}|{_ATPIN})"
# Unpublished / database cites.
_DBCITE = r"\b(?:19|20)\d{2}\s+(?:WL|U\.S\.\s+(?:App\.\s+)?LEXIS|Fed\.\s*App'?x\.?|BL)\s+\d+(?:,\s*at\s*\*?\d+)?"
# NY slip ops still occur in NY briefs; keep as one more structural form.
_SLIPOP = r"\d{4}\s+N\.Y\.\s*Slip\s*Op\.?\s*\d+\(U\)\s*,?\s*\*?[\d\-–]*"
# Docket-number cite (No. 3:15-CV-4108-D).
_DOCKETNO = r"\bNo\.?\s*\d{1,2}:\d{2}-[A-Za-z]{1,3}-\d{2,6}(?:-[A-Z])?"
# Bluebook signals.
_SIGNAL = (r"(?:[Ss]ee also|[Ss]ee, e\.g\.|[Ss]ee generally|[Ss]ee|[Cc]f\.|[Bb]ut see|"
           r"[Bb]ut cf\.|[Aa]ccord|[Cc]ompare|[Cc]ontra|[Ee]\.g\.|[Ii]\.e\.)")
# Court-year parenthetical: ( ... 1977 ) / (Tex. App.-Houston [1st Dist.] 1997, pet. denied)
_PAREN_YEAR = r"\((?:[^()]*\b(?:19|20)\d{2}\b[^()]*)\)"
# Explanatory / quoting parenthetical (keyword-driven, no case content needed).
_PAREN_EXPL = (r"\((?:internal|quoting|citing|holding|applying|affirming|reversing|"
               r"emphasis|collecting|dismissing|noting|finding|rejecting|per curiam|"
               r"explaining|concluding|recognizing|alteration|quotation|omitted)[^()]*\)")
# Record cites: paragraph / section / pincite / docket / exhibit / id.
_RECORD = (r"(?:¶+\s*[\d,\s.&–-]+"
           r"|§+\s*[\d.]+(?:\([a-z0-9]+\))*(?:\([a-z0-9]+\))*"
           r"|\bat\s+\*?\d+(?:\s*[,–-]\s*\*?\d+)*"
           r"|\bDkt\.?\s*\d+[^.;)\n]*"
           r"|\bECF\s+No\.?[^.;)\n]*"
           r"|\bEx\.\s*[A-Z0-9]+)")
# Subsequent history we must NOT treat as a separate supporting cite.
_HISTORY = r",?\s*(?:overrul(?:ed|ing)|abrogat(?:ed|ing)|aff'?d|rev'?d|cert\.?\s+denied|vacated|superseded)\b[^.;]*"
# Statutory codes (Code/Stat./U.S.C./C.F.R./Const./R. Civ. P., section optional).
_STATUTE = (r"(?:(?:[A-Z][A-Za-z.]+|&)\.?\s+){1,7}(?:Code|Stat|U\.S\.C|C\.F\.R|Const|R\. Civ\. P)\.?(?:\s+Ann)?\.?(?:\s*§+\s*[\d.]+(?:\([a-z0-9]+\))*)?"
        # "Rule(s) N..." -- REQUIRE a rule number so a bare "Rules"/"Rule" in prose is not eaten.
        r"|(?:(?:[A-Z][A-Za-z.]+|&)\.?\s+){0,7}Rules?\.?\s+\d[\d.]*(?:\([a-z0-9]+\))*"
        r"|§+\s*[\d.]+(?:\([a-z0-9]+\))*")

# A case-name TOKEN: a capitalized word (abbreviations, possessives,
# ampersands), a numeric-leading party ("805 Third Ave."), or one of the
# lowercase connectors that legitimately appear inside case names.
# 2026.07.04 (footnote fix session): the name arm used to be
# "[A-Z][^*\n]+?" -- ANY capital then ANY text (lazy, unbounded) -- so a
# whole prose sentence ending in a short-form cite masked as one "case
# name" ("On a motion to dismiss, a court may ... records. Alliance
# Network, 43 Misc. 3d at 852" became a single 236-char citation mask).
# That broke sentence segmentation AND _is_citation_sentence for every
# sentence in front of a short cite.  Token-based matching stops at the
# first ordinary lowercase word, so prose can never be swallowed.
_NAME_TOKEN = (r"(?:[A-Z][\w.'\u2019()&-]*|\d+[\w.-]*"
               r"|v\.?|vs\.?|of|the|in|re|ex|rel\.?|parte|and|for|&)")


def _full_cite_re() -> str:
    body = "|".join([_SLIPOP, _DBCITE, _REPORTER])
    return (rf"(?:\b{_SIGNAL}\b[\s,]*)?"
            rf"(?:\*?(?:{_NAME_TOKEN}[,:]?\s+){{1,14}})?"   # optional case name (token-bounded)
            rf"(?:{body})"
            rf"(?:\s*{_PAREN_YEAR})?"
            rf"(?:{_HISTORY})?")

_FULL_CITE = _full_cite_re()

# --------------------------------------------------------------------------
# Furniture stripping (conservative, repetition-based, logged)
# --------------------------------------------------------------------------
_HTML_COMMENT = re.compile(r"<!--.*?-->")
_BANNER = re.compile(r"^={5,}\s*$")
_CONVERTER_KV = re.compile(r"^\s*(?:FILE|SOURCE|EXTRACTION METHOD|OUTPUT FORMAT|"
                           r"TOTAL PAGES|CONVERTED|CONTENT GAPS):", re.I)
_PAGEMARK = re.compile(r"^\s*<!--\s*Page\s+\d+\s+of\s+\d+\s*-->\s*$", re.I)
_BARE_NUM = re.compile(r"^[\s—-]*\d{1,4}[\s—-]*$")
_ROMAN = re.compile(r"^[ivxlcdm]{1,7}$", re.I)

def _norm_line_for_repeat(ln: str) -> str:
    s = re.sub(r"\s+", " ", _strip_md(_fold(ln)).strip().lower())
    return re.sub(r"\d+", "#", s)   # ignore page numbers so running headers collapse

def strip_furniture(raw: str) -> Tuple[str, List[str]]:
    """Remove converter banner, page-mark comments, bare page numbers, and any
    line that REPEATS across the document (running headers/footers) — by
    repetition, not by content, so it is brief-agnostic.  Returns
    (clean_text, dropped) where dropped is the list of removed lines for audit."""
    raw = _HTML_COMMENT.sub("", raw)
    lines = raw.split("\n")
    # Count normalized non-trivial lines to find repeats (running headers).
    from collections import Counter
    norm = [_norm_line_for_repeat(l) for l in lines]
    counts = Counter(n for n in norm if len(n) >= 12)
    repeated = {n for n, c in counts.items() if c >= 3}
    kept, dropped = [], []
    for l, n in zip(lines, norm):
        s = l.strip()
        if not s:
            kept.append(l); continue
        if _BANNER.match(s) or _CONVERTER_KV.match(s) or _PAGEMARK.match(s):
            dropped.append(l); continue
        if _BARE_NUM.match(s) or _ROMAN.match(_strip_md(s)):
            dropped.append(l); continue
        if n in repeated:
            dropped.append(l); continue
        kept.append(l)
    return "\n".join(kept), dropped

# --------------------------------------------------------------------------
# Masking + sentence segmentation
# --------------------------------------------------------------------------
def mask_citations(text: str) -> str:
    folded = _fold(text)
    masked = list(folded)
    def blank(m):
        for i in range(m.start(), m.end()):
            if masked[i] != "\n":
                masked[i] = "_"
    for pat in (_FULL_CITE, _SLIPOP, _DBCITE, _DOCKETNO, _REPORTER,
                _PAREN_YEAR, _STATUTE, _RECORD):
        for m in re.finditer(pat, folded):          # case-sensitive: name prefix is [A-Z]
            blank(m)
    for m in re.finditer(_HISTORY, folded, flags=re.I):
        blank(m)
    for m in re.finditer(_PAREN_EXPL, folded, flags=re.I):
        blank(m)
    return "".join(masked)

def _word_before(s: str, pos: int) -> str:
    start = pos
    while start > 0 and (s[start-1].isalnum() or s[start-1] in ".-'"):
        start -= 1
    return s[start:pos]

def _is_boundary(s: str, p: int) -> bool:
    if p < 0 or p >= len(s) or s[p] not in ".!?":
        return False
    j = p + 1
    while j < len(s) and s[j] in "\"')]”’":
        j += 1
    if j >= len(s):
        return True
    if not s[j].isspace():
        return False
    k = j
    while k < len(s) and s[k].isspace():
        k += 1
    if k >= len(s):
        return True
    nxt = s[k]
    if nxt.isdigit():
        return False
    if nxt.isalpha() and nxt.islower():
        return False
    w = _word_before(s, p).lower().rstrip(".")
    if w in LEGAL_ABBREVS:
        return False
    if len(w) == 1 and w.isupper():
        return False
    if re.search(r"\d(?:st|nd|rd|th|d)$", w):
        return False
    if "." in w and all(len(seg) <= 2 for seg in w.split(".") if seg):
        return False
    return True

# A real sentence is essentially never longer than this many characters.  If
# segmentation misses every boundary -- citation-dense text with reporter
# periods masked to "_" and abbreviations everywhere -- a "sentence" can run
# for thousands of chars and swallow several citations, handing them all the
# same garbled proposition.  The backstop in _sentence_bounds clamps any span
# longer than this to the nearest genuine boundary around the cite.
_MEGA_SENTENCE = 700

def _sentence_bounds(text: str, pos: int, masked: Optional[str] = None) -> Tuple[int, int]:
    b = masked if masked is not None else text
    start = 0
    for i in range(pos-1, -1, -1):
        if b[i] in ".!?" and _is_boundary(b, i):
            start = i + 1; break
        if b[i] == "\n" and i > 0 and b[i-1] == "\n":
            start = i + 1; break
    end = len(text)
    for i in range(pos, len(text)):
        if b[i] in ".!?" and _is_boundary(b, i):
            end = i + 1
            # D2 (2026.07.14): a boundary period INSIDE a quotation is
            # followed by the closing mark -- include it, or the extracted
            # proposition ends mid-quote ('...claimants' with no closing ."").
            while end < len(text) and text[end] in "\"')]\u201d\u2019":
                end += 1
            break
        if b[i] == "\n" and i + 1 < len(b) and b[i+1] == "\n":
            end = i; break
    # Mega-sentence backstop (defensive): never let one span swallow many cites.
    if end - start > _MEGA_SENTENCE:
        half = _MEGA_SENTENCE // 2
        lo = max(start, pos - half)
        new_start = lo
        for i in range(pos - 1, lo - 1, -1):
            if b[i] in ".!?" and _is_boundary(b, i):
                new_start = i + 1; break
        hi = min(end, pos + half)
        new_end = hi
        for i in range(pos, hi):
            if b[i] in ".!?" and _is_boundary(b, i):
                new_end = i + 1; break
        start, end = new_start, max(new_end, min(end, pos + 1))
    return start, end

def _prev_sentence_bounds(text: str, before: int, masked: Optional[str] = None) -> Tuple[int, int]:
    b = masked if masked is not None else text
    end_b = None
    for i in range(before-1, -1, -1):
        if b[i] in ".!?" and _is_boundary(b, i):
            end_b = i; break
        if b[i] == "\n" and i > 0 and b[i-1] == "\n":
            end_b = i; break
    if end_b is None:
        return 0, before
    start = 0
    for i in range(end_b-1, -1, -1):
        if b[i] in ".!?" and _is_boundary(b, i):
            start = i + 1; break
        if b[i] == "\n" and i > 0 and b[i-1] == "\n":
            start = i + 1; break
    end = end_b + 1
    # D2 (2026.07.14): include closing quote/bracket marks after the period.
    while end < len(text) and text[end] in "\"')]\u201d\u2019":
        end += 1
    return start, end

# --------------------------------------------------------------------------
# Cleaning / de-citing
# --------------------------------------------------------------------------
def _clean(s: str) -> str:
    s = _fold(_strip_md(s)).strip()
    s = re.sub(r"^\d{1,2}\s+(?=[A-Z])", "", s)        # leading footnote-marker number
    s = re.sub(r"&?\s*\bn\.\s*\d+\b", " ", s)      # footnote refs "& n.13"
    s = re.sub(r"\(\s*\(", "(", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"\s+([,.;])", r"\1", s)               # orphaned punct from removed inline cites
    s = re.sub(r"([,;])\s*([,;.])", r"\2", s)
    s = re.sub(r"\bin\s*,", "", s)                    # "as noted in , the" -> tidy
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r'^[\s(){};,&.\"\']+', "", s)
    s = re.sub(r"[\s(;,&]+$", "", s)
    return s.strip(" ,;")

def _content_words(s: str) -> List[str]:
    return re.findall(r"[a-z]{3,}", _clean(s).lower())

def _is_bare(s: str) -> bool:
    cw = [w for w in _content_words(s) if w not in
          {"see","cf","accord","supra","infra","compare","contra","also","generally"}]
    return len(cw) < 4

def _decite(s: str) -> str:
    s = _fold(s)
    s = re.sub(_HISTORY, " ", s, flags=re.I)
    s = re.sub(_FULL_CITE, " ", s)
    s = re.sub(_PAREN_EXPL, " ", s, flags=re.I)
    s = re.sub(_PAREN_YEAR, " ", s)
    s = re.sub(_SLIPOP, " ", s)
    s = re.sub(_DBCITE, " ", s)
    s = re.sub(_DOCKETNO, " ", s)
    s = re.sub(_REPORTER, " ", s)
    s = re.sub(_STATUTE, " ", s)
    s = re.sub(_RECORD, " ", s, flags=re.I)
    # Drop italic case-name spans (a '*...*' span that is a case name/cite).
    def _itrepl(m):
        inner = m.group(1)
        low = inner.lower().strip(" .,'")
        if (" v. " in inner or " v " in inner
                or re.match(r"(see|cf|accord|compare|but see|e\.g\.|quoting|citing|id\.?|supra|infra)\b", low)
                or re.search(r"\d{1,3}\s+[A-Z]", inner)):
            return " "
        return inner
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", _itrepl, s)
    s = re.sub(r"\b(?:In re|Matter of|Estate of|Ex parte)\s+[A-Z][^,;.]{0,90}", " ", s)
    # Remaining bare case-name "X v. Y," fragments at clause edges.
    # D1 (2026.07.14): an inline case name whose defendant side carries
    # comma-separated parties ("Madeksho v. Abraham, Watkins, Nichols &
    # Friend, makes clear ...") used to be clipped at the first comma,
    # stranding "Watkins, Nichols & Friend" inside the proposition. The
    # optional continuation consumes capitalized comma-runs ONLY when the
    # run ends at a delimiter ([,.;]), so prose after the name ("..., Texas
    # courts hold") is never swallowed.
    s = re.sub(r"\b[A-Z][A-Za-z.'&-]+(?:\s+[A-Z][A-Za-z.'&-]+){0,4}\s+v\.\s+[A-Z][^,.;]{0,60}"
               r"(?:(?:,\s*(?:[A-Z][\w.'&-]+|&)(?:\s+(?:[A-Z][\w.'&-]+|&))*)+(?=[,.;]))?",
               " ", s)
    s = re.sub(r"\b(see also|see, e\.g\.|see generally|see|cf\.|accord|compare|but see|but cf\.|contra|e\.g\.|i\.e\.|quoting|citing|id\.)\b", " ", s, flags=re.I)
    s = re.sub(r"\*{1,3}", "", s)
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" ,;")
    # D2 (2026.07.14): keep a terminal period that closes the sentence or a
    # quotation; strip only orphaned periods left by cite removal.
    s = re.sub(r"\s+\.$", "", s)
    return s

def _is_citation_sentence(raw: str, masked_sent: Optional[str] = None) -> bool:
    """A sentence whose only content (after citations are masked) is case names,
    signals, and punctuation -- i.e., a string cite -- has essentially no
    lowercase content words.  A real proposition has verbs/connectives.

    2026.07.04 (footnote fix session): parenthetical content is stripped
    BEFORE counting -- a string cite whose statute member carries a quoted
    parenthetical ('...; CPLR 3014 ("A copy of any writing ... for all
    purposes.")') otherwise reads as substantive and ships the citation
    sentence as the proposition (the Gold-Set-A Alliance occ-2 card).  A
    substantive parenthetical that belongs to THIS cite is still honored
    by the dedicated _parenthetical_after branch; sentence-level substance
    must live OUTSIDE the parentheses."""
    base = masked_sent if masked_sent is not None else mask_citations(raw)
    for _ in range(4):  # innermost-out, bounded
        stripped = re.sub(r"\([^()]*\)", " ", base)
        if stripped == base:
            break
        base = stripped
    vis = base.replace("_", " ")
    stop = {"v","vs","see","also","cf","accord","compare","contra","but","eg","e","g",
            "ie","i","in","re","of","the","ex","parte","matter","id","supra","infra",
            "at","no","nos","and","et","al","op","mem","slip","u","s","f","d"}
    low = [w for w in re.findall(r"\b[a-z]{2,}\b", vis) if w not in stop]
    return len(low) < 2

# --------------------------------------------------------------------------
# Trailing parenthetical (holding/quoting) attached to THIS cite
# --------------------------------------------------------------------------
def _parenthetical_after(text: str, pos: int, window: int = 600) -> Optional[str]:
    region = _fold(text[pos:pos+window])
    stop = re.search(r";|\b" + _SIGNAL.replace("(?:", "(?:") + r"\b", region, flags=re.I)
    # bound to this cite: stop at a string-cite separator
    semi = region.find(";")
    if semi != -1:
        region = region[:semi]
    # skip the court-year parenthetical, then look for a following (...) group
    yp = re.search(_PAREN_YEAR, region)
    scan_from = yp.end() if yp else 0
    tail = region[scan_from:]
    m = re.search(r"\(", tail)
    if not m:
        return None
    if tail[:m.start()].strip(" ,\n"):
        return None
    depth = 1; j = m.start() + 1
    while j < len(tail) and depth:
        depth += (tail[j] == "(") - (tail[j] == ")"); j += 1
    inner = tail[m.start()+1:j-1].strip()
    # keep only substantive holding/quoting parentheticals
    mq = re.match(r'(?:quoting|citing|holding|noting|explaining|finding|concluding|recognizing)?\s*"?([^"]{12,})"?', inner, flags=re.I)
    cleaned = _clean(re.sub(r"^(quoting|citing|holding|noting|explaining|finding|concluding|recognizing)\b[^\"]*", "", inner, flags=re.I))
    if _is_bare(cleaned):
        return None
    return cleaned

# --------------------------------------------------------------------------
# Footnotes (generic; spans joined across page breaks)
# --------------------------------------------------------------------------
_FN_START = re.compile(r"^([1-9][0-9]?)\s+[A-Z\"“]")

def find_footnotes(text: str) -> List[Dict]:
    """Locate footnote blocks ("N Text ..." lines).

    2026.07.04 (footnote fix): a block ends at the first BLANK LINE
    (paragraph break) after the footnote-start line, or at the next
    footnote-start line, whichever comes first.  Page-bottom footnotes are
    spliced into the body stream by the PDF converter, so the old
    next-footnote-only rule swallowed ALL body text that followed a
    footnote -- every later citation in the brief then "lived in" the
    footnote and inherited its lead sentence (observed live on Gold-Set-A:
    FN 2's block claimed citations 0-21).  Footnotes in these briefs are
    single paragraphs; a multi-paragraph footnote ends early, which only
    sends the cite to the ordinary body extractor -- safe.
    """
    lines = text.split("\n"); offs = []; off = 0
    for ln in lines:
        offs.append(off); off += len(ln) + 1
    foots = []; i = 0
    while i < len(lines):
        if not _FN_START.match(lines[i]):
            i += 1; continue
        start = offs[i]; j = i + 1
        while j < len(lines):
            if _FN_START.match(lines[j]) or not lines[j].strip():
                break
            j += 1
        end = offs[j] - 1 if j < len(lines) else len(text)
        body = text[start:end]
        lead = _first_sentence(re.sub(r"^[1-9][0-9]?\s+", "", body))
        foots.append({"spans": [(start, end)], "lead": lead})
        i = j
    return foots

def _first_sentence(s: str) -> str:
    s = s.strip()
    for i, ch in enumerate(s):
        if ch in ".!?" and _is_boundary(s, i):
            return s[:i+1].strip()
    return s

def _in_footnote(pos: int, foots) -> Optional[Dict]:
    for f in foots:
        for s, e in f["spans"]:
            if s <= pos < e:
                return f
    return None

# --------------------------------------------------------------------------
# Public structural locator + extractor
# --------------------------------------------------------------------------
def find_citations(text: str) -> List[Tuple[int, int, str]]:
    folded = _fold(text)
    out = []
    for pat in (_SLIPOP, _DBCITE, _REPORTER):
        for m in re.finditer(pat, folded):
            out.append((m.start(), m.end(), folded[m.start():m.end()]))
    out.sort()
    # de-overlap
    pruned = []
    last_end = -1
    for s, e, t in out:
        if s >= last_end:
            pruned.append((s, e, t)); last_end = e
    return pruned

def _host_proposition(text, pos, masked) -> Dict:
    s, e = _sentence_bounds(text, pos, masked)
    raw = text[s:e]
    if _is_citation_sentence(raw, masked[s:e]):
        ps, pe = _prev_sentence_bounds(text, s, masked)
        prop = _decite(text[ps:pe])
        prop = _clean(prop)
        if not _is_bare(prop):
            return _host(prop)
        return _host(prop, na=True, reason="preceding sentence not substantive")
    prop = _clean(_decite(raw))
    if not _is_bare(prop):
        return _host(prop)
    ps, pe = _prev_sentence_bounds(text, s, masked)
    prev = _clean(_decite(text[ps:pe]))
    if not _is_bare(prev):
        return _host(prev)
    return _host(prop or prev, na=True, reason="no substantive proposition near cite")

def _host(prop, na=False, reason=""):
    return {"proposition": prop, "kind": "host", "source": "body",
            "needs_attention": na, "reason": reason}

def _footnote_prop(text, foot, name, pos=None) -> Dict:
    """Proposition for a citation instance INSIDE a brief footnote.

    2026.07.04 (footnote fix): a footnote sometimes merely drops a cite
    (bare -- the body sentence at the marker governs, handled by the
    caller's legacy fallback), but often carries NEW substantive content
    the cite supports.  Extraction order for the substantive case:
      1. a holding/quoting parenthetical attached to THIS citation;
      2. the host sentence at the citation's own position within the
         footnote (with the standard walk-back), confined to the footnote
         span so body text never bleeds in;
      3. the footnote's lead sentence (old behavior, kept as fallback --
         correct for single-sentence footnotes).
    A bare result is flagged needs_attention so the caller can fall back
    to the body-sentence-at-marker logic and the props-review manifest.
    """
    fs, fe = foot["spans"][0]
    fn_text = text[fs:fe]
    lt = " ".join(_fold(text[s:e]) for s, e in foot["spans"])
    rel = (pos - fs) if pos is not None and fs <= pos < fe else None
    # 1. holding/quoting parenthetical attached to THIS cite: first
    #    "(Year)"-style close after the instance, then an immediate "(".
    anchor = rel if rel is not None else 0
    yp = re.search(_PAREN_YEAR, lt[anchor:])
    if yp:
        after = lt[anchor + yp.end():]
        mm = re.search(r"\(", after)
        if mm and not after[:mm.start()].strip(" ,\n"):
            depth = 1; j = mm.start() + 1
            while j < len(after) and depth:
                depth += (after[j] == "(") - (after[j] == ")"); j += 1
            inner = re.sub(r"^(quoting|citing|holding)\s+", "", after[mm.start()+1:j-1].strip(), flags=re.I)
            prop = _clean(_strip_md(inner))
            if not _is_bare(prop):
                return {"proposition": prop, "kind": "citing_parenthetical",
                        "source": "footnote", "needs_attention": False, "reason": ""}
    # 2. host sentence at the instance's own position, confined to the
    #    footnote text (walk-back cannot leave the footnote).
    if rel is not None:
        host = _host_proposition(fn_text, rel, mask_citations(fn_text))
        hp = _clean(host.get("proposition") or "")
        if hp and not _is_bare(hp) and not host.get("needs_attention"):
            return {"proposition": hp, "kind": "footnote", "source": "footnote",
                    "needs_attention": False, "reason": ""}
    # 3. lead sentence fallback.
    lead = _clean(_decite(foot["lead"])); na = _is_bare(lead)
    return {"proposition": lead, "kind": "footnote", "source": "footnote",
            "needs_attention": na, "reason": "bare footnote" if na else ""}

def extract(text: str, pos: int, name: str = "", foots=None,
            signal: str = "", masked: Optional[str] = None) -> Dict:
    if masked is None:
        masked = mask_citations(text)
    if foots is None:
        foots = []
    f = _in_footnote(pos, foots)
    if f:
        return _footnote_prop(text, f, name, pos=pos)
    if signal == "id.+quote":
        ps, pe = _prev_sentence_bounds(text, pos, masked)
        prop = _clean(_decite(text[ps:pe])); na = _is_bare(prop)
        return {"proposition": prop, "kind": "id_quote", "source": "body",
                "needs_attention": na, "reason": "bare id." if na else ""}
    qp = _parenthetical_after(text, pos)
    if qp:
        return {"proposition": _clean(qp), "kind": "parenthetical", "source": "body",
                "needs_attention": _is_bare(qp), "reason": ""}
    return _host_proposition(text, pos, masked)
