#!/usr/bin/env python3
"""cc_detect_eyecite.py -- citation detection on eyecite (Phase 1, 2026.07.03).

Replaces the Isaacus-enricher detection layer per the 2026.07.03 audit.
Maps eyecite's FullCaseCitation / ShortCaseCitation / SupraCitation /
IdCitation objects (grouped by resolve_citations) onto per-instance dicts
the pipeline turns into Citation objects.

Locked rules preserved:
  * per-instance counting (every full/short instance = its own entry);
  * id. folds into the preceding cite unless its sentence carries a direct
    quote (locked spec #6);
  * bare supra without a pincite folds into its antecedent (adds nothing
    checkable); supra WITH a pincite is its own instance;
  * no hardcoded reporters or case names -- reporters come from eyecite's
    reporters_db, jurisdiction from courts_db guesses via cl_resolver's map.

SPAN INVARIANT: get_citations runs on EXACTLY the text the spans index into
(the already-preprocessed argument text). eyecite's clean_text is NOT used
here -- our preprocessing already ran, and a second length-changing pass
would break span indexing (standing invariant, build journal Part 2).
"""
from __future__ import annotations

import re
from typing import List, Optional

from eyecite import get_citations, resolve_citations
from eyecite.models import (
    CitationBase,
    FullCaseCitation,
    FullJournalCitation,
    FullLawCitation,
    IdCitation,
    ShortCaseCitation,
    SupraCitation,
    UnknownCitation,
)

# Quote long enough to be a "direct quote" for the id. rule.
_ID_QUOTE_RE = re.compile('["\u201c\u201d\u201e\u201f\u00ab]''[^"\u201c\u201d\u201e\u201f\u00bb]{15,}''["\u201c\u201d\u201e\u201f\u00bb]')

# --- Record-material id. guards (2026.08.04, v16 false-CRITICAL fix) -------
# An "Id." that points at RECORD material (exhibit, pleading, deposition
# transcript, appendix, docket entry) is not a case citation and must never
# enter the case pipeline -- the v16 run graded a deposition quote against
# the neighboring case's opinion and branded it FABRICATED (a deposition exhibit
# "Id. at 28:21-23" -> Bolle), and sent an exhibit id. ("Id. at 12
# \u00b6\u00b6 49-50, Apx. __") to the resolver, which dragged in a random
# wrong-case caption. Two independent tells, either drops the id.:
#   (a) the id.'s own pincite/tail carries a record fingerprint -- a
#       paragraph cite (\u00b6), a transcript page:line ("28:21-23"), or an
#       appendix/exhibit marker;
#   (b) a record cite intervenes between the id. and the nearest preceding
#       case-ish cite: Bluebook id. refers to the IMMEDIATELY preceding
#       cite, which eyecite cannot see when it is record material, so the
#       chain must not silently skip over it to a case.
_RECORD_PIN_RE = re.compile(
    "\u00b6"
    "|\\b\\d+:\\d+(?:-\\d+)?\\b"
    "|\\bApx\\b|\\bApp'x\\b|\\bAppx\\b")
_RECORD_TAIL_RE = re.compile(
    "^[\\s,.;]*(?:at\\s+\\d+\\s*\u00b6|\u00b6|\u00a7"
    "|(?:Ex|Exs|Apx|Tr|Dkt|ECF)\\.?[\\s\\d])")
_RECORD_MARK_RE = re.compile(
    "\\b(?:Ex|Exs)\\.\\s|\\bApx\\.|\\bApp'x\\b|\\bAppx\\."
    "|\\bDep\\.(?:\\s|$)|\\bTr\\.\\s|\\bDkt\\.|\\bECF\\b"
    "|\\bR\\.\\s+at\\s|\\bCR\\s+at\\s|\\bRR\\s+at\\s")
_CASEISH_RE = re.compile(
    "\\sv\\.\\s|\\bIn re\\b|\\bMatter of\\b|\\bEx parte\\b"
    "|\\b\\d+\\s+[A-Z][\\w.]*\\.?(?:\\s?(?:2d|3d|4th|5th))?\\s+\\d+")


def _id_is_record(text, span_start, span_end, pin_cite):
    """True when an IdCitation points at record material (see above)."""
    probe = (pin_cite or "") + " " + text[span_end:span_end + 40]
    if _RECORD_PIN_RE.search(probe):
        return True
    if _RECORD_TAIL_RE.match(text[span_end:span_end + 40]):
        return True
    back = text[max(0, span_start - 260):span_start]
    last = None
    for last in _RECORD_MARK_RE.finditer(back):
        pass
    if last is not None and not _CASEISH_RE.search(back[last.end():]):
        return True
    return False


# --- Prose-antecedent name harvest (2026.08.04, Williams-short fix) --------
# A short form or id. whose antecedent is named only in PROSE ("In Williams
# v. Glash, the Supreme Court outlined ..." ... "789 S.W.2d at 265") gets no
# antecedent_guess from eyecite, so its "name" degraded to the cite string
# and the resolver's caption check rejected its own correct lookup hit. The
# harvested name is only a HINT: the resolver's caption + cite-address
# identity gates still validate it, so a wrong harvest degrades to today's
# behavior, never worse.
_NONNAME_RE = re.compile("^(?:id|supra|ante)\\.?$|^\\d+\\s+\\S+", re.I)
_PROSE_NAME_RE = re.compile(
    "((?:In re|Matter of|Ex parte)\\s+[A-Z][\\w.'\u2019&-]*"
    "(?:\\s+[A-Z][\\w.'\u2019&-]*){0,4}"
    "|[A-Z][\\w.'\u2019&-]*(?:\\s+[A-Z][\\w.'\u2019&-]*){0,4}"
    "\\s+v\\.\\s+[A-Z][\\w.'\u2019&-]*(?:\\s+[A-Z][\\w.'\u2019&-]*){0,4})")


def _prose_antecedent(text, start):
    """Nearest preceding prose case name within ~320 chars, or ''."""
    back = text[max(0, start - 320):start]
    last = None
    for last in _PROSE_NAME_RE.finditer(back):
        pass
    if last is None:
        return ""
    nm = last.group(1).strip(" ,")
    # "In Williams v. Glash, the Court ..." -- the "In" is prose framing,
    # not part of the case name (but "In re X" keeps its "In").
    if nm.startswith("In ") and not nm.startswith("In re"):
        nm = nm[3:]
    return nm


# Extend cite_text past the reporter core over an immediate pincite and the
# (Court Year) parenthetical, as written in the brief.
_CITE_TAIL_RE = re.compile(r"^(?:,\s*(?:at\s+)?[*¶]?\d[\w\s.,*¶&-]{0,38}|\s*[*¶]?\d[\w,*¶-]{0,12})?(?:\s*\([^()]{0,70}\))?")

# Generic non-case shapes (informational section only; not on the citation
# path): "ABBR § N" statutes and procedural-rule "R. <X>. P. N" forms.
_STATUTE_RE = re.compile(r"\b([A-Z][A-Za-z.']{1,12}(?:\s+[A-Z][A-Za-z.']{1,12}){0,3})\s*§§?\s*[\d][\w().-]{0,18}")
_RULE_RE    = re.compile(r"\b((?:[A-Z][A-Za-z.]{0,8}\s+)?R\.\s*(?:Civ|App|Evid|Crim)\.\s*P\.)\s*\d+[\w().]*")


def _pin_norm(pin: Optional[str]) -> str:
    """Normalize eyecite pin_cite for the resolver ('at 324' -> '324')."""
    p = (pin or "").strip()
    p = re.sub(r"^at\s+", "", p, flags=re.I)
    return p.strip(" ,")


def _sentence_around(text: str, pos: int) -> str:
    try:
        import cc_proposition as ccprop
        s, e = ccprop._sentence_bounds(text, pos)
        return text[s:e]
    except Exception:  # noqa: BLE001
        return text[max(0, pos - 200):pos + 200]


def _prev_sentence(text: str, pos: int) -> str:
    """The sentence immediately BEFORE the one containing *pos*.

    Used by the id.-folding guard: a footnote that merely drops a bare
    cite ("Id. at 704") in support of a quotation carried in the BODY
    sentence puts the quote one sentence back from the cite (Brief C
    magic-wand miss, 2026.07.13).
    """
    try:
        import cc_proposition as ccprop
        s, _e = ccprop._sentence_bounds(text, pos)
        if s <= 0:
            return ""
        ps, pe = ccprop._sentence_bounds(text, s - 1)
        return text[ps:pe]
    except Exception:  # noqa: BLE001
        return text[max(0, pos - 320):pos]


def _name_from_full(c: FullCaseCitation) -> str:
    md = c.metadata
    p = (md.plaintiff or "").strip(" ,")
    d = (md.defendant or "").strip(" ,")
    if p and d:
        return f"{p} v. {d}"
    return d or p or ""


def _cite_text(text: str, c: CitationBase) -> str:
    s0, s1 = c.span()
    fs0, _fs1 = c.full_span()
    start = min(fs0, s0)
    m = _CITE_TAIL_RE.match(text[s1:s1 + 90])
    end = s1 + (m.end() if m else 0)
    return text[start:end].strip().strip(",")


def _jurisdiction_guess(court_ids: List[str]) -> Optional[str]:
    """Document-level jurisdiction from eyecite courts_db guesses, mapped
    through cl_resolver's reverse court map. State codes win by majority;
    any federal court guess contributes 'US-FED'."""
    try:
        from cl_resolver import _CL_COURT_TO_JURISDICTIONS
    except Exception:  # noqa: BLE001
        return None
    votes: dict = {}
    for cid in court_ids:
        jurs = _CL_COURT_TO_JURISDICTIONS.get((cid or "").lower(), [])
        for j in jurs:
            # Collapse federal circuits/districts/SCOTUS to US-FED.
            code = j if j in ("US-NY", "US-TX", "US-FL", "US-CA", "US-DE") else "US-FED"
            votes[code] = votes.get(code, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


def detect(text: str) -> dict:
    """Run eyecite over preprocessed argument text.

    Returns {"instances": [dict], "non_case": [dict], "jurisdiction": str|None}.
    Instance dicts carry: name, cite_text, span_start, span_end,
    reporter_cite {volume, reporter, page}, pin_cite (as written),
    pincite (normalized), is_short_form, kind, occurrence_index,
    occurrence_count.
    """
    cites = get_citations(text)
    resolved = resolve_citations(cites)

    # citation object -> its resource's full citation (for inheritance)
    cite2full: dict = {}
    for resource, group in resolved.items():
        full = getattr(resource, "citation", None)
        for c in group:
            cite2full[id(c)] = full

    instances: List[dict] = []
    court_ids: List[str] = []
    per_resource_counts: dict = {}

    for c in sorted(cites, key=lambda x: x.span()[0]):
        if isinstance(c, (UnknownCitation, FullLawCitation, FullJournalCitation)):
            continue

        full = cite2full.get(id(c))
        if isinstance(c, FullCaseCitation):
            full = c

        id_unresolved_review = False
        if isinstance(c, IdCitation):
            # Record-material guard (2026.08.04): a record id. is dropped
            # BEFORE the quote logic -- resolved or not, a deposition or
            # exhibit id. must never be graded against a case opinion.
            if _id_is_record(text, c.span()[0], c.span()[1],
                             getattr(c.metadata, "pin_cite", "") or ""):
                continue
            # Locked spec #6 (amended 2026.07.13, Brief C magic-wand miss):
            # an id. folds into the preceding cite -- UNLESS a direct
            # quotation sits in its own sentence OR in the immediately
            # preceding sentence. The preceding-sentence arm catches the
            # footnote-drops-a-bare-cite-for-a-body-quote pattern: the
            # quote lives in the body sentence and the footnote is just
            # "Id. at NNN", often at a DISTINCT page from its antecedent,
            # so it can support a DISTINCT proposition and must not be
            # silently dropped. Quote detector is curly-quote aware.
            _own = _sentence_around(text, c.span()[0])
            _prev = _prev_sentence(text, c.span()[0])
            # American-style punctuation puts the period INSIDE the
            # closing quote, so sentence segmentation can split a
            # quotation across the prev/own boundary (the closing quote
            # lands alone in the id.'s sentence). Search the JOINED
            # window so a boundary-split quote still registers.
            _has_quote = bool(_ID_QUOTE_RE.search(_prev + " " + _own))
            if not _has_quote:
                # No direct quote in scope -> id. folds into its antecedent
                # (adds nothing separately checkable), resolved or not.
                continue
            if full is None:
                # Fix 4 (Finding 2/B1): the id. chain broke (no resolved
                # antecedent), but its sentence carries a direct quotation
                # that MUST be checked. Rather than silently drop it (the old
                # `if full is None: continue` bug that lost the 3rd MacFarland
                # quote), attach it to the nearest preceding instance's
                # authority and flag it review-required. GUARD: an id. that
                # points to a STATUTE/CONTRACT section ("id. § 12.1(c)") or an
                # EXHIBIT/record ("id., Ex. F") is not a case citation -- do
                # not rescue it as one (it would grade a contract/record quote
                # against the neighboring case's opinion).
                _tail = text[c.span()[1]:c.span()[1] + 10].lstrip()
                if _tail[:1] == "§" or re.match(r"(?:Ex|App|Tr|R|CR|RR)\.", _tail):
                    continue
                id_unresolved_review = True
        elif isinstance(c, SupraCitation):
            # Bare supra (no pincite) adds nothing checkable; folds.
            if full is None or not _pin_norm(c.metadata.pin_cite):
                continue
        elif not isinstance(c, (FullCaseCitation, ShortCaseCitation)):
            continue

        if isinstance(c, FullCaseCitation) and c.metadata.court:
            court_ids.append(c.metadata.court)

        name = ""
        if full is not None:
            name = _name_from_full(full)
        if not name and isinstance(c, FullCaseCitation):
            # A repeat full cite of the same authority can miss its own name
            # tokens (docket-number prefix, mid-sentence cite); inherit the
            # resolved group's full citation name.
            gf = cite2full.get(id(c))
            if gf is not None and gf is not c:
                name = _name_from_full(gf)
        if not name and not isinstance(c, FullCaseCitation):
            name = (getattr(c.metadata, "antecedent_guess", "") or "").strip(" ,")
        if ((not name or _NONNAME_RE.match(name))
                and isinstance(c, (ShortCaseCitation, IdCitation,
                                   SupraCitation))):
            # 2026.08.04: recover the case name from preceding prose when
            # eyecite's antecedent guess came up empty (identity gates
            # downstream validate the harvested name).
            _pn = _prose_antecedent(text, c.span()[0])
            if _pn:
                name = _pn
        if not name:
            name = c.matched_text()

        rc = None
        src = c if isinstance(c, (FullCaseCitation, ShortCaseCitation)) else full
        if src is not None and getattr(src, "groups", None):
            g = src.groups
            rc = {"volume": g.get("volume"), "reporter": g.get("reporter"),
                  "page": (full.groups.get("page") if full is not None and full.groups else g.get("page"))}
            # Short forms carry the AT page in groups['page']; the canonical
            # first page comes from the resource's full cite (above). For a
            # full cite both are the same object.

        pin_raw = getattr(c.metadata, "pin_cite", None) or ""
        if isinstance(c, ShortCaseCitation) and not pin_raw:
            pin_raw = c.groups.get("page") or ""

        s0, s1 = c.span()
        rkey = id(full) if full is not None else id(c)
        if id_unresolved_review and instances:
            # Inherit the nearest preceding instance's authority so the
            # rescued id-with-quote groups with, and is graded against, that
            # authority (Bluebook: an id. refers back to the preceding cite).
            _prev_inst = instances[-1]
            name = _prev_inst["name"] or name
            rc = _prev_inst["reporter_cite"] if rc is None else rc
            rkey = _prev_inst["_rkey"]
        instances.append({
            "name": name,
            "cite_text": _cite_text(text, c),
            "span_start": s0,
            "span_end": s1,
            "reporter_cite": rc,
            "pin_cite": pin_raw,
            "pincite": _pin_norm(pin_raw),
            "is_short_form": not isinstance(c, FullCaseCitation),
            "kind": type(c).__name__,
            "id_unresolved_review": id_unresolved_review,
            "_rkey": rkey,
        })

    # 2c groundwork (2026.07.14): a cite that appears only inside another
    # citation's "(quoting ...)" / "(citing ...)" parenthetical is marked as
    # a QUOTED/CITED SOURCE and linked to its parent instance, so the report
    # layer (Phase 3) can attach it under the parent instead of emitting a
    # standalone card (the Dallas Bank / Citation 16 shape). Detection is
    # structural: an unclosed "(quoting"/"(citing" opener before the
    # instance, with the parent = the nearest earlier instance whose span
    # ends at or before the opener.
    for k, inst in enumerate(instances):
        s0 = inst["span_start"]
        lo = max(0, s0 - 300)
        window = text[lo:s0]
        opener_m = None
        for mm in re.finditer(r"\((quoting|citing)\b", window, flags=re.I):
            if ")" not in window[mm.end():]:
                opener_m = mm
        if opener_m is None:
            continue
        opener = lo + opener_m.start()
        parent = None
        for j in range(k - 1, -1, -1):
            pj = instances[j]
            if pj["span_end"] <= opener and not pj.get("nested_parenthetical"):
                parent = j
                break
        if parent is None:
            continue
        inst["nested_parenthetical"] = opener_m.group(1).lower()
        inst["parent_span_start"] = instances[parent]["span_start"]

    # Fix 5 (Finding 3/C2-C3): Id-antecedent correction under Bluebook R4.1.
    # eyecite chains an Id. to the immediately preceding cite even when that
    # cite sits inside a "(quoting/citing ...)" parenthetical; sources cited
    # within explanatory parentheticals are ignored for id. purposes. When the
    # instance directly before an Id. is marked nested_parenthetical, re-point
    # the Id. to that parenthetical's PARENT authority (e.g., cards 180/185:
    # Marcus & Millichap, not the inner Valdez).
    _span2inst = {inst["span_start"]: inst for inst in instances}
    for k in range(1, len(instances)):
        inst = instances[k]
        if inst.get("kind") != "IdCitation":
            continue
        prev = instances[k - 1]
        if not prev.get("nested_parenthetical"):
            continue
        parent = _span2inst.get(prev.get("parent_span_start"))
        if parent is None:
            continue
        inst["name"] = parent["name"]
        inst["reporter_cite"] = parent["reporter_cite"]
        inst["_rkey"] = parent["_rkey"]
        inst["_id_antecedent_corrected"] = True

    # occurrence_index / occurrence_count per resolved authority (rebuilt after
    # fixes 4-5 may have re-pointed some instances' authority keys)
    per_resource_counts = {}
    for inst in instances:
        per_resource_counts[inst["_rkey"]] = per_resource_counts.get(inst["_rkey"], 0) + 1
    seen: dict = {}
    for inst in instances:
        k = inst.pop("_rkey")
        seen[k] = seen.get(k, 0) + 1
        inst["occurrence_index"] = seen[k]
        inst["occurrence_count"] = per_resource_counts.get(k, 1)

    # Non-case references (informational): eyecite law/journal cites plus
    # generic statute/rule shapes.
    non_case: List[dict] = []
    seen_nc: set = set()
    for c in cites:
        if isinstance(c, (FullLawCitation, FullJournalCitation)):
            nm = c.matched_text().strip()
            if nm.lower() not in seen_nc:
                seen_nc.add(nm.lower())
                non_case.append({"name": nm, "type": "statute" if isinstance(c, FullLawCitation) else "journal"})
    for rx, label in ((_STATUTE_RE, "statute"), (_RULE_RE, "procedural rule")):
        for m in rx.finditer(text):
            nm = m.group(0).strip().rstrip(".,;")
            if nm.lower() not in seen_nc:
                seen_nc.add(nm.lower())
                non_case.append({"name": nm, "type": label})

    return {
        "instances": instances,
        "non_case": non_case,
        "jurisdiction": _jurisdiction_guess(court_ids),
    }
