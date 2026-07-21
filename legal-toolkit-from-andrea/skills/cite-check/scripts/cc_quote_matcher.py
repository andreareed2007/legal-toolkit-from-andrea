"""cc_quote_matcher.py -- graded quote-fidelity matching, v3 (2026.07.15).

v1 (2026.07.09) was adapted from rlfordon/citation-verifier quote_matcher.py
(MIT license, Rebecca Fordon). v2 (2026.07.14) rewrote the matching core for
the report redesign. v3 (Phase 8, 2026.07.15, the author's QA of the Phase 7
QA-Brief report) fixes the permitted-alteration path and adds display support:

* Bracketed SUBSTITUTIONS of any length ([judgment creditor] for "relator")
  are wildcards on the segment path; single-letter brackets that fold badly
  (part[y] for "parties") get a second, split-at-the-bracket pass. Segment
  edges are stripped of punctuation, so a quote that ends mid-sentence (the
  brief's closing period where the opinion sentence continues) still
  matches. QA-Brief cits 11 and 15.
* Dash folding: em/en dashes and the Windows-1252 \\x96/\\x97 bytes fold to a
  space on BOTH sides (the opinion's "Malones\\x97the" vs the brief's
  em-dash broke cit 15).
* Bracket gaps are capped (a substitution spans a few words, never pages);
  ellipsis gaps stay unlimited by design.
* quote_diff(): word-level alignment for the report -- which words the
  brief ADDED and which opinion words the brief is MISSING; edge context is
  trimmed so only true differences flag.
* clean_passage(): star-page tokens, bracket page markers, orphan leading
  punctuation, and a leading reporter-citation fragment are stripped from
  DISPLAYED passages (the ". *549Cleveland v. Ward ..." junk, cit 10).

Stdlib-only. Public surface: verify_quote(), QuoteVerification, QuoteMatch,
detect_license_signal(), quote_diff(), clean_passage(). OCR-confusion rules
kept but OFF by default (was_ocrd=False); our PDF path goes through
pdf-to-cowork-txt.
"""
from __future__ import annotations

import difflib
import enum
import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
_QUOTE_FOLD = {"“": '"', "”": '"', "‘": "'", "’": "'"}

# Dash variants folded to a space on BOTH sides (Phase 8): em dash, en dash,
# horizontal bar, and the raw Windows-1252 control bytes that survive some
# CourtListener copies (\x96 en dash, \x97 em dash).
_DASH_RE = re.compile("[–—―\x96\x97]")

# Reporter-pagination noise inside opinion text: star pages ("*688", "* 688")
# and bracketed page markers ("[688]"). Stripped from the HAYSTACK only.
_STAR_TOKEN_RE = re.compile(r"\*\s*\d{1,5}(?!\d)")
_PAGE_BRACKET_RE = re.compile(r"\[\s*\d{1,5}\s*\]")


def _fold_dashes(s: str) -> str:
    return _DASH_RE.sub(" ", s)


def _normalize_quote_text(text: str) -> str:
    """Normalize NEEDLE text: fold smart quotes and dashes, strip bracketed
    alterations ([T] -> t, [word] -> ''), collapse ellipses and whitespace."""
    s = text
    for k, v in _QUOTE_FOLD.items():
        s = s.replace(k, v)
    s = _fold_dashes(s)
    s = re.sub(r"\[([A-Za-z])\]", lambda m: m.group(1).lower(), s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = s.replace("…", " ")
    s = re.sub(r"\.{3,}", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_haystack(text: str):
    """Normalize OPINION text for matching; returns (norm, idxmap).

    norm: lowercased, smart quotes folded, dashes folded to spaces,
    star-page/bracket-page tokens removed, whitespace collapsed to single
    spaces.
    idxmap: for each char of norm, its offset in the RAW text, so matched
    passages can be extracted from the original."""
    out: list = []
    idx: list = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "*":
            m = _STAR_TOKEN_RE.match(text, i)
            if m:
                i = m.end()
                continue
        if ch == "[":
            m = _PAGE_BRACKET_RE.match(text, i)
            if m:
                i = m.end()
                continue
        ch = _QUOTE_FOLD.get(ch, ch)
        if ch.isspace() or _DASH_RE.match(ch):
            if out and out[-1] != " ":
                out.append(" ")
                idx.append(i)
            i += 1
            continue
        out.append(ch.lower())
        idx.append(i)
        i += 1
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    start = 0
    while start < len(out) and out[start] == " ":
        start += 1
    return "".join(out[start:]), idx[start:]


# OCR-confusion normalization (conservative, one-directional). Case-sensitive
# O/l rules => must run BEFORE any .lower().
_OCR_RN_RE = re.compile(r"(?<=\w)rn")
_OCR_O_RE = re.compile(r"(?<=\d)O|O(?=\d)")
_OCR_L_RE = re.compile(r"(?<=\d)l|l(?=\d)")


def _normalize_ocr_confusions(text: str) -> str:
    """Collapse the three canonical OCR misreads. Idempotent; clean-text no-op."""
    out = _OCR_RN_RE.sub("m", text)
    out = _OCR_O_RE.sub("0", out)
    out = _OCR_L_RE.sub("1", out)
    return out


# --------------------------------------------------------------------------
# License signals -- "(cleaned up)" and friends (G4). The brief itself
# declares that the quotation was altered, so fidelity grading must not
# punish the licensed alterations.
# --------------------------------------------------------------------------
_LICENSE_RE = re.compile(
    r"\(\s*(cleaned\s+up|internal\s+quotation\s+marks?\s+omitted|"
    r"quotation\s+marks?\s+omitted|citations?\s+omitted|"
    r"alterations?\s+(?:omitted|accepted|in\s+original)|"
    r"emphasis\s+(?:added|omitted|in\s+original)|"
    r"footnotes?\s+omitted|brackets?\s+omitted|ellips[ei]s\s+omitted)"
    r"[^)]*\)", re.IGNORECASE)


def detect_license_signal(text: str) -> str:
    """Return the first alteration-license parenthetical in ``text`` (e.g.
    '(cleaned up)'), or '' when none is present."""
    m = _LICENSE_RE.search(text or "")
    return m.group(0) if m else ""


# --------------------------------------------------------------------------
# Segment matching -- permitted-alteration path (v3, Phase 8)
# --------------------------------------------------------------------------
_SEG_TOKEN_RE = re.compile(r"\[[^\]]*\]|…|\.{3,}")
_SEG_MIN = 8          # ignore literal fragments shorter than this
_SEG_COVER_MIN = 0.5  # matched literal chars must cover >= 50% of the needle
_SEG_BRACKET_GAP_MAX = 80  # a bracket substitution spans words, never pages
_SEG_EDGE_PUNCT = " \t\n,.;:'\"()“”‘’"


def _segments(raw_needle: str, split_single: bool = False):
    """(segs, gaps, altered): literal text runs between bracketed
    alterations / ellipses, with the separator TYPE between consecutive
    kept segments ('br' bracket substitution, 'el' ellipsis).

    split_single=False folds single-letter case alterations ([T]he) into the
    neighboring text; split_single=True treats them as separators too -- the
    part[y]-for-"parties" class, where the folded word ("party") appears
    nowhere in the opinion (Phase 8, QA-Brief cit 15). Segment edges are
    stripped of punctuation so the brief's closing period never has to match
    a mid-sentence opinion (QA-Brief cit 11)."""
    s = raw_needle
    for k, v in _QUOTE_FOLD.items():
        s = s.replace(k, v)
    if not split_single:
        s = re.sub(r"\[([A-Za-z])\]", lambda m: m.group(1).lower(), s)
    altered = bool(_SEG_TOKEN_RE.search(s)) or "[" in raw_needle
    parts, seps = [], []
    pos = 0
    for m in _SEG_TOKEN_RE.finditer(s):
        parts.append(s[pos:m.start()])
        seps.append("br" if m.group(0).startswith("[") else "el")
        pos = m.end()
    parts.append(s[pos:])
    segs, gaps = [], []
    pending = None
    for i, p in enumerate(parts):
        p = _fold_dashes(p)
        p = re.sub(r"\s+", " ", p).strip().strip(_SEG_EDGE_PUNCT).strip().lower()
        sep = seps[i] if i < len(seps) else None
        if len(p) >= _SEG_MIN:
            if segs:
                gaps.append(pending if pending is not None else "br")
            segs.append(p)
            pending = sep
        else:
            # Dropped short fragment: merge into the pending gap. An
            # ellipsis anywhere in the merged run keeps the gap unbounded.
            if pending is None:
                pending = sep
            elif sep == "el" or pending == "el":
                pending = "el"
    return segs, gaps, altered


def _segments_in_order(segs, gaps, haystack_norm: str,
                       bracket_gap_max: int = _SEG_BRACKET_GAP_MAX):
    """If every segment appears in haystack_norm at increasing positions --
    with bracket gaps capped at ``bracket_gap_max`` chars and ellipsis gaps
    unbounded -- return (first_start, last_end); else None."""
    pos = 0
    first_start, last_end = None, None
    for k, seg in enumerate(segs):
        at = haystack_norm.find(seg, pos)
        if at < 0:
            return None
        if k > 0 and gaps[k - 1] == "br" and (at - pos) > bracket_gap_max:
            return None
        if first_start is None:
            first_start = at
        last_end = at + len(seg)
        pos = last_end
    if first_start is None:
        return None
    return first_start, last_end


# --------------------------------------------------------------------------
# Fuzzy matching -- same-length windows + alignment refinement (G1)
# --------------------------------------------------------------------------
def _best_ratio(needle: str, haystack: str):
    """Best difflib ratio of ``needle`` against same-length windows of
    ``haystack``; returns (ratio, start, window_len)."""
    w = len(needle)
    if not w or not haystack:
        return 0.0, 0, 0
    if len(haystack) <= w:
        return difflib.SequenceMatcher(None, needle, haystack,
                                       autojunk=False).ratio(), 0, len(haystack)
    step = max(1, w // 8)
    best, best_start = 0.0, 0
    for start in range(0, len(haystack) - w + 1, step):
        r = difflib.SequenceMatcher(None, needle, haystack[start:start + w],
                                    autojunk=False).ratio()
        if r > best:
            best, best_start = r, start
            if best > 0.98:
                break
    # Alignment refinement: char-step around the coarse winner, and a
    # slightly wider window to absorb small insertions in the opinion copy.
    lo = max(0, best_start - step)
    hi = min(len(haystack) - 1, best_start + step)
    best_wl = w
    for start in range(lo, hi + 1):
        for wl in (w, w + max(2, w // 10)):
            chunk = haystack[start:start + wl]
            if not chunk:
                continue
            r = difflib.SequenceMatcher(None, needle, chunk,
                                        autojunk=False).ratio()
            if r > best:
                best, best_start, best_wl = r, start, wl
    return best, best_start, best_wl


# Bucketing thresholds. VERBATIM is strictly above _VERBATIM_MIN; CLOSE is at
# or above _CLOSE_MIN; else FABRICATED. _LICENSE_MIN: with an explicit
# alteration-license signal ('(cleaned up)'), a match at or above this floor
# classifies VERBATIM with license_applied=True.
_VERBATIM_MIN = 0.85
_CLOSE_MIN = 0.6
_LICENSE_MIN = 0.72
# Fuzzy-path VERBATIM also requires word-level fidelity: char-ratio 0.86 on a
# long quote can hide several substituted words ("can correct only clerical"
# vs "may modify substantive" scored 0.86 in testing). Alpha-only tokens so
# footnote-marker digits and punctuation noise do not count as word changes.
_WORD_RATIO_MIN = 0.98


def _word_ratio(a: str, b: str) -> float:
    """Word-level difflib ratio on alpha-only tokens."""
    ta = [t for t in (re.sub(r"[^a-z]", "", w) for w in a.split()) if t]
    tb = [t for t in (re.sub(r"[^a-z]", "", w) for w in b.split()) if t]
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


class QuoteMatch(str, enum.Enum):
    """How well a quote matched the opinion text."""
    VERBATIM = "VERBATIM"
    CLOSE = "CLOSE"
    FABRICATED = "FABRICATED"


@dataclass(frozen=True)
class QuoteVerification:
    """Result of verifying one quote against one opinion's text."""
    quote: str              # the RAW input quote, echoed verbatim
    result: QuoteMatch
    similarity: float       # 0.0-1.0 (1.0 = exact / segments-exact)
    matched_passage: str    # best-matching span from RAW opinion text ("" if none)
    was_ocrd: bool          # whether OCR-confusion rules were applied
    alterations_only: bool = False  # VERBATIM via permitted alterations only
    license_applied: bool = False   # VERBATIM granted under a license signal
    matched_window: str = ""        # cleaned, word-bounded aligned window (display)
    diff: object = None             # quote_diff() output for CLOSE results
    clean_alterations: object = None  # CLOSE only: True when every literal
                                      # segment between brackets/ellipses
                                      # appears in order in the opinion (the
                                      # only differences are permitted
                                      # alterations); False = a word was
                                      # dropped/changed silently (misquote).


# --------------------------------------------------------------------------
# Display helpers (Phase 8): passage cleaning + word-level diff
# --------------------------------------------------------------------------
_CITE_FRAG_RE = re.compile(r"\d+\s+[A-Z][A-Za-z.'\d ]{0,28}?\s\d+")


def clean_passage(text: str) -> str:
    """Strip display junk from an opinion passage: star-page tokens (*549),
    bracket page markers, orphan leading punctuation, and a LEADING
    reporter-citation fragment sentence ('Cleveland v. Ward, 116 Tex. 1,
    285 S.W. 1063.'). Whitespace collapsed. Content sentences untouched."""
    s = _STAR_TOKEN_RE.sub(" ", text or "")
    s = _PAGE_BRACKET_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.lstrip(" .,;:)'\"”’")
    # Leading citation fragment: scan sentence boundaries in the first ~100
    # chars and strip through the LAST one whose head reads like a reporter
    # citation ('Cleveland v. Ward, 116 Tex. 1, 285 S.W. 1063.'). A head
    # ending in a digit is the citation-tail signature -- 'v.' and reporter
    # abbreviation dots never end in one.
    cut = 0
    for m in re.finditer(r"\.\s+", s[:110]):
        head = s[:m.start()]
        if not head:
            continue
        if head[-1].isdigit() and _CITE_FRAG_RE.search(head) \
                and (" v. " in head or head.count(",") >= 2):
            cut = m.end()
    if cut:
        s = s[cut:]
    return s.strip()


_TOK_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _norm_tok(w: str) -> str:
    s = w
    for k, v in _QUOTE_FOLD.items():
        s = s.replace(k, v)
    return _TOK_STRIP_RE.sub("", _fold_dashes(s).lower())


def quote_diff(quote: str, window: str):
    """Word-level alignment for the report's misquote display.

    Returns {"brief": [[word, flagged], ...], "opinion": [[word, flagged],
    ...]} -- flagged brief words are ADDITIONS not in the opinion; flagged
    opinion words are MISSING from the brief. Window-only runs at the edges
    are alignment context, not omissions, and are dropped. Returns None when
    nothing flags (or on empty input)."""
    qa = (quote or "").split()
    wb = (clean_passage(window) or "").split()
    if not qa or not wb:
        return None
    # Pure-punctuation tokens (ellipses, lone dashes) carry no alignment
    # signal -- exclude them from matching, never flag them.
    fa = [(i, w, _norm_tok(w)) for i, w in enumerate(qa)]
    fb = [(j, w, _norm_tok(w)) for j, w in enumerate(wb)]
    ka = [t for t in fa if t[2]]
    kb = [t for t in fb if t[2]]
    if not ka or not kb:
        return None
    ops = difflib.SequenceMatcher(None, [t[2] for t in ka],
                                  [t[2] for t in kb],
                                  autojunk=False).get_opcodes()
    while ops and ops[0][0] == "insert":
        ops = ops[1:]
    while ops and ops[-1][0] == "insert":
        ops = ops[:-1]
    if not ops:
        return None
    flag_a = {}
    flag_b = {}
    j_lo = ops[0][3]
    j_hi = ops[-1][4]
    for op, i1, i2, j1, j2 in ops:
        flag = op != "equal"
        for t in ka[i1:i2]:
            flag_a[t[0]] = flag
        for t in kb[j1:j2]:
            flag_b[t[0]] = flag
    brief = [[w, bool(flag_a.get(i, False))] for i, w in enumerate(qa)]
    # The window may carry alignment context past the trimmed edges -- keep
    # only the aligned span for display.
    b_keep = [t[0] for t in kb[j_lo:j_hi]]
    if b_keep:
        lo, hi = b_keep[0], b_keep[-1]
        opin = [[w, bool(flag_b.get(j, False))]
                for j, w in enumerate(wb) if lo <= j <= hi]
    else:
        opin = [[w, bool(flag_b.get(j, False))] for j, w in enumerate(wb)]
    if not any(f for _, f in brief) and not any(f for _, f in opin):
        return None
    return {"brief": brief, "opinion": opin}


def _passage_from_raw(raw: str, idxmap, npos: int, nlen: int,
                      context: int = 80) -> str:
    """Map a normalized-haystack span back to the raw text and extract it."""
    if not idxmap or npos >= len(idxmap):
        return ""
    raw_start = idxmap[npos]
    end_i = min(npos + max(nlen - 1, 0), len(idxmap) - 1)
    raw_end = idxmap[end_i] + 1
    return _extract_passage(raw, raw_start, raw_end - raw_start, context)


def _window_from_raw(raw: str, idxmap, npos: int, nlen: int) -> str:
    """The aligned window itself (no context), extended to word boundaries
    in the RAW text and display-cleaned."""
    if not idxmap or npos >= len(idxmap):
        return ""
    raw_start = idxmap[npos]
    end_i = min(npos + max(nlen - 1, 0), len(idxmap) - 1)
    raw_end = idxmap[end_i] + 1
    while raw_start > 0 and raw[raw_start - 1].isalnum():
        raw_start -= 1
    while raw_end < len(raw) and raw[raw_end - 1].isalnum() \
            and raw[raw_end].isalnum():
        raw_end += 1
    return clean_passage(raw[raw_start:raw_end])


def verify_quote(quote: str, opinion_text: str, *, was_ocrd: bool = False,
                 license_signal: bool = False,
                 strict: bool = False) -> QuoteVerification:
    """Verify a quote against opinion text. Public primitive.

    strict (Phase 6, 2026.07.14, Cit 17 short quotes): normalized-exact
    substring match ONLY -- bracket / ellipsis alterations resolved, then a
    literal substring test. No fuzzy bucketing: a very short needle (e.g.
    "gap[]" -> "gap") must not fuzzy-match arbitrary text, and a miss is a
    clean FABRICATED rather than a fuzzy CLOSE. Substring (not word-
    bounded) so an explicit alteration like "gap[]" matches within "gaps".
    """
    if not quote or not opinion_text:
        return QuoteVerification(quote=quote or "", result=QuoteMatch.FABRICATED,
                                 similarity=0.0, matched_passage="",
                                 was_ocrd=was_ocrd)
    raw_needle, raw_hay = quote, opinion_text
    if was_ocrd:
        raw_needle = _normalize_ocr_confusions(raw_needle)
        raw_hay = _normalize_ocr_confusions(raw_hay)

    hay_norm, idxmap = _normalize_haystack(raw_hay)

    if strict:
        # Phase 6 short-quote path: normalized-exact substring only.
        sq = _normalize_quote_text(raw_needle).lower()
        if sq and sq in hay_norm:
            pos = hay_norm.index(sq)
            return QuoteVerification(
                quote=quote, result=QuoteMatch.VERBATIM, similarity=1.0,
                matched_passage=_passage_from_raw(raw_hay, idxmap, pos,
                                                  len(sq)),
                was_ocrd=was_ocrd,
                alterations_only=("[" in raw_needle or "…" in raw_needle
                                  or "..." in raw_needle))
        return QuoteVerification(
            quote=quote, result=QuoteMatch.FABRICATED, similarity=0.0,
            matched_passage="", was_ocrd=was_ocrd)

    # 1. Pure exact: the quote as written (whitespace/smart quotes/dashes
    #    aside).
    plain = raw_needle
    for k, v in _QUOTE_FOLD.items():
        plain = plain.replace(k, v)
    plain = re.sub(r"\s+", " ", _fold_dashes(plain)).strip().lower()
    if plain and plain in hay_norm:
        pos = hay_norm.index(plain)
        return QuoteVerification(
            quote=quote, result=QuoteMatch.VERBATIM, similarity=1.0,
            matched_passage=_passage_from_raw(raw_hay, idxmap, pos, len(plain)),
            was_ocrd=was_ocrd)

    # 2. Permitted alterations: every literal segment between brackets /
    #    ellipses appears in order (bracket gaps capped). Two passes: fold
    #    single-letter brackets first; then split at them (part[y] class).
    for split_single in (False, True):
        segs, gaps, altered = _segments(raw_needle, split_single)
        if not (altered and segs):
            continue
        seg_chars = sum(len(s) for s in segs)
        span = _segments_in_order(segs, gaps, hay_norm)
        if span is not None and seg_chars >= _SEG_COVER_MIN * max(len(plain), 1):
            pos, end = span
            return QuoteVerification(
                quote=quote, result=QuoteMatch.VERBATIM, similarity=1.0,
                matched_passage=_passage_from_raw(raw_hay, idxmap, pos,
                                                  min(end - pos, 600)),
                was_ocrd=was_ocrd, alterations_only=True)

    # 3. Fuzzy: alteration-stripped needle vs same-length windows.
    needle_norm = _normalize_quote_text(raw_needle).lower()
    if not needle_norm:
        return QuoteVerification(quote=quote, result=QuoteMatch.FABRICATED,
                                 similarity=0.0, matched_passage="",
                                 was_ocrd=was_ocrd)
    ratio, start, wl = _best_ratio(needle_norm, hay_norm)
    passage = _passage_from_raw(raw_hay, idxmap, start, wl) if ratio >= 0.4 else ""

    if ratio > _VERBATIM_MIN and _word_ratio(
            needle_norm, hay_norm[start:start + wl]) >= _WORD_RATIO_MIN:
        result = QuoteMatch.VERBATIM
        licensed = False
    elif license_signal and ratio >= _LICENSE_MIN:
        result = QuoteMatch.VERBATIM
        licensed = True
    elif ratio >= _CLOSE_MIN:
        result = QuoteMatch.CLOSE
        licensed = False
    else:
        result = QuoteMatch.FABRICATED
        licensed = False
    window, diff, clean_alt = "", None, None
    if result is QuoteMatch.CLOSE:
        # Extend the diff window past the same-length alignment so the tail
        # of the quote is compared against real opinion text, not cut off;
        # quote_diff() trims surplus window context at the edges.
        window = _window_from_raw(raw_hay, idxmap, start,
                                  wl + max(40, wl // 4))
        diff = quote_diff(raw_needle, window)
        # author 2026.07.15 (QA-Brief cits 16 & 10): a CLOSE result is a MISQUOTE
        # unless every literal run between brackets/ellipses still appears in
        # order in the FULL opinion -- i.e. the only differences are bracket
        # substitutions and clean ellipsis omissions. A word dropped or
        # changed with no bracket/ellipsis (even where brackets appear
        # elsewhere) fails this test. Relaxed bracket gap + no coverage floor
        # (this is a fidelity classification, not the VERBATIM gate).
        clean_alt = False
        for _ss in (True, False):
            _segs, _gaps, _alt = _segments(raw_needle, _ss)
            if _alt and _segs and _segments_in_order(
                    _segs, _gaps, hay_norm, bracket_gap_max=240) is not None:
                clean_alt = True
                break
    return QuoteVerification(
        quote=quote, result=result, similarity=round(ratio, 2),
        matched_passage=passage, was_ocrd=was_ocrd,
        alterations_only=(result is QuoteMatch.VERBATIM and altered),
        license_applied=licensed, matched_window=window, diff=diff,
        clean_alterations=clean_alt)


def _extract_passage(text: str, match_start: int, match_len: int, context: int) -> str:
    """Extract a passage from text around a match, trimmed to sentences."""
    start = max(0, match_start - context)
    end = min(len(text), match_start + match_len + context)
    passage = text[start:end].strip()
    if start > 0:
        dot = passage.find(". ")
        if 0 < dot < context:
            passage = passage[dot + 2:]
    if end < len(text):
        dot = passage.rfind(". ")
        if dot > len(passage) - context and dot > 0:
            passage = passage[:dot + 1]
    return passage.strip()
