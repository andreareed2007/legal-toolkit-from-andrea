"""cc_application.py -- application-sentence detector (2026.08.04).

Locked design: `2026.08.03 Handoff - Application-Sentence Build (Design
Locked).md`. An APPLICATION sentence applies the cited rule to the instant
facts ("Because no defendant has liability... Acme's civil conspiracy
theory fails as a matter of law") -- Isaacus verify() scores it against the
opinion text directly, so the score is structurally low even when the
citation is sound. This module detects that sentence form so the verdict
layer (cc_severity / cite_check_report) can re-render the outcome:

  * detector fires AND the same authority carries a verified rule instance
    elsewhere in the brief -> "APPLIED RULE -- VERIFIED ELSEWHERE" (Tier 4
    REVIEW, never PASS);
  * detector fires, NO verified sibling -> verdict stays DOES NOT SUPPORT
    with an additive note;
  * authority-characterization sentences ("reliance on X is misplaced") ->
    "CITED TO DISTINGUISH" (Tier 4 REVIEW), also only on low outcomes.

Hard limits (locked):
  * runs on the DE-CITED proposition text only;
  * kinds `parenthetical` / `citing_parenthetical` are EXEMPT (they describe
    the cited case, so role nouns false-positive there);
  * SUPPORTED outcomes are never touched; re-render engages only where the
    machine verdict would land does-not-support or weak-REVIEW;
  * "as a matter of law" alone NEVER fires;
  * no hardcoded case or party names anywhere (prime directive) -- the
    roster is harvested per-brief from the caption, defined aliases, and
    the TOA (for the party/authority name-collision downweight).

Pure stdlib. Gate: cc_application_gate.py (offline dev-set replay).
"""

from __future__ import annotations

import re
from typing import List, Optional

EXEMPT_KINDS = {"parenthetical", "citing_parenthetical"}

# ---------------------------------------------------------------------------
# Roster builder
# ---------------------------------------------------------------------------
# Corporate/entity furniture stripped from caption party names to get the
# distinctive head ("THE ACME INVESTMENT TRUST" -> "Acme").
_NAME_STOP = {
    "the", "of", "and", "an", "a",
    "investment", "investments", "trust", "trustee", "llc", "l.l.c.", "lp",
    "l.p.", "llp", "l.l.p.", "pllc", "inc", "inc.", "corp", "corp.",
    "corporation", "co", "co.", "company", "companies", "ltd", "ltd.",
    "limited", "partners", "partnership", "holdings", "holdco", "group",
    "fund", "funds", "capital", "management", "advisors", "n.a.", "na",
    "d/b/a", "dba", "et", "al", "al.", "jr", "jr.", "sr", "sr.",
}

_ROLE_WORDS = (
    "Plaintiff|Defendant|Claimant|Respondent|Movant|Appellant|Appellee|"
    "Petitioner|Relator|Intervenor|Debtor"
)

# Caption designation line: "..., Claimant," / "Defendants." / "Cross-Appellees,"
_CAPTION_ROLE_RE = re.compile(
    r"(?:Counter-|Cross-|Third-Party\s+)?(?:%s)s?\b" % _ROLE_WORDS)

# Defined alias: (the "GC"), ("Settlement Note"), (collectively, "Lawyers")
_ALIAS_RE = re.compile(
    r"\(\s*(?:hereinafter,?\s*)?(?:collectively,?\s*)?(?:together,?\s*)?"
    r"(?:the\s+|referred to as\s+)?"
    r"[“\"‘']([A-Z][A-Za-z0-9 .,&'’-]{1,40}?)[”\"’']"
    r"\s*\)")

_CAPTION_WINDOW = 6000    # caption lives in the head of the brief
_MAX_ALIAS_WORDS = 4


def _clean_party_head(name: str) -> str:
    """Distinctive head of a caption party name, entity furniture stripped."""
    toks = [t for t in re.split(r"[\s,]+", name.strip()) if t]
    # Strip leading articles and trailing entity furniture.
    while toks and toks[0].lower().strip(".,") in ("the", "in", "re"):
        toks.pop(0)
    while toks and toks[-1].lower().strip(".,") in _NAME_STOP:
        toks.pop()
    head = " ".join(toks).strip(" ,.")
    return head


def harvest_roster(brief_text: str, toa_index: Optional[dict] = None) -> dict:
    """Build the instant-case actor roster for one brief.

    Returns {"names": [...], "downweighted": [...]}. `names` feeds Tier 1;
    `downweighted` holds roster names that also appear inside a TOA authority
    caption (the party/authority name-collision trap) -- they are recorded
    but never fire.
    """
    names: List[str] = []
    head = brief_text[:_CAPTION_WINDOW]
    # Caption parties: for each role designation in the head, take the
    # nearest preceding capitalized name block on the same/preceding lines.
    for m in _CAPTION_ROLE_RE.finditer(head):
        back = head[max(0, m.start() - 300):m.start()]
        # Kill connector furniture between party name and designation.
        back = re.sub(r"(?:\bv\.?s?\.\b|\bagainst\b).*$", "", back,
                      flags=re.IGNORECASE | re.DOTALL)
        # Take trailing run of name-ish tokens (caps-led words, commas, &).
        nm = re.search(
            r"((?:[A-Z][\w.'’&-]*|of|the|and|d/b/a|et|al\.?|,|&)"
            r"(?:\s+(?:[A-Z][\w.'’&-]*|of|the|and|d/b/a|et|al\.?|,|&))*)"
            r"[\s,]*$", back)
        if not nm:
            continue
        for piece in re.split(r",| and | & ", nm.group(1)):
            h = _clean_party_head(piece)
            if len(h) >= 3 and not _CAPTION_ROLE_RE.fullmatch(h):
                names.append(h)
    # Defined aliases, brief-wide.
    for m in _ALIAS_RE.finditer(brief_text):
        alias = m.group(1).strip(" ,.")
        if not alias or len(alias) < 3:
            continue
        if len(alias.split()) > _MAX_ALIAS_WORDS:
            continue
        if _CAPTION_ROLE_RE.fullmatch(alias):
            continue          # "(the "Respondents")" -- role nouns fire on their own
        names.append(alias)
    # Dedupe (case-insensitive), keep first spelling. Drop blobs a caption
    # harvest can produce from headings (never a usable actor reference).
    seen, uniq = set(), []
    for n in names:
        k = n.lower()
        if len(n) > 40 or len(n.split()) > 4:
            continue
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    # Downweight: a roster name that also appears inside a cited authority's
    # caption (TOA) is ambiguous between party and authority -- drop it from
    # the firing set (build the roster from the caption; DOWNWEIGHT roster
    # names that also appear in TOA authority names).
    toa_names = " || ".join(
        (e.get("name") or "") for e in (toa_index or {}).values())
    keep, down = _split_collisions(uniq, toa_names)
    return {"names": keep, "downweighted": down}


def _split_collisions(names, authority_blob):
    """Word-boundary collision split: a roster name that appears inside a
    cited-authority caption is ambiguous between party and authority and
    must not fire ("H-Corp" the party vs In re H-Corp Holdings
    Mgmt.). Word-bounded so "Rand" never collides with "Randall"."""
    keep, down = [], []
    for n in names:
        if authority_blob and re.search(
                r"\b%s\b" % re.escape(n), authority_blob, re.IGNORECASE):
            down.append(n)
        else:
            keep.append(n)
    return keep, down


# ---------------------------------------------------------------------------
# Tier 1 -- instant-case actor references
# ---------------------------------------------------------------------------
# Party-role nouns, CAPITALIZED forms only (a lowercase "appellee" inside a
# quoted passage refers to the CITED case's party, not ours).
_ROLE_RE = re.compile(
    r"\b(?:Counter-|Cross-|Third-Party\s+)?(?:%s)s?(?:['’]s?)?(?=\W|$)"
    % _ROLE_WORDS)
_GENERIC_DETS = {"a", "an", "any", "each", "every", "no", "some", "another"}

# Instant-proceeding artifacts. "this court"-family is case-insensitive;
# document artifacts require the capitalized defined-term form ("the Motion")
# so rule prose about "the motion to dismiss standard" cannot fire.
_ARTIFACT_CI_RE = re.compile(
    r"\bthis\s+Court\b|\bthis\s+proceeding\b|\bthe\s+arbitrator\b",
    re.IGNORECASE)
_ARTIFACT_CS_RE = re.compile(
    r"\b[Tt]he\s+(?:Panel|Motion|Petition|FAC|Complaint|Application)\b"
    r"|\bCount\s+(?:\d+|[IVXL]+)\b")


def _role_hits(text: str) -> List[str]:
    hits = []
    for m in _ROLE_RE.finditer(text):
        before = text[:m.start()].rstrip()
        prev = before.rsplit(None, 1)[-1].lower().strip("\"'“‘([") \
            if before else ""
        if prev in _GENERIC_DETS:
            continue                      # "a plaintiff must show" is a rule
        tail = text[m.end():]
        if re.match(r"\s+in\s+[A-Z]", tail):
            continue                      # "the plaintiff in Smith" = cited case
        hits.append(m.group(0))
    return hits


def _roster_hits(text: str, roster_names: List[str]) -> List[str]:
    hits = []
    for n in roster_names or []:
        pat = re.compile(r"\b%s(?:['’]s?)?\b" % re.escape(n))
        if pat.search(text):
            hits.append(n)
    return hits


# ---------------------------------------------------------------------------
# Tier 2 -- conclusion/application markers (four families)
# ---------------------------------------------------------------------------
_LOCATIVE_RE = re.compile(
    r"\bhere\b|\bin\s+this\s+case\b|\bin\s+the\s+present\s+case\b"
    r"|\bin\s+this\s+instance\b|\bon\s+this\s+record\b|\bon\s+these\s+facts\b"
    r"|\bin\s+the\s+case\s+at\s+bar\b", re.IGNORECASE)

# Sentence-initial consequence openers (leading Id./quote furniture allowed).
_LEAD_FURNITURE_RE = re.compile(
    r"^(?:\s|[\"'“‘\[\(]|Id\.(?:\s+at\s+\S+)?[,;]?\s)*")
_CONSEQ_RE = re.compile(
    r"(?:Thus|Therefore|Accordingly|Consequently|As\s+a\s+result"
    r"|It\s+follows|For\s+(?:these|the\s+foregoing)\s+reasons"
    r"|In\s+sum|In\s+short)\b")

# Disposition predicates. "As a matter of law" alone NEVER fires -- it only
# rides as an optional tail here.
_DISP_RE = re.compile(
    r"\bfail(?:s|ed)?(?:\s+as\s+a\s+matter\s+of\s+law)?"
    r"(?:\s+to\s+(?:establish|show|state|support|raise|produce|present|plead|allege))?\b"
    r"|\b(?:cannot|can\s+not)\s+(?:survive|establish|show|state)\b"
    r"|\bnor\s+can\b(?=.{0,80}\b(?:establish|survive|show)\b)"
    r"|\bmust\s+be\s+dismissed\b"
    r"|\b(?:is|are)\s+(?:time-?\s*)?barred\b"
    r"|\b(?:is|are)\s+entitled\s+to\s+(?:traditional\s+|no-evidence\s+)?summary\s+judgment\b"
    r"|\bshould\s+be\s+(?:granted|denied)\b"
    r"|\b(?:has|have)\s+not\s+(?:shown|established|raised|produced|met|carried)\b"
    r"|\bdid\s+not\b"
    r"|\bwaived\b"
    r"|\blacks?\s+standing\b"
    r"|\b(?:is|are)\s+required\s+to\b"
    r"|\bwarrants?\b",
    re.IGNORECASE)

# Guards for the no-Tier-1 fire path. (1) A sentence shaped like a
# description of the CITED case ("In Smith, ..." / "the court held/found
# ...") must not fire on markers alone -- corpus-verified false-positive
# class. (2) A generically-determined subject ("any claimant who fails ...
# cannot survive") is a RULE even when two marker families co-occur.
_GENERIC_SUBJECT_RE = re.compile(
    r"\b(?:a|an|any|each|every|no|one)\s+(?:part(?:y|ies)|person|court"
    r"|claimant|plaintiff|defendant|movant|litigant|debtor|stakeholder"
    r"|respondent|appellant|appellee|petitioner)\b", re.IGNORECASE)
_CITED_CASE_DESC_RE = re.compile(
    r"^In\s+[A-Z][\w.'’-]+\s*,"
    r"|\b[Tt]he\s+court\b[^.;]{0,60}?\b(?:held|found|granted|denied|affirmed"
    r"|reversed|concluded|rejected|explained|reasoned|noted|upheld)\b"
    r"|\b(?:court|Circuit|Court)\b[^.;]{0,40}?\b(?:held|found|affirmed"
    r"|reversed|rejected|upheld)\b")


def _tier2_families(text: str) -> List[str]:
    fams = []
    if _LOCATIVE_RE.search(text):
        fams.append("locative")
    lead = _LEAD_FURNITURE_RE.match(text)
    body_start = lead.end() if lead else 0
    if _CONSEQ_RE.match(text, body_start):
        fams.append("consequence")
    disp = _DISP_RE.search(text)
    if disp:
        fams.append("disposition")
        if re.match(r"Because\b", text[body_start:]):
            fams.append("causal")        # "Because [facts], [conclusion]"
    return fams


# ---------------------------------------------------------------------------
# Characterization subclass ("CITED TO DISTINGUISH")
# ---------------------------------------------------------------------------
_CHAR_RE = re.compile(
    r"\bmisplaced\b|\binapposite\b|\bdistinguishable\b|\bdoes\s+not\s+help\b"
    r"|\bis\s+not\s+to\s+the\s+contrary\b"
    r"|\breliance\s+on\b.{0,60}?\bis\s+(?:misplaced|misleading|misguided|unavailing)\b",
    re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
def detect(proposition: str, roster: Optional[dict] = None,
           kind: str = "host") -> dict:
    """Classify one de-cited proposition.

    Returns {"fired", "characterization", "tier1", "tier2", "exempt"}.
    Fire rule (locked): application = (>=1 Tier-1 hit AND >=1 Tier-2 hit)
    OR (>=2 Tier-2 hits from different families). Never on exempt kinds.
    """
    out = {"fired": False, "characterization": False,
           "tier1": [], "tier2": [], "exempt": False}
    text = (proposition or "").strip()
    if not text:
        return out
    if kind in EXEMPT_KINDS:
        out["exempt"] = True
        return out
    names = (roster or {}).get("names") or []
    t1 = _roster_hits(text, names) + _role_hits(text)
    t1 += [m.group(0) for m in _ARTIFACT_CI_RE.finditer(text)]
    t1 += [m.group(0) for m in _ARTIFACT_CS_RE.finditer(text)]
    t2 = _tier2_families(text)
    out["tier1"] = t1
    out["tier2"] = t2
    if t1 and t2:
        out["fired"] = True
    elif (len(set(t2)) >= 2 and not _CITED_CASE_DESC_RE.search(text)
            and not _GENERIC_SUBJECT_RE.search(text)):
        out["fired"] = True
    if _CHAR_RE.search(text):
        out["characterization"] = True
    return out


# ---------------------------------------------------------------------------
# Verified-sibling cross-check + result attachment
# ---------------------------------------------------------------------------
def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _authority_key(r) -> str:
    """Group instances of the same resolved authority. Primary: resolved
    opinion URL (id-chain members inherit it). Fallback: normalized name +
    volume/reporter/page."""
    url = (getattr(r, "opinion_url", "") or "").strip().rstrip("/")
    if url:
        return "url::" + url
    rc = getattr(r.citation, "reporter_cite", None) or {}
    if rc.get("volume") and rc.get("reporter"):
        return "cite::%s %s %s" % (rc.get("volume"), rc.get("reporter"),
                                   rc.get("page"))
    return "name::" + _norm_name(getattr(r.citation, "name", ""))


def _sibling_verified(r) -> bool:
    """Support outcomes that qualify a sibling as a VERIFIED rule instance:
    supported (score >= 0.8), supported (verbatim quote), or supported
    (agent-verified Step 6.6 override)."""
    if getattr(r, "verification_override", False):
        return True
    if not getattr(r, "opinion_resolved", False):
        return False
    if getattr(r, "name_cite_ok", None) is False:
        return False
    thin = 0 < getattr(r, "opinion_chars", 0) < 400
    if getattr(r, "quote_matched", False) and not thin:
        return True
    return bool(getattr(r, "supports", False)) and r.score >= 0.8 \
        and r.inextractability_score < 0.7


def attach(results, roster: Optional[dict] = None) -> None:
    """Run the detector over every result and attach findings in place.

    Sets, per result: r.application (detector dict) and, where the same
    authority has a verified rule instance elsewhere, r.applied_rule_sibling
    = {"cit_num", "cite_text", "pincite", "same_pincite"}. Idempotent --
    safe to re-run after Step 6.6 overrides land."""
    # Per-run downweight against the DETECTED authority names too -- a brief
    # with no parseable TOA (working drafts) still gets the party/authority
    # collision guard.
    auth_blob = " || ".join(
        (getattr(r.citation, "name", "") or "") for r in results)
    keep, _down = _split_collisions((roster or {}).get("names") or [],
                                    auth_blob)
    roster = {"names": keep,
              "downweighted": ((roster or {}).get("downweighted") or [])
              + _down}
    verified_by_key = {}
    for i, r in enumerate(results):
        if _sibling_verified(r):
            verified_by_key.setdefault(_authority_key(r), []).append(i)
    for i, r in enumerate(results):
        kind = getattr(r.citation, "prop_kind", "host") or "host"
        det = detect(getattr(r.citation, "proposition", "") or "",
                     roster=roster, kind=kind)
        r.application = det
        r.applied_rule_sibling = None
        if not (det["fired"] or det["characterization"]):
            continue
        sibs = [j for j in verified_by_key.get(_authority_key(r), [])
                if j != i]
        if not sibs:
            continue
        j = sibs[0]
        sib = results[j]
        pin_a = (getattr(r.citation, "pin_cite", "") or
                 getattr(r.citation, "pincite", "") or "").strip()
        pin_b = (getattr(sib.citation, "pin_cite", "") or
                 getattr(sib.citation, "pincite", "") or "").strip()
        r.applied_rule_sibling = {
            "cit_num": j + 1,
            "cite_text": getattr(sib.citation, "cite_text", "") or
                         getattr(sib.citation, "name", ""),
            "pincite": pin_b,
            "same_pincite": bool(pin_a and pin_b and
                                 _norm_name(pin_a) == _norm_name(pin_b)),
        }


def rerender_key(r, quote_matched: bool, thin: bool,
                 unavail: bool) -> Optional[str]:
    """Single source of re-render truth for cc_severity.check_support AND
    cite_check_report._verdict. Returns "applied_rule", "distinguish", or
    None. Engages ONLY where the machine verdict would land does-not-support
    or the weak-REVIEW branch; SUPPORTED outcomes are never touched."""
    det = getattr(r, "application", None)
    if not det or quote_matched or thin or unavail:
        return None
    dns_bound = r.inextractability_score >= 0.7
    weak_bound = (r.inextractability_score >= 0.5 or r.score < 0.3)
    if not (dns_bound or weak_bound):
        return None
    if det.get("characterization"):
        return "distinguish"
    if det.get("fired") and getattr(r, "applied_rule_sibling", None):
        return "applied_rule"
    return None


def application_note(r) -> str:
    """Additive note for a DNS card whose sentence fired as application but
    has no verified sibling (bad cites are never masked)."""
    det = getattr(r, "application", None)
    if det and det.get("fired") and not getattr(r, "applied_rule_sibling",
                                                None):
        return (" Note: the sentence reads as an application of the cited "
                "rule to the facts of this case, a form the support "
                "classifier scores low by construction; no other instance "
                "of this authority verified the underlying rule, so the "
                "verdict stands. Review the cite by hand.")
    return ""
