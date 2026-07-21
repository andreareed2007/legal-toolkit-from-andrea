"""cc_severity.py -- Four-check severity model (Phase 3, 2026.07.14).

Every citation runs four independent checks -- Identity, Quote, Support,
Treatment -- and the card's tier is the WORST failing check (parent handoff
SS2a, locked by author 2026.07.14). Each check row is computed from RESULT
PRIMITIVES (G8): name_cite_ok, quote_results/quote_fabricated,
score/supports/inextractability, pincite_given/found, and the goodlaw
treatment class. The folded 11-verdict key from cite_check_report._verdict
survives only as a legacy label; it never feeds these rows.

Tier vocabulary (parent handoff SS2a):
  TIER 1 CRITICAL -- sanction risk (fabricated quote; nonexistent/wrong
                     case; cited as support when the opinion holds the
                     opposite, per an agent finding -- never a raw score).
  TIER 2 DEFECT   -- fix before filing (does not support; material
                     misquote; negative treatment signal).
  TIER 3 REVIEW   -- human should confirm (somewhat; page not found;
                     identity unconfirmed; treatment caution; unable).
  TIER 4 PASS     -- verified.

Design contracts honored here:
  B1  quote_fabricated forces TIER 1 (worst-check-governs makes it
      automatic); the G2 full-text recheck upstream is the false-positive
      guard.
  G5  the pre-existing benign adverse-signal case ("but see" in the brief)
      is PASS/informational -- never Tier 1.
  G6  Tier-1 contrary fires ONLY on verification_finding ==
      "confirmed_contrary" from the Step 6.6 agent loop (locked Option 1).
  G7  lookup-404 + zero case-name hits renders "possible nonexistent
      authority" -- an elevated, red-styled REVIEW distinct from the
      ordinary coverage-gap "unable".
"""
from __future__ import annotations

import re

# Phase 8 (author 2026.07.15): five tiers. UNVERIFIED is its own bucket
# -- authorities the tool could not check at all (not found in any free
# database, wrong document retrieved, identity unconfirmable). Ranked
# ABOVE Defect (a case nobody checked outranks a weak-support flag) and
# colored its own purple, never amber or orange.
TIER_CRITICAL, TIER_UNVERIFIED, TIER_DEFECT, TIER_REVIEW, TIER_PASS = \
    1, 2, 3, 4, 5

TIER_LABEL = {1: "TIER 1 · CRITICAL", 2: "TIER 2 · UNVERIFIED",
              3: "TIER 3 · FIX", 4: "TIER 4 · REVIEW",
              5: "TIER 5 · PASS"}
TIER_SHORT = {1: "CRITICAL", 2: "UNVERIFIED", 3: "FIX",
              4: "REVIEW", 5: "PASS"}
TIER_SUB = {1: "sanction risk", 2: "could not check — verify on "
            "Westlaw or Lexis", 3: "fix before filing",
            4: "confirm by hand", 5: "verified"}
TIER_HEX = {1: "#B00020", 2: "#534AB7", 3: "#E8870E",
            4: "#F5C518", 5: "#0FA685"}
TIER_BG = {1: "#FBEAEC", 2: "#EEEDFE", 3: "#FDF1E2",
           4: "#FBF6E3", 5: "#E6F4EF"}
TIER_ICON = {1: "⛔", 2: "◆", 3: "▲", 4: "●", 5: ""}

# Chip color classes for the HTML renderer: t1-t4 map to the tier colors,
# "na" is the neutral informational chip.
CHIP_NA = "na"

# Thin-text guard threshold (mirrors cite_check_report.THIN_OPINION_CHARS;
# kept local so the dependency stays one-way: report -> severity).
THIN_OPINION_CHARS = 400


def _row(axis, status, tier, chip, chip_cls, text, short=""):
    return {"axis": axis, "status": status, "tier": tier, "chip": chip,
            "chip_cls": chip_cls, "text": text,
            "short": short or (text.split(". ")[0].rstrip(".") if text else "")}


def tidy_passage(p, limit=300):
    """M1: never cut a passage mid-word; close with an ellipsis when cut."""
    p = (p or "").strip()
    if len(p) <= limit:
        return p
    cut = p[:limit]
    sp = cut.rfind(" ")
    if sp > limit // 2:
        cut = cut[:sp]
    return cut.rstrip(" ,;") + "…"


def _source_unpaginated(r):
    note = (getattr(r, "pincite_note", "") or "").lower()
    return ("could not be checked" in note or "no pagination" in note
            or "no reporter pagination" in note)


def _identity_unconfirmed(r):
    """True when the opinion resolved but the name/cite identity gate could
    not confirm it is the cited case (REVIEW class, not a positive wrong-case
    finding). A fabrication or does-not-support finding requires confidence the
    opinion IS the cited case; when identity is unconfirmed, the Quote and
    Support checks must not escalate above the Identity row's REVIEW -- a
    missing quote or weak support against the WRONG opinion is not attributable
    to the brief (2026.07.14, QA-Brief Cit 18: 116 F.4th 422 mis-resolved to a
    2019 record; the quote absence there is meaningless)."""
    return (getattr(r, "opinion_resolved", False)
            and getattr(r, "name_cite_ok", None) is False
            and (getattr(r, "verification_finding", "") or "")
            != "confirmed_wrong_case"
            and not getattr(r, "verification_override", False))


def _wrong_document(r):
    """True when Step 6.6 confirmed the resolved opinion is a DIFFERENT
    document (genuine wrong case or CL-coverage wrong document). Quote and
    Support findings computed against the wrong document are meaningless
    and must never escalate the card (Phase 7, QA-Brief cit 18 -- the quote
    absence from a 2019 Delaware filing says nothing about the brief)."""
    return ((getattr(r, "verification_finding", "") or "")
            == "confirmed_wrong_case")


def _reporter_style_cite(r):
    """The citation is a reporter/opinion cite (e.g. '116 F.4th 422'), not an
    inherently docket-style reference."""
    if getattr(r.citation, "reporter_cite", None):
        return True
    return (getattr(r.citation, "type", "") or "") == "decision"


def _recap_wrong_document(r):
    """A reporter/opinion cite that resolved to a RECAP/PACER DOCKET record (a
    non-opinion document) where the cited reporter address is absent from
    CourtListener's citation-lookup index (status 404) or the pincite could
    not be located on reporter pagination -- the resolver landed on the wrong
    document. Findings scored against such a record are meaningless; the
    Support axis must render UNABLE, never DOES NOT SUPPORT (author
    2026.07.15, QA-Brief cit 18: 116 F.4th 422 resolved to a bankruptcy
    docket ENTRY, then the tool scored 'does not support' against it)."""
    if not getattr(r, "opinion_resolved", False):
        return False
    src = (getattr(r, "opinion_source", "") or "").lower()
    if "recap" not in src and "pacer" not in src:
        return False
    if not _reporter_style_cite(r):
        return False
    return (getattr(r, "lookup_status", None) == 404
            or getattr(r, "pincite_found", None) is False)


def _bracket_region_flags(quote):
    """For each whitespace-split token of ``quote``, True when the token lies
    inside a [...] bracket span (a Bluebook alteration), tracking bracket
    depth so every interior token of a multi-word substitution like
    '[the judgment debtor]' counts -- not only the tokens carrying a literal
    bracket char. Fixes the QA-Brief cit 16 miscount where the bracket-less
    interior word 'judgment' was treated as an unbracketed change."""
    flags, depth = [], 0
    for tok in (quote or "").split():
        in_region = depth > 0 or "[" in tok or "]" in tok
        flags.append(in_region)
        depth += tok.count("[") - tok.count("]")
        if depth < 0:
            depth = 0
    return flags


def _fab_confirmed(q):
    """Phase 7 backward-compat default: cards pickled before the gate
    carry no 'confirmed' key -- fall back to full_text_checked."""
    return q.get("confirmed", q.get("full_text_checked"))


# ---------------------------------------------------------------------------
# Check 1 -- Existence & Identity
# ---------------------------------------------------------------------------
def check_identity(r):
    if not r.opinion_resolved:
        # G7: reporter cite known to the lookup index shape but 404, AND the
        # case-name search returned zero hits -- the classic hallucinated-
        # authority signature. Brazda-class records (real, published, cite
        # not indexed) resolve via the 404 name walk upstream, so they do
        # not reach this branch.
        if (getattr(r, "lookup_status", None) == 404
                and "No results found." in (getattr(r, "search_detail", "") or "")):
            return _row(
                "Identity", "possible_nonexistent", TIER_UNVERIFIED,
                "POSSIBLE NONEXISTENT AUTHORITY", "t1",
                "The reporter citation is not in the citation-lookup index "
                "(status 404) and a case-name search returned no hits. "
                "Possible nonexistent authority — verify on Westlaw or "
                "Lexis before filing.",
                short="possible nonexistent authority — verify on "
                      "Westlaw or Lexis")
        return _row("Identity", "not_found", None, "NOT FOUND", CHIP_NA,
                    "Opinion not located in free databases; identity could "
                    "not be assessed. See the Support row.",
                    short="opinion not located")
    if (getattr(r, "verification_finding", "") or "") == "confirmed_wrong_case":
        if getattr(r, "lookup_status", None) == 404:
            # Phase 7 (author 2026.07.15, QA-Brief cit 18): the cited reporter
            # address is NOT in CourtListener's citation-lookup index, so
            # the resolver landing on a different document is a database
            # COVERAGE artifact, not a positive wrong-case finding against
            # the brief. Evidence-based (the 404 is CL's own index status),
            # never a guess. Renders REVIEW, not CRITICAL.
            return _row(
                "Identity", "wrong_doc_coverage", TIER_UNVERIFIED,
                "WRONG DOCUMENT — COVERAGE GAP", "tu",
                "The retrieved document is not the cited case, but the "
                "cited reporter address is not in CourtListener's "
                "citation-lookup index (status 404) — the correct "
                "opinion is likely outside the free databases and the "
                "resolver landed on the wrong record. A coverage "
                "artifact, not necessarily a brief defect. Verify the "
                "citation on Westlaw or Lexis.",
                short="wrong document retrieved — CourtListener "
                      "coverage gap; verify on Westlaw or Lexis")
        return _row("Identity", "wrong_case", TIER_CRITICAL, "WRONG CASE",
                    "t1",
                    "Manual verification (Step 6.6) confirmed the resolved "
                    "opinion is a DIFFERENT case than the one the brief "
                    "cites.",
                    short="wrong case — resolves to a different case")
    if _recap_wrong_document(r):
        return _row(
            "Identity", "wrong_doc_coverage", TIER_UNVERIFIED,
            "WRONG DOCUMENT — COVERAGE GAP", "tu",
            "The reporter citation resolved to a docket record (RECAP/PACER), "
            "not the cited opinion, and the reporter address is not in "
            "CourtListener's citation-lookup index. The correct opinion is "
            "likely outside the free databases and the resolver landed on the "
            "wrong record — a coverage artifact, not necessarily a brief "
            "defect. Verify the citation on Westlaw or Lexis.",
            short="wrong document retrieved — coverage gap; verify on "
                  "Westlaw or Lexis")
    if getattr(r, "name_cite_ok", None) is False:
        return _row("Identity", "unconfirmed", TIER_UNVERIFIED,
                    "UNCONFIRMED", "tu",
                    "Could not confirm the resolved opinion is the case the "
                    "brief names (name/cite identity gate). Often a "
                    "resolution artifact — verify the citation points "
                    "to the intended authority.",
                    short="identity unconfirmed")
    return _row("Identity", "confirmed", TIER_PASS, "PASS", "t5",
                "Correct case.", short="correct case")


# ---------------------------------------------------------------------------
# Check 2 -- Quote fidelity
# ---------------------------------------------------------------------------
def check_quote(r):
    qrs = getattr(r, "quote_results", None) or []
    prop = (getattr(r.citation, "proposition", "") or "")
    has_quote_marks = any(ch in prop for ch in ('"', "“", "”"))
    if not r.opinion_resolved:
        if qrs or has_quote_marks:
            return _row("Quote", "unchecked", None, "UNCHECKED", CHIP_NA,
                        "Opinion not retrieved; the quoted language could "
                        "not be checked.", short="quote unchecked")
        return _row("Quote", "no_quote", None, "NO QUOTE", CHIP_NA,
                    "No quotation marks in the citing sentence; nothing to "
                    "check.", short="no quote")
    # Agent-located quotation (Step 6.6): when the must-verify loop confirmed
    # the quoted language verbatim on the fetched full opinion, the quote row
    # reflects that regardless of what the machine pass found on a trimmed
    # copy -- QA-Brief as-filed Cit 19 (the "hammer/nail" quotation) that the
    # stale 25-character branch wrongly reported as nothing-to-check.
    if getattr(r, "verification_override", False) and \
            (getattr(r, "verification_finding", "") or "") == "confirmed_supports":
        return _row("Quote", "verified_agent", TIER_PASS, "VERIFIED", "t5",
                    "Quoted language located verbatim in the opinion by "
                    "manual verification (Step 6.6).",
                    short="quote verified (agent-located)")
    if not qrs:
        # Every direct quotation must be checked (author, 2026.07.15) -- the
        # 25-character floor is retired. An empty quote_results here means no
        # quoted span was machine-locatable in the citing sentence; if
        # quotation marks are present, route to a hand check rather than
        # declaring there is nothing to check.
        if has_quote_marks:
            return _row("Quote", "present_unlocated", TIER_REVIEW,
                        "VERIFY BY HAND", "t4",
                        "A quotation is present in the citing sentence but "
                        "the tool could not isolate the quoted span to check "
                        "it automatically — verify it against the opinion "
                        "by hand.",
                        short="quote present — verify by hand")
        return _row("Quote", "no_quote", None, "NO QUOTE", CHIP_NA,
                    "No quotation in the citing sentence; nothing to check.",
                    short="no quote")
    if (_identity_unconfirmed(r) or _wrong_document(r)
            or _recap_wrong_document(r)) and any(
            q.get("result") in ("FABRICATED", "CLOSE") for q in qrs):
        return _row("Quote", "unchecked_identity", None, "UNCHECKED", CHIP_NA,
                    "The quoted language could not be checked against the "
                    "cited case — the resolved opinion's identity is "
                    "unconfirmed or it is a different document. See the "
                    "Identity row.",
                    short="quote unchecked (wrong or unconfirmed "
                          "document)")
    fabs = [q for q in qrs if q.get("result") == "FABRICATED"]
    fabs_c = [q for q in fabs if _fab_confirmed(q)]
    fabs_u = [q for q in fabs if not _fab_confirmed(q)]
    if fabs_c:
        # Phase 7 (author 2026.07.15): EVERY confirmed-absent quotation is
        # Critical, short spans included -- a fabricated two-word quote is
        # still a fabrication.
        f = fabs_c[0]
        if f.get("short"):
            txt = ("A short quoted phrase (“%s”) is not in the opinion — "
                   "confirmed absent from the complete opinion text. A "
                   "fabricated quotation is Critical regardless of "
                   "length." % tidy_passage(f.get("quote", ""), 60))
        else:
            txt = ("Quoted words not located in the opinion — possible "
                   "paraphrase presented as quotation. Absence of quoted "
                   "words is always Critical, even where the case is "
                   "thematically consistent. Confirmed against the "
                   "complete opinion text before flagging.")
            if len(qrs) > 1:
                txt += (" Affected quote: “%s…”"
                        % tidy_passage(f.get("quote", ""), 120))
        return _row("Quote", "fabricated", TIER_CRITICAL,
                    "ABSENT — FABRICATED", "t1", txt,
                    short="fabricated quotation — quoted language does "
                          "not appear in the opinion")
    if fabs_u:
        # Phase 7 confirmation gate: no full text, no fabrication. The
        # tool never cries fabrication against text it did not fully see.
        if getattr(r, "verification_override", False):
            return _row("Quote", "verified_agent", TIER_PASS, "VERIFIED",
                        "t5",
                        "Manual verification (Step 6.6) located the "
                        "quotation on the fetched full opinion; the "
                        "partial-copy miss is superseded.",
                        short="quote verified (agent-located)")
        f = fabs_u[0]
        return _row("Quote", "unconfirmed_absence", TIER_REVIEW,
                    "NOT CONFIRMED", "t4",
                    "Quoted language (“%s…”) was not located in the "
                    "partial copy retrieved, and the complete opinion was "
                    "unavailable to confirm absence. Not graded as "
                    "fabricated — confirm the quotation against the full "
                    "opinion (routed to the must-verify loop)."
                    % tidy_passage(f.get("quote", ""), 80),
                    short="quote not confirmed — full opinion "
                          "unavailable")
    closes = [q for q in qrs if q.get("result") == "CLOSE"]
    if closes:
        c = closes[0]
        # A word-faithful quotation whose only differences are Bluebook
        # bracket substitutions and/or ellipsis elisions is NOT a misquote --
        # but the substitutions replace meaning-bearing terms, so an attorney
        # must confirm each is a fair rendering of the source (author,
        # 2026.07.15: new "verify brackets" review flag). Bracket-dominated
        # changes only, so a genuine unbracketed word-swap still lands as a
        # material misquote below.
        # author 2026.07.15 (QA-Brief cits 16 & 10): brackets and CLEAN ellipsis
        # omissions are the ONLY permitted differences. ANY other alteration
        # -- an unbracketed word swap, OR a word dropped with no ellipsis --
        # is a material misquote (Tier 3). The matcher, when it has the full
        # opinion, sets clean_alterations directly (every literal segment
        # between brackets/ellipses appears in order); trust it when present.
        # Otherwise fall back to the word diff.
        _qtext = c.get("quote", "") or ""
        _n_brackets = _qtext.count("[")
        _n_ellipsis = _qtext.count("…") + _qtext.count("...")
        _has_ellipsis = _n_ellipsis > 0 or any(e in prop for e in ("…", "..."))
        _clean = c.get("clean_alterations")
        if _clean is None:
            _diff = c.get("diff") or {}
            _brief_pairs = _diff.get("brief", [])
            _op_pairs = _diff.get("opinion", [])
            _region = _bracket_region_flags(_qtext)
            # (a) any CHANGED brief word outside a [...] span is a genuine
            #     addition/substitution (QA-Brief cit 16: "to pay the judgment").
            _brief_alt = False
            for _i, _pair in enumerate(_brief_pairs):
                if not _pair[1]:
                    continue
                _tok = _pair[0]
                _in_bracket = ("[" in _tok or "]" in _tok
                               or (_i < len(_region) and _region[_i]))
                if not _in_bracket and any(_c.isalnum() for _c in _tok):
                    _brief_alt = True
                    break
            # (b) each opinion-side run MISSING from the brief must be
            #     explained by a bracket substitution or an ellipsis; more
            #     runs than (brackets + ellipses) means a word was dropped
            #     silently (QA-Brief cit 10: "ask for [an order of] interpleader"
            #     omitted with NO ellipsis).
            _op_runs, _prev = 0, False
            for _pair in _op_pairs:
                _fl = bool(_pair[1]) and any(_c.isalnum() for _c in _pair[0])
                if _fl and not _prev:
                    _op_runs += 1
                _prev = _fl
            _silent_omission = _op_runs > (_n_brackets + _n_ellipsis)
            _clean = (not _brief_alt) and (not _silent_omission)
        _bracket_driven = bool(_clean) and (_n_brackets > 0 or _has_ellipsis)
        _bracket_changed = ["["] * _n_brackets
        if _bracket_driven:
            _n = len(_bracket_changed)
            if _n:
                btxt = ("Quoted words track the opinion, but %d bracketed "
                        "substitution%s%s replace meaning-bearing terms — "
                        "confirm by hand that each substitution is a fair "
                        "rendering of the source before filing." % (
                            _n, "" if _n == 1 else "s",
                            " and an ellipsis omission"
                            if _has_ellipsis else ""))
            else:
                btxt = ("Quoted words track the opinion; the only differences "
                        "are ellipsis omissions — confirm by hand that the "
                        "omissions do not distort the source before filing.")
            brow = _row("Quote", "verify_brackets", TIER_REVIEW,
                        "VERIFY BRACKETS", "t4", btxt,
                        short="verify bracket substitutions — manual "
                              "check required")
            brow["diff"] = c.get("diff")
            brow["window"] = c.get("window", "")
            brow["must_verify"] = True
            return brow
        txt = ("Quoted language matches the opinion only approximately "
               "(similarity %.2f) — unbracketed alteration. "
               % (c.get("similarity") or 0.0))
        if not c.get("diff") and c.get("passage"):
            txt += "Closest passage: “%s”" % tidy_passage(
                c.get("passage", ""))
        row = _row("Quote", "misquote", TIER_DEFECT, "MISQUOTE", "t3",
                   txt.strip(),
                   short="material misquote — quoted words altered")
        # Phase 8: word-level diff for the renderers (brief additions /
        # opinion words missing from the brief, both red-bolded).
        row["diff"] = c.get("diff")
        row["window"] = c.get("window", "")
        return row
    if any(q.get("license_applied") for q in qrs):
        return _row("Quote", "verified_licensed", TIER_PASS, "VERIFIED",
                    "t5",
                    "Quote verified (alterations licensed by the brief's "
                    "signal parenthetical, e.g. “(cleaned up)”).",
                    short="quote verified (licensed alterations)")
    if any(q.get("alterations_only") for q in qrs):
        return _row("Quote", "verified_alterations", TIER_PASS, "VERIFIED",
                    "t5", "Quote verified (permitted alterations).",
                    short="quote verified (permitted alterations)")
    return _row("Quote", "verified", TIER_PASS, "VERIFIED", "t5",
                "Quote verified — matches the opinion.",
                short="quote verified")


# ---------------------------------------------------------------------------
# Check 3 -- Support
# ---------------------------------------------------------------------------
def check_support(r):
    if not r.opinion_resolved:
        return _row("Support", "unable", TIER_UNVERIFIED, "UNABLE",
                    "tu",
                    "Not found in the free databases searched — a "
                    "coverage gap, not necessarily an error. Use the card's "
                    "link to check on Westlaw or Lexis.",
                    short="unable to verify (coverage gap)")
    if not (getattr(r.citation, "proposition", "") or "").strip():
        return _row("Support", "no_proposition", TIER_REVIEW, "REVIEW",
                    "t4",
                    "No verifiable proposition extracted — review "
                    "required; nothing was scored.",
                    short="proposition not extracted")
    if _identity_unconfirmed(r):
        return _row("Support", "identity_unconfirmed", TIER_UNVERIFIED,
                    "UNCHECKED", "tu",
                    "Support was not scored — the resolved opinion's identity "
                    "is unconfirmed, so a match against it would not be "
                    "reliable. See the Identity row.",
                    short="support not scored — identity unconfirmed")
    if _recap_wrong_document(r):
        # author 2026.07.15 (QA-Brief cit 18): a reporter cite that
        # resolved to a RECAP/PACER docket record is a wrong document -- the
        # Support axis must say "could not locate the cited opinion", never
        # affirmatively classify support/DOES NOT SUPPORT against a random
        # docket filing.
        return _row("Support", "wrong_document_recap", TIER_UNVERIFIED,
                    "UNABLE", "tu",
                    "Could not locate the cited opinion — the reporter "
                    "citation resolved to a docket record (RECAP/PACER), not "
                    "the cited reporter opinion, and the cite is not in "
                    "CourtListener's index. Nothing was scored. Verify on "
                    "Westlaw or Lexis.",
                    short="could not locate the cited opinion — verify "
                          "on Westlaw or Lexis")
    if _wrong_document(r):
        # Phase 7 (QA-Brief cit 18): a support score computed against the
        # WRONG document is meaningless and must not escalate.
        return _row("Support", "wrong_document", TIER_UNVERIFIED,
                    "UNCHECKED", "tu",
                    "Support was not scored — the resolved opinion is a "
                    "different document than the cited case. See the "
                    "Identity row.",
                    short="support not scored — wrong document "
                          "resolved")
    if getattr(r, "quote_fabricated", False):
        return _row("Support", "superseded", None, "SUPERSEDED", CHIP_NA,
                    "Thematic support score (%.2f) is irrelevant — a "
                    "fabricated quotation controls the verdict." % r.score,
                    short="support superseded by fabricated quote")
    vf = (getattr(r, "verification_finding", "") or "").strip()
    if vf == "confirmed_contrary":
        txt = ("Cited as support, but manual verification (Step 6.6) found "
               "the opinion holds the OPPOSITE of what the brief claims.")
        note = (getattr(r, "verification_note", "") or "").strip()
        if note:
            txt += " " + note
        return _row("Support", "contrary", TIER_CRITICAL,
                    "CITED AS CONTRARY", "t1", txt,
                    short="cited as contrary — the opinion holds the "
                          "opposite")
    if getattr(r, "verification_override", False):
        # Display string only (B7 Cit 17, 2026.07.14): the override confirms
        # SUPPORT; it does not always rest on a located quote, so do not claim
        # one here -- that contradicted the Quote row's "No Quote" finding.
        return _row("Support", "supported", TIER_PASS, "SUPPORTED", "t5",
                    "Manual verification (Step 6.6) confirmed the opinion "
                    "supports the proposition; the machine verdict is "
                    "superseded.", short="supported (agent-verified)")
    quote_matched = bool(getattr(r, "quote_matched", False))
    if (getattr(r.citation, "adverse_signal", False) and not quote_matched
            and not (r.supports and r.score >= 0.8)):
        tok = (getattr(r.citation, "adverse_signal_token", "") or "").strip()
        return _row("Support", "adverse_by_design", TIER_PASS,
                    "CONTRARY (AS CITED)", CHIP_NA,
                    "The brief itself cites this case as contrary authority"
                    + (" (“%s”)" % tok if tok else "")
                    + ", so low support is the expected, correct outcome. "
                      "Not an error.",
                    short="cited as contrary authority by the brief — "
                          "not an error")
    unpag = _source_unpaginated(r)
    if (getattr(r, "pincite_given", False)
            and getattr(r, "pincite_found", None) is False
            and not unpag and not quote_matched):
        note = (getattr(r, "pincite_note", "") or "").strip()
        return _row("Support", "page_not_found", TIER_REVIEW,
                    "PAGE NOT FOUND", "t4",
                    (note + " " if note else "")
                    + "The cited page could not be located on the "
                      "paginated source copy. Check the pincite.",
                    short="pincite page not found")
    if r.inextractability_score >= 0.7 and not quote_matched:
        txt = ("The cited page does not back the proposition (confidence "
               "%.2f, inextractability %.2f)."
               % (r.score, r.inextractability_score))
        if vf == "confirmed_does_not_support":
            txt += " Confirmed by manual verification (Step 6.6)."
        elif vf == "unable":
            txt += (" Manual verification could not obtain the opinion; "
                    "treat as unsupported pending human review.")
        return _row("Support", "does_not_support", TIER_DEFECT,
                    "DOES NOT SUPPORT", "t3", txt,
                    short="cited page does not support the proposition")
    thin = 0 < getattr(r, "opinion_chars", 0) < THIN_OPINION_CHARS
    if quote_matched and not thin:
        return _row("Support", "supported", TIER_PASS, "SUPPORTED", "t5",
                    "Verbatim quotation located in the opinion — "
                    "confirmed support.", short="supported (verbatim quote)")
    if thin:
        return _row("Support", "review", TIER_REVIEW, "REVIEW", "t4",
                    "The resolved text is a stub (%d characters) and cannot "
                    "ground a confident verdict on its own."
                    % getattr(r, "opinion_chars", 0),
                    short="stub text — review by hand")
    if (not r.supports and r.score == 0.0
            and r.inextractability_score == 0.0):
        return _row("Support", "text_unavailable", TIER_UNVERIFIED,
                    "TEXT UNAVAILABLE", "tu",
                    "Full opinion text was unavailable, so support could "
                    "not be checked. Pull the opinion manually to confirm.",
                    short="text unavailable")
    if r.inextractability_score >= 0.5 or r.score < 0.3:
        return _row("Support", "review", TIER_REVIEW, "REVIEW", "t4",
                    "Support is weak or ambiguous (confidence %.2f, "
                    "inextractability %.2f). Read the opinion before "
                    "relying on it." % (r.score, r.inextractability_score),
                    short="weak or ambiguous support")
    if r.supports and r.score >= 0.8:
        txt = "Cited page backs the proposition."
        if (getattr(r, "pincite_given", False)
                and getattr(r, "pincite_found", None) is False and unpag):
            txt += (" The specific page could not be checked — the "
                    "retrieved copy carries no reporter pagination — "
                    "but the support itself is confirmed.")
        return _row("Support", "supported", TIER_PASS, "SUPPORTED", "t5",
                    txt, short="supported")
    if r.supports and r.score > 0.5:
        return _row("Support", "somewhat", TIER_REVIEW, "SOMEWHAT", "t4",
                    "Moderate support (confidence %.2f). Review whether the "
                    "cited case's facts are distinguishable." % r.score,
                    short="moderate support — confirm by hand")
    return _row("Support", "review", TIER_REVIEW, "REVIEW", "t4",
                "Low support without a clear red flag (confidence %.2f). "
                "Read the cite before relying on it." % r.score,
                short="low support — review by hand")


# ---------------------------------------------------------------------------
# Check 4 -- Good-law treatment (evidence classes; never a conclusion)
# ---------------------------------------------------------------------------
def _signal_sentence(entry):
    sigs = (entry or {}).get("signals") or []
    if not sigs:
        return ""
    s = sigs[0]
    src = ", ".join(x for x in (s.get("citing_name"), s.get("court"),
                                s.get("date")) if x)
    out = "Evidence: “%s”" % s.get("verb", "")
    if src:
        out += " — " + src
    out += "."
    if s.get("passage"):
        out += " “%s”" % tidy_passage(s.get("passage", ""))
    return out


def check_treatment(r, tcls=None, tentry=None):
    if tcls is None:
        return _row("Treatment", "not_checked", None, "NOT CHECKED",
                    CHIP_NA, "Treatment pass not run for this citation.",
                    short="treatment not checked")
    if tcls == "not_checked":
        reason = (tentry or {}).get("reason", "") or ""
        return _row("Treatment", "not_checked", None, "NOT CHECKED",
                    CHIP_NA,
                    ("Not checked" + (" — " + reason if reason else "")
                     + ".").replace("..", "."),
                    short="treatment not checked")
    cov = (tentry or {}).get("coverage", "") or ""
    ceiling = " The coverage sentence is the ceiling of the claim."
    if tcls == "negative":
        # Phase 8 (author 2026.07.15): the goodlaw pass yields PROXIMITY
        # evidence, never a confirmed holding -- a negative signal alone
        # is a Review item (verify the treatment on Westlaw or Lexis),
        # not a fix-before-filing Defect.
        return _row("Treatment", "negative", TIER_REVIEW,
                    "NEGATIVE SIGNAL", "t4",
                    ("A negative-treatment signal (overruled/abrogated/"
                     "superseded class) appears NEAR a citation to this "
                     "case in a citing opinion — a proximity signal, not "
                     "a confirmed holding. Verify the treatment on "
                     "Westlaw or Lexis. "
                     + _signal_sentence(tentry)
                     + (" " + cov if cov else "") + ceiling).strip(),
                    short="unconfirmed negative-treatment signal — "
                          "verify on Westlaw or Lexis")
    if tcls == "caution":
        return _row("Treatment", "caution", TIER_REVIEW, "CAUTION", "t4",
                    ("A caution signal (distinguished/limited/questioned "
                     "class) appears in a citing opinion. "
                     + _signal_sentence(tentry)
                     + " Confirm the distinction does not reach these "
                       "facts." + (" " + cov if cov else "") + ceiling
                     ).strip(),
                    short="treatment caution — distinguished or "
                          "questioned")
    return _row("Treatment", "clean", TIER_PASS, "GOOD LAW", "t5",
                ("No negative-treatment signals found."
                 + (" " + cov if cov else "") + ceiling).strip(),
                short="no negative-treatment signals")


# ---------------------------------------------------------------------------
# Card assembly
# ---------------------------------------------------------------------------
def is_nested(r):
    """2c: a cite living only inside a parent's (quoting ...)/(citing ...)
    parenthetical renders as a sub-block under the parent, never as a
    standalone card."""
    return bool(getattr(r.citation, "nested_parenthetical", "") or "")


def compute_checks(r, tcls=None, tentry=None):
    """The four rows + the card tier (worst failing check)."""
    rows = [check_identity(r), check_quote(r), check_support(r),
            check_treatment(r, tcls, tentry)]
    tiers = [row["tier"] for row in rows if row["tier"] is not None]
    tier = min(tiers) if tiers else TIER_PASS
    if is_nested(r):
        # 2c: quoted-source blocks are informational. Existence confirmed ->
        # PASS; otherwise a quiet REVIEW. They never carry an adverse tier
        # of their own (the parent's quote/support checks own the language).
        tier = TIER_PASS if (r.opinion_resolved
                             or getattr(r, "opinion_url", "")) else TIER_REVIEW
    return {"rows": rows, "identity": rows[0], "quote": rows[1],
            "support": rows[2], "treatment": rows[3], "tier": tier}


def offender_reason(checks):
    """One-line reason for the scoreboard offender list: the worst row."""
    worst = None
    for row in checks["rows"]:
        if row["tier"] is None:
            continue
        if worst is None or row["tier"] < worst["tier"]:
            worst = row
    if worst is None or worst["tier"] == TIER_PASS:
        return ""
    return worst["short"]


def all_clean(checks):
    """True when the card can collapse to a single PASS line: every row is
    PASS or informational."""
    return checks["tier"] == TIER_PASS and all(
        row["tier"] in (None, TIER_PASS) for row in checks["rows"])
