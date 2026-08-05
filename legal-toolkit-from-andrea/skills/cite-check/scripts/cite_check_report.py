"""
C2 -- Analyst report surface for cite-check results (Project: Isaacus
Integration, Plan v3).

Renders the list of CiteCheckResult objects produced by cite_check.py
into a human-readable artifact.  Three formats:

    * render_html(results, meta)         -> str   (for quick browser review)
    * render_markdown(results, meta)     -> str   (for pasting / .txt output)
    * render_docx(results, meta, out)    -> None  (Word document via docx-js)

Styling: neutral serif stack (Palatino/Georgia), teal
section labels, amber/red/green flag colors for QA verdicts.

render_html and render_markdown have no external dependencies.
render_docx requires Node.js and the `docx` npm package.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from cite_check import CiteCheckResult
import cc_application as ccapp


# --------------------------------------------------------------------------
# Verdict classification
# --------------------------------------------------------------------------
# Below this many characters the resolved opinion is a stub / wrong page and
# cannot ground a confident verdict (Chunk 4 thin-text guard).
THIN_OPINION_CHARS = 400


def _balance_display_quotes(s: str) -> str:
    """Rebalance a proposition's quotation marks FOR DISPLAY only.

    Proposition extraction can strip a leading quotation mark (the parenthetical
    ("...") wrapper), leaving a dangling closing quote so the reader cannot see
    where the checkable quote begins (Brief C as-filed Cits 2 & 7). This adds the
    missing delimiter so the quoted span reads cleanly; it never changes the
    text that was verified, only what is shown.
    """
    s = (s or "").strip()
    if not s:
        return s
    # Curly quotes: add the missing partner on whichever side is short.
    if s.count("”") > s.count("“"):
        s = "“" + s
    elif s.count("“") > s.count("”"):
        s = s + "”"
    # Straight quotes: an odd count means one is unmatched. A lone mark in the
    # back half is a closing quote (prepend an opener); otherwise it opens
    # (append a closer).
    if s.count('"') % 2 == 1:
        pos = s.rfind('"')
        if pos > len(s) / 2:
            s = '"' + s
        else:
            s = s + '"'
    return s


def _verdict(result: CiteCheckResult) -> str:
    """Classify a result into one of eight statuses.

    Verdict keys:
        verified        — found, confirmed, holding supports proposition
        somewhat        — found, confirmed, but support is only moderate (Chunk 4)
        partial         — found, confirmed, but full text unavailable for holding check
        flagged         — found, but issue detected (nuanced holding, discrepancy, dicta)
        does_not_support — found, but holding contradicts/does not support proposition
        unable          — not found in free databases (coverage gap)

    Chunk 4 verdict layer adds three behaviors on top of the score thresholds:
      * thin / wrong-text guard — a stub opinion or one that failed the case
        name/cite gate can never be "verified" (unless a verbatim quote is
        actually located in it);
      * quote-match override — a verbatim quote present in the opinion is
        confirmed support regardless of a moderate model score (Connaughton);
      * "Somewhat Supports" tier — genuine but moderate support gets its own
        verdict instead of a blanket Flagged (Platinum).

    Body-only citations (cited in the brief but missing from the TOA) are
    force-flagged.  "Does Not Support" still wins over body_only.
    """
    if not result.opinion_resolved:
        return "unable"

    # Step 6.6 (2026.07.10): evidence-gated agent-verification override.
    # Granted ONLY by cite_check_runner's `verify` ingest after the fetched
    # opinion passed the identity gate and the agent's quote matched VERBATIM
    # -- the same evidence bar as the Connaughton quote override.  The machine
    # verdict stays visible on the card (verification_machine_verdict).
    # Taxonomy untouched: the override maps to the existing "verified" key.
    if getattr(result, "verification_override", False):
        return "verified"

    # Proposition never extracted (Phase 1 / known-bug 5.2): the pipeline
    # refused to verify a bare case name, so there is no support score to
    # report.  Own verdict; never a confident-looking number.
    if not (getattr(result.citation, "proposition", "") or "").strip():
        return "prop_not_extracted"

    quote_matched = bool(getattr(result, "quote_matched", False))
    thin = 0 < getattr(result, "opinion_chars", 0) < THIN_OPINION_CHARS

    # Identity gate failed (item 1 / locked taxonomy): the resolved opinion may
    # not be the cited case (e.g. a different same-surname opinion).  Never
    # confident; a located verbatim quote overrides.
    if getattr(result, "name_cite_ok", None) is False and not quote_matched:
        return "identity_unconfirmed"
    # Pincite rule (locked spec #10; item 3): a supplied pinpoint page that
    # cannot be located in the resolved opinion is Not Verified -- never a silent
    # whole-opinion pass.  A located verbatim quote overrides.
    # BUT only when the resolved copy actually CARRIES pagination: if the source
    # has no page markers at all (common when a "WL" cite resolves to a free
    # source that lacks Westlaw star-pages), the page simply could not be
    # checked -- that is not a citation defect and must not headline over the
    # substantive support verdict (else a Does-Not-Support or a strong Verified
    # is hidden behind "Pincite Not Found").  2026.06.30.
    _pnote = (getattr(result, "pincite_note", "") or "").lower()
    _source_unpaginated = ("could not be checked" in _pnote) or ("no pagination" in _pnote) or ("no reporter pagination" in _pnote)
    # Hybrid case (2026.06.30): the proposition IS supported but the cited page
    # cannot be confirmed because the source copy is unpaginated.  Reported as
    # its own treatment ("Supported - Pincite Unconfirmed") so we never imply we
    # checked the page (false "Verified") nor imply the cite is wrong (false
    # "Pincite Not Found").  Precise about what was and was not verified.
    _pincite_unverifiable = (
        getattr(result, "pincite_given", False)
        and getattr(result, "pincite_found", None) is False
        and _source_unpaginated
        and not quote_matched
    )
    if (getattr(result, "pincite_given", False)
            and getattr(result, "pincite_found", None) is False
            and not _source_unpaginated
            and not quote_matched):
        return "pincite_not_found"

    # Cited as Contrary (locked taxonomy; item 6): the citation is introduced
    # by an adverse signal (but see / contra / distinguished), so a low or
    # contradicting support score is the EXPECTED, correct outcome -- NOT a
    # citation error.  A located verbatim quote or genuine strong support still
    # overrides (the cite actually backs the proposition as stated).
    if (getattr(result.citation, "adverse_signal", False)
            and not quote_matched
            and not (result.supports and result.score >= 0.8)):
        return "cited_as_contrary"

    # Application-sentence re-render (2026.08.04): same single source of
    # truth as cc_severity.check_support (cc_application.rerender_key).
    _app_unavail = (not result.supports and result.score == 0.0
                    and result.inextractability_score == 0.0)
    _app_rr = ccapp.rerender_key(result, quote_matched, thin, _app_unavail)
    if _app_rr == "distinguish":
        return "cited_to_distinguish"
    if _app_rr == "applied_rule":
        return "applied_rule"
    # Opinion found but holding flatly contradicts the cited proposition.
    # A located verbatim quote outranks a high inextractability score.
    if result.inextractability_score >= 0.7 and not quote_matched:
        return "does_not_support"
    # TOA coverage gap — force flagged even when verify() liked the result.
    if getattr(result, "body_only", False):
        return "flagged"
    # Opinion found but full text wasn't available to check the holding
    if not result.supports and result.score == 0.0 and result.inextractability_score == 0.0:
        return "partial"

    # Quote-match override: a verbatim quote literally in the opinion is
    # confirmed support regardless of a moderate score (fixes Connaughton @0.56).
    if quote_matched and not thin:
        return "verified"
    # Thin-text guard: a stub can't carry a confident verdict on its own.
    if thin:
        return "flagged"

    # Something's off — moderate inextractability or very low confidence
    if result.inextractability_score >= 0.5 or result.score < 0.3:
        return "flagged"
    if result.supports:
        if result.score >= 0.8:
            return "pincite_unconfirmed" if _pincite_unverifiable else "verified"
        # Genuine but moderate support → "Somewhat Supports" (Platinum), not a
        # blanket Flagged.
        if result.score > 0.5:
            return "pincite_unconfirmed" if _pincite_unverifiable else "somewhat"
        return "flagged"
    # Fallback — low support without a clear red flag
    return "flagged"


_VERDICT_LABEL = {
    "verified": "Verified",
    "somewhat": "Somewhat Supports",
    "pincite_unconfirmed": "Supported · Page Unverified",
    "partial": "Text Unavailable",
    "flagged": "Flagged",
    "applied_rule": "Applied Rule — Verified Elsewhere",
    "cited_to_distinguish": "Cited to Distinguish — Review",
    "does_not_support": "Does Not Support",
    "cited_as_contrary": "Cited as Contrary",
    "identity_unconfirmed": "Identity Unconfirmed",
    "pincite_not_found": "Page Not Found",
    "prop_not_extracted": "Proposition Not Extracted — Review Required",
    "unable": "Unable to Verify",
}

_VERDICT_HEX = {
    "verified": "#0FA685",
    "somewhat": "#C9961C",
    "pincite_unconfirmed": "#00838F",
    "partial": "#2E8BC0",
    "flagged": "#E8870E",
    "applied_rule": "#B8860B",
    "cited_to_distinguish": "#B8860B",
    "does_not_support": "#D44040",
    "cited_as_contrary": "#455A64",
    "identity_unconfirmed": "#C2185B",
    "pincite_not_found": "#6D4C41",
    "prop_not_extracted": "#827717",
    "unable": "#7E57C2",
}

_VERDICT_BG = {
    "verified": "#E6F4ED",
    "somewhat": "#FBF3DF",
    "pincite_unconfirmed": "#E0F7FA",
    "partial": "#E3F2FD",
    "flagged": "#FFF3E0",
    "applied_rule": "#FBF6E3",
    "cited_to_distinguish": "#FBF6E3",
    "does_not_support": "#FDECEC",
    "cited_as_contrary": "#ECEFF1",
    "identity_unconfirmed": "#FCE4EC",
    "pincite_not_found": "#EFEBE9",
    "prop_not_extracted": "#F6F5E4",
    "unable": "#F0EBF5",
}

# Ordered list for summary tables and status keys
_VERDICT_ORDER = ["verified", "somewhat", "pincite_unconfirmed", "partial", "flagged", "applied_rule", "cited_to_distinguish", "does_not_support", "cited_as_contrary", "identity_unconfirmed", "pincite_not_found", "prop_not_extracted", "unable"]

_VERDICT_KEY_TEXT = {
    "verified": "Green light. We found the opinion, and its holding supports the exact point the brief cites it for, at the page cited. Nothing to do.",
    "somewhat": "We found the opinion and it supports the point, but only moderately. Check that the cited case's facts aren't meaningfully different from ours before relying on it.",
    "pincite_unconfirmed": "Two separate things: (1) the opinion DOES support the point \u2014 that is confirmed; (2) we could NOT confirm the specific PAGE cited, because the copy we retrieved has no reporter page numbers to check against (e.g. a Westlaw \"WL\" cite pulled from a free source or PACER, which number pages differently). So support = yes, page = could not check. This is NOT a page error \u2014 see \"Page Not Found\" for that. (Confidence score on the card shows how strong the support is.)",
    "partial": "We found the opinion but could not get its full text, so we could not check whether it supports the point. Pull it manually to confirm.",
    "flagged": "We found the opinion, but something needs a human eye: the support is weak or unclear, the language may be dicta, or there is a discrepancy. Read it before relying on it.",
    "applied_rule": "The sentence this case is cited for APPLIES the cited rule to the facts of this case (e.g. \u201cBecause no defendant has liability, the conspiracy theory fails\u201d), so the support classifier scores it against the opinion text and lands low by construction. The same authority\u2019s underlying rule IS verified at another citation in this brief (the card names it). The citation is sound; review the application sentence, not the cite.",
    "cited_to_distinguish": "The brief cites this authority to distinguish or discount it (\u201creliance on X is misplaced\u201d), so a low support score is the expected shape, not a defect. The characterization\u2019s claim about the cited case may still be checkable \u2014 read the opinion before relying on it.",
    "does_not_support": "Red flag. We found the opinion and it does NOT support \u2014 or actually cuts against \u2014 the point the brief cites it for. Review before filing.",
    "cited_as_contrary": "Not an error. The brief itself cites this case as contrary authority (e.g. \"but see\"), so a low or negative support score is expected and correct.",
    "identity_unconfirmed": "We found AN opinion, but could not confirm it is the SAME case the brief cites \u2014 the name or reporter citation did not line up (often a different case sharing a surname). Treat as possibly the wrong case and verify the citation.",
    "pincite_not_found": "Possible page error. The opinion DOES have page numbers, we looked for the specific page the brief cites, and that page is not there. (Contrast \"Supported \u00b7 Page Unverified,\" where there were no pages to check at all.) Check the pincite.",
    "prop_not_extracted": "The pipeline could not extract the sentence this case is cited to support, so nothing was verified — no score is shown because none exists. Read the cite in the brief and check it manually (or re-run after the proposition pass).",
    "unable": "We could not find this opinion in any database we searched. This is a coverage gap, not necessarily an error \u2014 use the link on the card to check it yourself (e.g. on Westlaw).",
}


# --------------------------------------------------------------------------
# Reviewer notes (Step 4B — factual-distinguishability flags)
# --------------------------------------------------------------------------
def _reviewer_note(result: CiteCheckResult) -> str:
    """Return a reviewer note if the result warrants human attention.

    Returns empty string if no note needed. These notes are additive --
    they appear alongside the verdict, not instead of it.
    """
    if not result.opinion_resolved:
        return ""
    # Graded quote-fidelity note (Part 3, 2026.07.09): additive and LEADS any
    # other note — a misquote warning must never be hidden behind a
    # moderate-support observation.
    qn = (getattr(result, "quote_note", "") or "").strip()

    def _with_qn(msg):
        return (qn + " " + msg) if qn else msg

    # Moderate support — facts may be distinguishable
    if result.supports and 0.5 < result.score < 0.8:
        return _with_qn(
            "The opinion text contains language supporting this proposition, "
            "but the support is moderate (confidence {:.0f}%). Review whether "
            "the cited facts are distinguishable from your case.".format(
                result.score * 100
            )
        )
    # High inextractability but not red — something is off
    if 0.5 <= result.inextractability_score < 0.7:
        return _with_qn(
            "Support is weak or ambiguous (inextractability {:.0f}%). "
            "The opinion may address the topic without directly supporting "
            "the specific proposition.".format(
                result.inextractability_score * 100
            )
        )
    return qn


# --------------------------------------------------------------------------
# Step 6.6 (2026.07.10): agent-verification line (additive provenance layer,
# same design as agent-supplied propositions).  Set by cite_check_runner's
# `verify` ingest; never changes verify() semantics or the taxonomy.
# --------------------------------------------------------------------------
_VERIFICATION_LABEL = {
    "confirmed_supports": "Supports",
    "confirmed_does_not_support": "Does Not Support (confirmed)",
    "confirmed_wrong_case": "Wrong case resolved (confirmed)",
    "unable": "Unable to verify manually",
}


def _verification_note(result) -> str:
    """One-line agent-verification annotation, or '' when none ingested."""
    f = (getattr(result, "verification_finding", "") or "").strip()
    if not f:
        return ""
    lab = _VERIFICATION_LABEL.get(f, f)
    if getattr(result, "verification_override", False):
        mv = getattr(result, "verification_machine_verdict", "") or ""
        head = ("machine: " + _VERDICT_LABEL.get(mv, mv or "?")
                + " \u2192 agent-verified: Supports")
    else:
        head = "agent-verified: " + lab
    note = (getattr(result, "verification_note", "") or "").strip()
    url = (getattr(result, "verification_url", "") or "").strip()
    out = head
    if note:
        out += " \u2014 " + note
    if url:
        out += " [" + url + "]"
    return out


# --------------------------------------------------------------------------
# Non-case reference section helper
# --------------------------------------------------------------------------
def _render_non_case_markdown(non_case_refs: list) -> str:
    """Render a Markdown section for non-case references."""
    if not non_case_refs:
        return ""
    lines = [
        "",
        "## Non-Case References Identified",
        "",
        "The following references were identified in the brief but are not court opinions.",
        "They were not verified by this tool.",
        "",
    ]
    for ref in non_case_refs:
        name = ref.get("name", "")
        rtype = ref.get("type", "reference")
        lines.append(f"- {name} ({rtype})")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Markdown renderer
# --------------------------------------------------------------------------
# --- Treatment (good-law evidence) section, 2026.07.06 ----------------------
# Language discipline (LOCKED): this section presents EVIDENCE, never a
# conclusion about an authority's continuing validity. Classes are evidence
# classes on a separate axis from the 11 verdicts.

_TREATMENT_LABEL = {"negative": "Negative Treatment Signal",
                    "caution": "Treatment Caution"}
_TREATMENT_INTRO = (
    "Evidence gathered from opinions citing each authority (CourtListener "
    "citation graph, full-text treatment-term probe). This section presents "
    "evidence with quotes and links -- never a conclusion about an "
    "authority's continuing validity. The coverage sentence on each row is "
    "the ceiling of the claim.")


def _render_treatment_markdown(meta: dict) -> str:
    tr = meta.get("treatment") or {}
    if not tr:
        return ""
    lines = ["", "---", "", "## Treatment Signals (Good-Law Evidence)", "",
             _TREATMENT_INTRO, ""]
    rows = tr.get("rows") or []
    if rows:
        for row in rows:
            label = _TREATMENT_LABEL.get(row["classification"],
                                         row["classification"])
            inst = ", ".join(str(i + 1) for i in row.get("instance_indexes", []))
            lines.append(f"### {label}: {row['name']}, {row['reporter_cite']}"
                         + (f" (Citation {inst})" if inst else ""))
            lines.append("")
            for sg in row.get("signals", []):
                _cd = ", ".join(x for x in (sg.get("court"),
                                            sg.get("date")) if x)
                who = sg.get("citing_name") or "a later opinion"
                lines.append(
                    f'- **{row["name"]}**, {row["reporter_cite"]} -- the '
                    f'treatment term **"{sg["verb"]}"** appears near a '
                    f'citation to this authority in *{who}*'
                    + (f" ({_cd})" if _cd else "")
                    + (f" ([citing opinion]({sg['url']}))"
                       if sg.get("url") else ""))
                if sg.get("passage"):
                    lines.append(f"  > …{sev.tidy_passage(sg['passage'], 320)}…")
            if row.get("coverage"):
                lines.append(f"- *Coverage: {row['coverage']}*")
            lines.append("")
    else:
        lines.append("*No negative-treatment or caution signals found.*")
        lines.append("")
    clean = tr.get("clean") or []
    if clean:
        lines.append(f"### No signal found ({len(clean)} authorities)")
        lines.append("")
        for row in clean:
            lines.append(f"- **{row['name']}**, {row['reporter_cite']} -- "
                         f"{row.get('coverage', '')}")
        lines.append("")
    ncx = tr.get("not_checked") or []
    if ncx:
        lines.append(f"### Not checked ({len(ncx)})")
        lines.append("")
        for row in ncx:
            lines.append(f"- **{row['name']}** -- {row.get('reason', '')}")
        lines.append("")
    return "\n".join(lines)


def _render_treatment_html(meta: dict) -> str:
    tr = meta.get("treatment") or {}
    if not tr:
        return ""
    esc = html.escape
    parts = ['<div style="margin-top:2rem;padding-top:1rem;'
             'border-top:1px solid #E8E8E4">',
             '<h2>Treatment Signals (Good-Law Evidence)</h2>',
             '<p style="color:var(--muted);font-size:0.88rem">'
             + esc(_TREATMENT_INTRO) + '</p>']
    colors = {"negative": "#B71C1C", "caution": "#E65100"}
    for row in tr.get("rows") or []:
        cls = row["classification"]
        label = _TREATMENT_LABEL.get(cls, cls)
        inst = ", ".join(str(i + 1) for i in row.get("instance_indexes", []))
        parts.append(
            '<div style="border-left:4px solid {c};background:#FFF;'
            'padding:0.8rem 1rem;margin:0.8rem 0;border-radius:6px">'.format(
                c=colors.get(cls, "#666")))
        parts.append(
            '<b style="color:{c}">&#9888; {lab}</b> &mdash; <b>{nm}</b>, {rc}'
            .format(c=colors.get(cls, "#666"), lab=esc(label.upper()),
                    nm=esc(row["name"]), rc=esc(row["reporter_cite"]))
            + (' <span style="color:var(--muted)">(Citation ' + esc(inst)
               + ')</span>' if inst else ''))
        for sg in row.get("signals", []):
            link = (' <a href="{u}">read the citing opinion</a>'.format(
                        u=esc(sg["url"])) if sg.get("url") else "")
            # Every signal line names WHICH authority is at issue and states
            # that this is a proximity signal (the term appears near a citation
            # to the authority), never a conclusion that the authority was
            # treated -- legible and accurate (B4, 2026.07.14).
            parts.append(
                '<div style="margin-top:0.6rem;font-size:0.92rem">'
                '<b>{nm}</b>, {rc} &mdash; the treatment term '
                '<b>&ldquo;{v}&rdquo;</b> appears near a citation to this '
                'authority in <i>{who}</i>{l}</div>'.format(
                    nm=esc(row["name"]), rc=esc(row["reporter_cite"]),
                    v=esc(sg["verb"]),
                    who=esc(sg.get("citing_name") or "a later opinion"),
                    l=link))
            _cd = ", ".join(x for x in (sg.get("court"), sg.get("date")) if x)
            if _cd:
                parts.append('<div style="color:var(--muted);font-size:0.82rem;'
                             'margin:0.1rem 0 0 1rem">' + esc(_cd) + '</div>')
            if sg.get("passage"):
                parts.append('<div style="color:var(--muted);font-size:0.88rem;'
                             'font-style:italic;margin:0.2rem 0 0 1rem">'
                             '&ldquo;&hellip;' + esc(sev.tidy_passage(
                                 sg["passage"], 320)) + '&hellip;&rdquo;</div>')
        if row.get("coverage"):
            parts.append('<div style="color:var(--muted);font-size:0.82rem;'
                         'margin-top:0.5rem">' + esc(row["coverage"]) + '</div>')
        parts.append('</div>')
    if not tr.get("rows"):
        parts.append('<p style="font-size:0.92rem"><i>No negative-treatment '
                     'or caution signals found.</i></p>')
    clean = tr.get("clean") or []
    if clean:
        parts.append('<details style="margin-top:0.8rem"><summary style="cursor:'
                     'pointer;font-size:0.92rem">No signal found ('
                     + str(len(clean)) + ' authorities) &mdash; coverage per '
                     'authority</summary><ul style="font-size:0.88rem;'
                     'color:var(--muted)">')
        for row in clean:
            parts.append('<li><b>{nm}</b>, {rc} &mdash; {cov}</li>'.format(
                nm=esc(row["name"]), rc=esc(row["reporter_cite"]),
                cov=esc(row.get("coverage", ""))))
        parts.append('</ul></details>')
    # "Not checked" is surfaced prominently at the TOP of the report now
    # (B8, 2026.07.14) via render_html's _not_checked_html; not repeated here.
    parts.append('</div>')
    return "\n".join(parts)


def render_markdown(
    results: Sequence[CiteCheckResult],
    meta: dict | None = None,
) -> str:
    """Produce a Markdown cite-check report."""
    meta = meta or {}
    _attach_display_captions(results)
    lines = []
    lines.append("# Cite-Check Report")
    lines.append("")
    if meta.get("jurisdiction"):
        lines.append(f"**Detected jurisdiction:** {meta['jurisdiction']}")
    chunking = meta.get("chunking") or {}
    if chunking:
        lines.append(
            f"**Chunking:** {chunking.get('mode', 'skip')} "
            f"({chunking.get('chunks', 0)} chunk(s))"
        )
    lines.append(f"**Citations verified:** {len(results)}")
    lines.append("")

    # Summary table
    counts = {k: 0 for k in _VERDICT_ORDER}
    for r in results:
        counts[_verdict(r)] += 1
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for k in _VERDICT_ORDER:
        lines.append(f"| {_VERDICT_LABEL[k]} | {counts[k]} |")
    lines.append("")

    # Detail -- Phase 5 layout (2026.07.04, audit 3.5): full brief cite +
    # pincite -> verdict -> proposition -> evidence passage (+ located page)
    # -> links -> notes.
    for i, r in enumerate(results, start=1):
        v = _verdict(r)
        _ct = (getattr(r.citation, "cite_text", "") or "").strip()
        _headline = _ct or (r.citation.name or "").strip() or "(citation)"
        lines.append(f"## {i}. {_headline} \u2014 {_VERDICT_LABEL[v]}")
        _nm = (r.citation.name or "").strip()
        if _ct and _nm:
            _nm_head = _nm.split(",")[0].strip().lower()
            if _nm_head and _nm_head not in _ct.lower():
                lines.append(f"*Authority:* {_nm}")
        if _ct:
            lines.append(f"*Cite (as written in brief):* {_ct}")
        if r.citation.type:
            lines.append(f"*Type:* {r.citation.type}")
        if r.citation.jurisdiction:
            lines.append(f"*Jurisdiction:* {r.citation.jurisdiction}")
        # TOA enrichment
        toa = getattr(r.citation, "toa_match", None)
        if toa:
            pages = toa.get("pages") or []
            if pages:
                lines.append(f"*Cited at pp.:* {', '.join(pages)}")
            full_cite = toa.get("cite") or ""
            if full_cite:
                lines.append(f"*TOA full cite:* {toa.get('name', '')}, {full_cite}")
        # Pincite from the brief -- always shown when supplied.
        _pin = (getattr(r.citation, "pincite", "") or "").strip()
        if _pin:
            _pf = getattr(r, "pincite_found", None)
            if _pf is True:
                _ps = " -- located in opinion"
            elif _pf is False:
                _n = (getattr(r, "pincite_note", "") or "").strip()
                _ps = " -- " + (_n if _n else "not located on source")
            else:
                _ps = ""
            lines.append(f"*Pincite (from brief):* p. {_pin}{_ps}")
        lines.append("")
        # Proposition (+ agent provenance rendered cleanly under it).
        if r.citation.proposition:
            lines.append("**Proposition (from brief):**")
            lines.append(f"> {_balance_display_quotes(r.citation.proposition.strip())}")
            if getattr(r.citation, "proposition_source", "") == "agent":
                lines.append("")
                lines.append("*Proposition supplied by in-session Claude review of the "
                             "brief paragraph (the structural extractor could not).*")
            lines.append("")
        elif getattr(r.citation, "proposition_review", False):
            lines.append("**Proposition (from brief):** _No verifiable proposition extracted — review required._")
            lines.append("")
        if r.opinion_resolved:
            # Confidence to two decimals ONLY on close calls (audit 3.5).
            if v in ("somewhat", "flagged", "does_not_support", "cited_as_contrary"):
                lines.append(f"**Confidence:** {r.score:.2f}")
            if r.passage:
                lines.append("")
                lines.append("**Extracted passage:**")
                lines.append(f"> {r.passage.strip()}")
            _ppage = (getattr(r, "passage_page", "") or "").strip()
            if _ppage == "footnotes":
                lines.append("")
                lines.append("*Supporting passage located:* in the opinion's footnotes")
            elif _ppage:
                lines.append("")
                lines.append(f"*Supporting passage located at:* *{_ppage}")
            # Phase 4: Answer Extractor second opinion (close-call cards only).
            _so = (getattr(r, "second_opinion", "") or "").strip()
            if _so:
                _sos = getattr(r, "second_opinion_score", None)
                _tail = f" (answer strength {_sos:.2f})" if _sos is not None else ""
                lines.append("")
                lines.append(f"**Second opinion (Answer Extractor):** {_so}{_tail}")
            _vn = _verification_note(r)
            if _vn:
                lines.append("")
                lines.append(f"**Manual verification (Step 6.6):** {_vn}")
            rnote = _reviewer_note(r)
            if rnote:
                lines.append("")
                lines.append(f"**Reviewer note:** {rnote}")
        elif r.search_detail:
            # Gray / UNRESOLVED citation -- show what the resolver tried.
            lines.append(f"**Search attempted:** {r.search_detail}")
        # Citation-lookup note (Phase 5: own labeled line, not glued to notes).
        _ln = (getattr(r, "lookup_note", "") or "").strip()
        if _ln:
            _ls = getattr(r, "lookup_status", None)
            _ls_str = f" (status {_ls})" if _ls else ""
            lines.append(f"**Citation lookup{_ls_str}:** {_ln}")
        # Universal source link -- every citation is one click from the source.
        _op_url = getattr(r, "opinion_url", "") or ""
        _src_link = _op_url or r.search_url
        if _src_link:
            _src_text = "View opinion on CourtListener" if _op_url else "Look up on CourtListener"
            lines.append(f"**CourtListener:** [{_src_text}]({_src_link})")
        if (r.notes or "").strip():
            lines.append("")
            lines.append(f"*{r.notes}*")
        _tcls = ((meta.get("treatment") or {}).get("by_index") or {}).get(i - 1)
        if _tcls in _TREATMENT_LABEL:
            lines.append("")
            lines.append(f"**\u26a0 {_TREATMENT_LABEL[_tcls]}** -- see the "
                         "Treatment Signals section below.")
        lines.append("")
        lines.append("---")
        lines.append("")

    # TOA Coverage section
    toa_only = meta.get("toa_only_cases") or []
    body_only = _body_only_display(results, meta)
    if toa_only or body_only:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## TOA Coverage")
        lines.append("")
        lines.append(
            "Cross-check between the Table of Authorities and the citations "
            "actually extracted from the body."
        )
        lines.append("")
        if body_only:
            lines.append(f"### Cited in body, missing from TOA ({len(body_only)})")
            lines.append("")
            for c in body_only:
                name = c.get("name", "")
                prop = (c.get("proposition") or "").strip()
                if prop:
                    lines.append(f"- **{name}** — *{prop}…*")
                else:
                    lines.append(f"- **{name}**")
            lines.append("")
        if toa_only:
            lines.append(f"### Listed in TOA, not cited in body ({len(toa_only)})")
            lines.append("")
            for entry in toa_only:
                name = entry.get("name", "")
                cite = entry.get("cite", "")
                pages = ", ".join(entry.get("pages") or [])
                bits = [f"**{name}**"]
                if cite:
                    bits.append(cite)
                line = ", ".join(bits)
                if pages:
                    line += f" (TOA pp. {pages})"
                lines.append(f"- {line}")
            lines.append("")

    # Treatment (good-law evidence) section, 2026.07.06
    _tmd = _render_treatment_markdown(meta)
    if _tmd:
        lines.append(_tmd)

    # Non-case reference section
    non_case = meta.get("non_case_references") or []
    if non_case:
        lines.append(_render_non_case_markdown(non_case))

    # Status key
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Status Key")
    lines.append("")
    for k in _VERDICT_ORDER:
        lines.append(f"- **{_VERDICT_LABEL[k]}:** {_VERDICT_KEY_TEXT[k]}")
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# HTML renderer -- Phase 3 redesign (2026.07.14). Severity-first scoreboard,
# four labeled check rows per card (Identity / Quote / Support / Treatment),
# Variant B full-width red header on Tier-1 cards (locked), quoted-source
# sub-blocks under the parent (2c). Tiers come from cc_severity, computed
# from primitives (G8); the folded 11-verdict key survives in
# render_markdown / render_docx as a legacy label only.
# --------------------------------------------------------------------------
import cc_severity as sev

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Cite-Check Report</title>
<style>
:root {
  --t1: #B00020; --t1bg: #FBEAEC; --t1line: #8E0019;
  --tu: #534AB7; --tubg: #EEEDFE;
  --t3: #E8870E; --t3bg: #FDF1E2;
  --t4: #F5C518; --t4bg: #FBF6E3;
  --t5: #0FA685; --t5bg: #E6F4EF;
  --ink: #1F2A2E; --muted: #5B6B70; --teal: #0E5A5A; --rule: #D8E0E0;
  --navy: #1B2A4A; --body: #1F2A2E; --card: #FFFFFF;
  --verified: #0FA685; --partial: #2E8BC0; --flagged: #E8870E;
  --dns: #D44040; --unable: #7E57C2; --amber: #E8870E; --green: #0FA685;
  --t2: #534AB7; --t2bg: #EEEDFE;
}
* { box-sizing: border-box; }
body { margin: 0; background: #F4F6F6; color: var(--ink);
  font-family: "Palatino Linotype", "Book Antiqua", Georgia, serif;
  font-size: 15.5px; line-height: 1.45; }
.wrap { max-width: 940px; margin: 0 auto; padding: 28px 20px 60px; }
h1 { font-family: Georgia, serif;
  color: var(--teal); font-size: 1.9rem; margin: .2em 0 .1em; }
h2 { font-family: Georgia, serif; font-size: 1.25rem;
  color: var(--teal); margin: 1.6em 0 .5em; }
.sub { color: var(--muted); font-size: .95rem; margin-bottom: 1.1em; }
.board { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px;
  margin: 14px 0 14px; }
.tile { background: #fff; border: 1px solid var(--rule);
  border-radius: 10px; overflow: hidden; text-align: center; }
.tile .cap { height: 6px; }
.tile .body { padding: 8px 8px 10px; }
.tile .n { font-size: 1.7rem; font-weight: 700; line-height: 1.1;
  font-family: Georgia, serif; }
.tile .lab { font-size: .74rem; letter-spacing: .08em; color: var(--muted);
  text-transform: uppercase; }
.tabbar { display: flex; gap: 4px; border-bottom: 2px solid var(--rule);
  margin: 6px 0 18px; }
.tabbar button { font-family: inherit; font-size: .98rem; border: none;
  background: none; padding: 9px 18px 7px; cursor: pointer; color: var(--muted);
  border-bottom: 3px solid transparent; margin-bottom: -2px; }
.tabbar button.on { color: var(--teal); font-weight: 700;
  border-bottom-color: var(--teal); }
.tabpane { display: block; scroll-margin-top: 12px; }
.tabpane.on { display: block; }
/* Fix 10a (addendum): panes stay in the document flow so browser
   Ctrl+F finds verification evidence (PASS cards) that used to live
   only inside a display:none pane. Tabs act as jump navigation. */
.tabpane + .tabpane { border-top: 2px solid var(--rule);
  margin-top: 30px; padding-top: 6px; }
.panelabel { font-family: Georgia, serif;
  color: var(--teal); font-size: 1.15rem; margin: 10px 0 6px;
  letter-spacing: .02em; }
[id$='card-1'],[id*='card-'] { scroll-margin-top: 14px; }
.actgroup { background: #fff; border: 1px solid var(--rule);
  border-radius: 10px; padding: 11px 16px; margin: 10px 0; }
.actgroup .ghead { font-weight: 700; letter-spacing: .04em;
  font-size: .95rem; margin-bottom: 4px; }
.actgroup p { margin: .3em 0; font-size: .95rem; }
.actgroup .cn { font-weight: 700; }
.actgroup a { text-decoration: none; }
.cleanline { color: var(--muted); font-size: .95rem; margin: 12px 2px; }
.offenders { background: #fff; border: 1px solid var(--rule);
  border-radius: 10px; padding: 12px 16px; margin: 10px 0; }
.offenders p { margin: .3em 0; }
.offenders .oc { font-weight: 700; }
.oc.c { color: var(--t1); } .oc.d { color: var(--t3); }
.oc.r { color: #9A7508; }
.card { background: #fff; border: 1px solid var(--rule);
  border-radius: 10px; margin: 16px 0; display: flex; overflow: hidden; }
.band { width: 10px; flex: none; }
.card.crit { border: 2px solid var(--t1);
  box-shadow: 0 2px 10px rgba(176,0,32,.18); }
.cbody { padding: 13px 18px 14px; flex: 1; min-width: 0; }
.chead { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  margin-bottom: 2px; }
.cnum { font-family: Georgia, serif; font-weight: 700;
  font-size: 1.06rem; color: var(--teal); }
.ccite { font-style: italic; color: var(--ink); }
.chip { display: inline-block; border-radius: 999px; padding: 2px 11px;
  font-size: .74rem; font-weight: 700; letter-spacing: .07em; color: #fff;
  font-family: Georgia, serif; white-space: nowrap; }
.chip.t1 { background: var(--t1); }
.chip.t2, .chip.tu { background: var(--tu); }
.chip.t3 { background: var(--t3); }
.chip.t4 { background: var(--t4); color: #3d2f00; }
.chip.t5 { background: var(--t5); }
.chip.na { background: #B9C4C6; color: #33403f; }
.prop { background: #F6F8F8; border-left: 3px solid var(--rule);
  padding: 7px 11px; margin: 8px 0; font-size: .93rem; color: #37474c;
  border-radius: 0 6px 6px 0; }
.prop b { color: var(--teal); font-style: normal; }
table.checks { width: 100%; border-collapse: collapse; margin-top: 6px; }
table.checks td { padding: 5px 8px 5px 0; vertical-align: top;
  border-top: 1px dotted #E3E9E9; }
td.axis { width: 96px; font-size: .8rem; letter-spacing: .06em;
  color: var(--muted); text-transform: uppercase; padding-top: 8px; }
td.stat { width: 170px; }
td.why { font-size: .95rem; }
.ok { color: #0B7A62; }
.crittext { color: var(--t1); font-weight: 700; }
.evid { background: var(--t1bg); border-left: 4px solid var(--t1);
  padding: 8px 12px; margin: 8px 0 2px; border-radius: 0 6px 6px 0; }
a { color: var(--teal); }
.note { font-size: .88rem; color: var(--muted); margin-top: 6px; }
.sr { font-size: .9rem; color: var(--muted); }
.qdiff { background: #FBFBF7; border: 1px solid var(--rule);
  border-radius: 8px; padding: 9px 13px; margin: 8px 0; font-size: .94rem; }
.qdiff .lbl { font-size: .78rem; letter-spacing: .06em; color: var(--muted);
  text-transform: uppercase; margin: 2px 0 3px; }
.qdiff .qtext { font-style: italic; line-height: 1.55; }
.qd { color: var(--t1); font-weight: 700; font-style: normal; }
.nested { margin: 12px 0 4px 14px; border-left: 4px solid #34595F;
  background: #EEF5F5; border-radius: 0 8px 8px 0; padding: 10px 14px; }
.nested .tag { display: inline-block; font-size: .8rem; letter-spacing: .1em;
  color: #fff; background: #34595F; border-radius: 6px; padding: 3px 10px;
  font-weight: 700; text-transform: uppercase; }
.nested .nhead { font-size: 1rem; margin-top: 6px; }
.notchecked { background: #FBF6E3; border: 1px solid #E4D9A8;
  border-left: 5px solid var(--t4); border-radius: 10px;
  padding: 10px 18px 12px; margin: 12px 0; }
.notchecked h2 { color: #7A5C00; font-size: 1.1rem; margin: .1em 0 .3em; }
.notchecked ul { margin: .4em 0 .2em; font-size: .95rem; }
.pagehit { background: var(--t4bg); border-left: 4px solid var(--t4);
  padding: 7px 11px; margin: 8px 0; border-radius: 0 6px 6px 0;
  font-size: .93rem; color: #5A4600; }
.vb { display: block; padding: 0; }
.vb .hdr { background: var(--t1); color: #fff; padding: 10px 18px;
  display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
.vb .hdr .cnum { color: #fff; }
.vb .hdr .ccite { color: #FBD9DE; }
.vb .cbody { padding: 12px 18px 14px; }
.keyrow { display: flex; gap: 10px; align-items: baseline;
  margin: .45em 0; }
.dot { width: 13px; height: 13px; border-radius: 3px; flex: none;
  position: relative; top: 1px; }
@media print { .tabpane { display: block !important; }
  .tabbar { display: none; } }
</style>
</head>
<body>
<div class="wrap">
<h1>Cite-Check Report</h1>
<div class="sub">@@META_LINE@@</div>
@@BOARD@@
<div class="tabbar">
<button id="tb-triage" class="on" onclick="showTab('triage')">Triage</button>
<button id="tb-all" onclick="showTab('all')">All citations</button>
<button id="tb-method" onclick="showTab('method')">Methodology and detail</button>
</div>
<div id="tab-triage" class="tabpane on">
@@ACTION_GROUPS@@
@@TRIAGE_CARDS@@
</div>
<div id="tab-all" class="tabpane">
<h2 class="panelabel">All citations</h2>
@@CARDS@@
</div>
<div id="tab-method" class="tabpane">
<h2 class="panelabel">Methodology and detail</h2>
@@NOT_CHECKED@@
@@STATUS_KEY@@
@@SECTIONS@@
</div>
</div>
<script>
function showTab(id) {
  // Fix 10a: panes are always in the searchable flow; tabs are jump nav.
  var panes = ["triage", "all", "method"];
  for (var i = 0; i < panes.length; i++) {
    document.getElementById("tb-" + panes[i]).className =
      (panes[i] === id ? "on" : "");
  }
  var el = document.getElementById("tab-" + id);
  if (el) el.scrollIntoView({behavior: "smooth"});
}
function jumpTo(cardId) {
  var el = document.getElementById(cardId);
  if (el) el.scrollIntoView();
}
</script>
</body>
</html>
"""


def _treatment_maps(meta):
    """(by_index cls map, by_index entry map) from the goodlaw summary."""
    tr = (meta or {}).get("treatment") or {}
    by_index = tr.get("by_index") or {}
    entries = {}
    for e in ((tr.get("rows") or []) + (tr.get("clean") or [])
              + (tr.get("not_checked") or [])):
        for i in e.get("instance_indexes", []):
            entries[i] = e
    return by_index, entries


_BARE_CITE_RE = re.compile(r"^\d[\d,]*\s+.+\s+\d+$")


def _is_bare_reporter_name(name):
    """True when an eyecite `name` is itself a bare reporter cite (e.g.
    '112 S.W.3d 679') with no party tokens -- nothing to caption-check."""
    nm = (name or "").strip()
    if not nm:
        return False
    low = " " + nm.lower() + " "
    if " v. " in low or " v " in low or " in re " in low or " ex parte " in low:
        return False
    if "," in nm:
        return False
    return bool(_BARE_CITE_RE.match(nm))


_PARTY_TOKENS = (" v. ", " v ", " in re ", " ex parte ", " estate of ",
                 " matter of ")


def _has_party_name(name):
    """True when a case name carries a party token (X v. Y, In re X, Estate
    of X, Matter of X, Ex parte X) -- i.e. it already reads as a case."""
    low = " " + (name or "").strip().lower() + " "
    return any(tok in low for tok in _PARTY_TOKENS)


def _lead_party(name):
    """The lead party of a case name ('Madeksho v. Abraham...' -> 'Madeksho';
    'In re H-Corp Holdings, L.P.' -> 'In re H-Corp Holdings
    Mgmt.')."""
    nm = re.split(r"\s+v\.?\s+", (name or "").strip(), maxsplit=1)[0].strip()
    return nm.split(",")[0].strip()


def _slug_caption(url):
    """Short case name parsed from a CourtListener opinion-URL slug
    ('.../madeksho-v-abraham-.../' -> 'Madeksho')."""
    slug = (url or "").rstrip("/").split("/")[-1]
    if not slug or slug.endswith(".html") or slug[:1].isdigit():
        return ""
    left = re.split(r"-v-", slug)[0]
    words = [w for w in left.split("-") if w]
    return " ".join(w.capitalize() for w in words) if words else ""


def _attach_display_captions(results):
    """Give bare-reporter / short-form cards a resolved case-name prefix for
    DISPLAY only (the attorney 2026.07.15, Brief C cit 1: '112 S.W.3d 679' headlined a
    bare reporter with no case name). Never mutates citation.name. Caption
    source priority: a sibling citation resolving to the same opinion that
    carries a party name; else the opinion-URL slug. Idempotent."""
    by_slug = {}
    for r in results:
        url = getattr(r, "opinion_url", "") or ""
        slug = url.rstrip("/").split("/")[-1]
        if not slug:
            continue
        nm = (getattr(r.citation, "name", "") or "").strip()
        if _has_party_name(nm):
            cand = _lead_party(nm)
            if cand and len(cand) > len(by_slug.get(slug, "")):
                by_slug[slug] = cand
    for r in results:
        try:
            r._display_caption = ""
        except Exception:
            continue
        nm = (getattr(r.citation, "name", "") or "").strip()
        if _has_party_name(nm):
            continue
        if not (_is_bare_reporter_name(nm)
                or getattr(r.citation, "is_short_form", False)):
            continue
        url = getattr(r, "opinion_url", "") or ""
        slug = url.rstrip("/").split("/")[-1]
        cap = by_slug.get(slug) or _slug_caption(url)
        if cap:
            r._display_caption = cap


def _body_only_display(results, meta):
    """The 'cited in body, missing from TOA' list, de-duplicated to ONE entry
    per resolved authority so a case cited under several aliases (bare
    reporter, short form, 'Id.') is named just once (the attorney 2026.07.15, Brief C:
    Madeksho appeared 6x). Falls back to meta's list when results carry no
    body_only flags."""
    out, seen, any_flag = [], set(), False
    for r in results:
        if not getattr(r, "body_only", False):
            continue
        any_flag = True
        url = getattr(r, "opinion_url", "") or ""
        slug = url.rstrip("/").split("/")[-1]
        nm = (getattr(r.citation, "name", "") or "").strip()
        key = slug or re.sub(r"[^a-z0-9]", "", nm.lower())
        if key in seen:
            continue
        seen.add(key)
        disp = (getattr(r, "_display_caption", "") or "").strip() \
            or _lead_party(nm) or nm or _short_case(r)
        out.append({"name": disp,
                    "proposition": (getattr(r.citation, "proposition", "")
                                    or "").strip()})
    if not any_flag:
        return meta.get("body_only_cases") or []
    return out


def _caption_mismatch_suppressed(r):
    """A bare-reporter name yields a false 'does not match the cited case
    name' note -- there is no party name to compare (B5 Cit 1, 2026.07.14)."""
    ln = (getattr(r, "lookup_note", "") or "")
    return (_is_bare_reporter_name(getattr(r.citation, "name", ""))
            and "does not match the cited case name" in ln)


def _retrieved_copy_label(r):
    """Human label for a retrieved opinion copy, noting a RECAP/PACER source."""
    src = (getattr(r, "opinion_source", "") or "").lower()
    if "recap" in src or "pacer" in src:
        return "the RECAP/PACER docket on CourtListener"
    return "the retrieved copy on CourtListener"


def _short_case(r):
    nm = (r.citation.name or "").strip()
    if nm:
        return nm.split(",")[0].strip()
    return (getattr(r.citation, "cite_text", "") or "").strip() or "(citation)"


def _headline(r):
    ct = (getattr(r.citation, "cite_text", "") or "").strip()
    base = ct or (r.citation.name or "").strip() or "(citation)"
    cap = (getattr(r, "_display_caption", "") or "").strip()
    # Prepend the resolved case name unless the headline already shows it
    # (the attorney 2026.07.15: no card should headline a bare reporter/short form).
    if cap and cap.split()[0].lower() not in base.lower():
        return cap + ", " + base
    return base


def _chip_html(row):
    return '<span class="chip {}">{}</span>'.format(
        row["chip_cls"], html.escape(row["chip"]))


def _diff_html(diff):
    """Misquote word-diff block: brief additions and opinion words missing
    from the brief, both red-bolded (the attorney 2026.07.15)."""
    if not diff:
        return ""
    esc = html.escape

    def _line(tokens):
        out = []
        for w, flag in tokens:
            out.append('<span class="qd">' + esc(w) + "</span>" if flag
                       else esc(w))
        return " ".join(out)

    return ('<div class="qdiff">'
            '<div class="lbl">The brief quotes</div>'
            '<div class="qtext">\u201c' + _line(diff.get("brief") or [])
            + '\u201d</div>'
            '<div class="lbl" style="margin-top:7px">The opinion reads '
            '(red = differs from the brief)</div>'
            '<div class="qtext">\u201c' + _line(diff.get("opinion") or [])
            + '\u201d</div></div>')


def _row_html(row, extra=""):
    why = html.escape(row["text"])
    if row["tier"] == sev.TIER_PASS:
        why = '<span class="ok">' + why + '</span>'
    elif row["tier"] == sev.TIER_CRITICAL:
        why = '<span class="crittext">' + why + '</span>'
    if row.get("diff"):
        extra = _diff_html(row["diff"]) + extra
    return ('<tr><td class="axis">{ax}</td><td class="stat">{chip}</td>'
            '<td class="why">{why}{extra}</td></tr>').format(
                ax=html.escape(row["axis"]), chip=_chip_html(row),
                why=why, extra=extra)


def _identity_extra(r, row):
    esc = html.escape
    bits = []
    _op_url = getattr(r, "opinion_url", "") or ""
    if _op_url and row["status"] == "confirmed":
        bits.append(' Resolved to <a href="{u}" target="_blank">the opinion '
                    'on CourtListener</a>.'.format(u=esc(_op_url)))
    elif _op_url:
        # URL present but identity NOT confirmed: surface the retrieved copy
        # (incl. RECAP/PACER) and say plainly it was not identity-confirmed,
        # so the link and the caveat stop contradicting each other
        # (B5 Cit 3 / Cit 18, 2026.07.14).
        bits.append(' A copy was retrieved (<a href="{u}" target="_blank">{lab}'
                    '</a>), but the identity gate did not confirm it is the '
                    'cited case.'.format(u=esc(_op_url),
                                         lab=esc(_retrieved_copy_label(r))))
    elif (getattr(r, "search_url", "") or ""):
        bits.append(' <a href="{u}" target="_blank">Look up on '
                    'CourtListener</a>.'.format(u=esc(r.search_url)))
    _ln = (getattr(r, "lookup_note", "") or "").strip()
    if _ln and not _caption_mismatch_suppressed(r):
        bits.append(' <span class="sr">' + esc(_ln) + '</span>')
    return "".join(bits)


def _treatment_extra(tentry):
    sigs = (tentry or {}).get("signals") or []
    if sigs and sigs[0].get("url"):
        return (' <a href="{u}" target="_blank">Citing case on '
                'CourtListener</a>.'.format(u=html.escape(sigs[0]["url"])))
    return ""


def _crit_banner(r, checks):
    """(chip label, evidence html) for a Tier-1 card. FABRICATED leads (B1)."""
    esc = html.escape
    q = checks["quote"]
    if q["status"] == "fabricated":
        fq = next((x for x in (getattr(r, "quote_results", None) or [])
                   if x.get("result") == "FABRICATED"), {})
        body = ('<span class="crittext">Fabricated quotation.</span> The '
                'quoted language — “{qt}” — does not appear anywhere in '
                'the opinion. Absence of quoted words is always Critical, '
                'even where the case is thematically consistent.').format(
                    qt=esc(sev.tidy_passage(fq.get("quote", ""), 220)))
        if fq.get("full_text_checked"):
            body += (' Re-checked against the complete opinion text before '
                     'flagging.')
        return "CRITICAL · FABRICATED QUOTATION", body
    if checks["identity"]["status"] == "wrong_case":
        return ("CRITICAL · WRONG CASE",
                '<span class="crittext">Wrong case.</span> '
                + esc(checks["identity"]["text"]))
    if checks["support"]["status"] == "contrary":
        return ("CRITICAL · CITED AS CONTRARY",
                '<span class="crittext">Cited as contrary.</span> '
                + esc(checks["support"]["text"]))
    return "CRITICAL", ""


def _prop_html(r):
    esc = html.escape
    if r.citation.proposition:
        out = ('<div class="prop"><b>Proposition (from brief):</b> '
               + esc(_balance_display_quotes(r.citation.proposition.strip())))
        if getattr(r.citation, "proposition_source", "") == "agent":
            out += (' <span class="sr">Proposition identified by Claude '
                    'review of the brief paragraph.</span>')
        return out + '</div>'
    if getattr(r.citation, "proposition_review", False):
        return ('<div class="prop"><b>Proposition (from brief):</b> '
                '<i>No verifiable proposition extracted — review '
                'required.</i></div>')
    return ""


def _nested_html(n, child, parent_checks):
    """2c: quoted-source sub-block under the parent card."""
    esc = html.escape
    kind = (getattr(child.citation, "nested_parenthetical", "") or "quoting")
    name = (child.citation.name or "").strip()
    ct = (getattr(child.citation, "cite_text", "") or "").strip()
    head = '<i>' + esc(name) + '</i>' if name else ''
    if ct and ct.lower() != name.lower():
        head += (', ' if head else '') + esc(ct)
    _op_url = getattr(child, "opinion_url", "") or ""
    if child.opinion_resolved or _op_url:
        if parent_checks and parent_checks["quote"]["tier"] == sev.TIER_PASS:
            tail = ("and the quoted language verifies against the parent's "
                    "proposition.")
        else:
            tail = "— see the parent's Quote row for the quoted language."
        status = ('<span class="ok">✓ Source exists</span> '
                  + ('(<a href="{u}" target="_blank">direct opinion '
                     'link</a>) '.format(u=esc(_op_url)) if _op_url else '')
                  + tail + ' Not scored as a standalone citation.')
    else:
        status = ('Source could not be retrieved from free databases — '
                  'confirm it by hand. Not scored as a standalone citation.')
    # Session E (2026.07.30): a nested quoted-source child can still carry a
    # Step 6.6 manual-verification finding (e.g. PPG [85]: the quoted language
    # attributed to the nested source is absent from that source's complete
    # opinion -- a possible hallucinated quotation).  Surface it here; the
    # nested block previously dropped it entirely.
    _cvn = _verification_note(child)
    vnote = ('<div class="note" style="color:#0B6E4F;font-weight:600">'
             '<b>Manual verification (Step 6.6):</b> ' + esc(_cvn) + '</div>'
             if _cvn else '')
    return ('<div class="nested"><span class="tag">QUOTED SOURCE · '
            'CITATION {n}</span>'
            '<div class="nhead">{head} — appears only inside the '
            'parent\'s “({kind} …)” parenthetical.</div>'
            '<div class="sr" style="margin-top:4px">{status}</div>'
            '{vnote}'
            '</div>').format(n=n, head=head, kind=esc(kind), status=status,
                             vnote=vnote)


def _card_html(n, r, checks, nested_blocks):
    esc = html.escape
    tier = checks["tier"]
    icon = sev.TIER_ICON.get(tier, "")
    cnum = (icon + " " if icon else "") + "Citation {}".format(n)
    headline = _headline(r)
    parts = []
    body = []

    # Authority context when the headline is a short form / bare cite.
    _ct = (getattr(r.citation, "cite_text", "") or "").strip()
    _nm = (r.citation.name or "").strip()
    auth_line = ""
    if _ct and _nm:
        _nm_head = _nm.split(",")[0].strip().lower()
        if _nm_head and _nm_head not in _ct.lower():
            auth_line = ('<div class="note"><b>Authority:</b> ' + esc(_nm)
                         + '</div>')

    collapse = (sev.all_clean(checks)
                and checks["support"]["status"] == "supported"
                and not getattr(r, "body_only", False)
                and not (getattr(r, "lookup_note", "") or "").strip()
                and not _verification_note(r))

    if auth_line:
        body.append(auth_line)
    body.append(_prop_html(r))

    if collapse:
        _op_url = getattr(r, "opinion_url", "") or ""
        idb = ('Identity confirmed'
               + (' (<a href="{u}" target="_blank">direct opinion '
                  'link</a>)'.format(u=esc(_op_url)) if _op_url else ''))
        qrow = checks["quote"]
        _pin = (getattr(r.citation, "pincite", "") or "").strip()
        if qrow["status"] == "verified":
            qb = "quote verbatim" + (" at " + esc(_pin)
                                     if _pin and getattr(r, "pincite_found",
                                                         None) else "")
        elif qrow["status"] in ("verified_alterations", "verified_licensed"):
            qb = "quote verified (permitted alterations)"
        else:
            qb = "no quote to check"
        trow = checks["treatment"]
        tb = ("no negative-treatment signals"
              if trow["status"] == "clean" else "treatment not checked")
        body.append('<p class="sr" style="margin:.4em 0 0">'
                    '<span class="ok">✓ All four checks pass.</span> '
                    + idb + ' · ' + qb + ' · supported · ' + tb + '</p>')
    else:
        # Pincite meta line (kept verbatim label; markdown carries the
        # gate-asserted copy).
        _pin = (getattr(r.citation, "pincite", "") or "").strip()
        if _pin:
            _pf = getattr(r, "pincite_found", None)
            if _pf is True:
                _ps = ' <span class="ok">— located in opinion</span>'
            elif _pf is False:
                _note = (getattr(r, "pincite_note", "") or "").strip()
                _ps = (' <span style="color:var(--t2)">— '
                       + esc(_note if _note else "not located on source")
                       + '</span>')
            else:
                _ps = ''
            body.append('<div class="note"><b>Pincite (from brief):</b> p. '
                        + esc(_pin) + _ps + '</div>')
        rows = [
            _row_html(checks["identity"],
                      _identity_extra(r, checks["identity"])),
            _row_html(checks["quote"]),
            _row_html(checks["support"]),
            _row_html(checks["treatment"],
                      _treatment_extra(checks.get("_tentry"))),
        ]
        body.append('<table class="checks">' + "".join(rows) + '</table>')
        # Failing-axis evidence only (F1): passage + second opinion on
        # Support rows that need a human eye.
        if checks["support"]["tier"] in (sev.TIER_DEFECT, sev.TIER_REVIEW):
            if (r.passage or "").strip():
                body.append('<div class="prop"><b>Extracted passage:</b> '
                            '<i>' + esc(sev.tidy_passage(r.passage, 480))
                            + '</i></div>')
            _ppage = (getattr(r, "passage_page", "") or "").strip()
            _pnf = checks["support"]["status"] == "page_not_found"
            if _ppage == "footnotes":
                body.append('<div class="note"><b>Supporting passage '
                            'located:</b> in the opinion\'s footnotes</div>')
            elif _ppage and _pnf:
                # Pair the located page prominently with the PAGE NOT FOUND
                # finding so the reviewer sees where support actually sits
                # (B8 Cit 14, 2026.07.14).
                body.append('<div class="pagehit"><b>Supporting passage '
                            'located at: *' + esc(_ppage) + '</b> &mdash; the '
                            'proposition\'s support appears on this page, not '
                            'the cited pincite. Confirm the pincite.</div>')
            elif _ppage:
                body.append('<div class="note"><b>Supporting passage '
                            'located at:</b> *' + esc(_ppage) + '</div>')
            _so = (getattr(r, "second_opinion", "") or "").strip()
            if _so:
                _sos = getattr(r, "second_opinion_score", None)
                _tail = (" (answer strength %.2f)" % _sos
                         if _sos is not None else "")
                body.append('<div class="note"><b>Second opinion (Answer '
                            'Extractor):</b> ' + esc(_so) + esc(_tail)
                            + '</div>')
        _vn = _verification_note(r)
        if _vn:
            body.append('<div class="note" style="color:#0B6E4F;'
                        'font-weight:600"><b>Manual verification '
                        '(Step 6.6):</b> ' + esc(_vn) + '</div>')
        _sd = (getattr(r, "search_detail", "") or "").strip()
        if not r.opinion_resolved and _sd:
            body.append('<div class="note"><b>Search attempted:</b> '
                        + esc(_sd) + '</div>')
        elif _sd and ("STATUTE CHECK" in _sd
                      or "Patched from free source" in _sd):
            # Session E (2026.07.30): surface the source-provenance note
            # (SCOTX/COA/Business Court builder patch, and the I3 statute
            # check) on RESOLVED cards too -- otherwise it renders only when
            # the opinion is unresolved and is lost on the cards it describes.
            body.append('<div class="note"><b>Source note:</b> '
                        + esc(_sd) + '</div>')
        if (r.notes or "").strip():
            body.append('<div class="note">' + esc(r.notes) + '</div>')

    body.extend(nested_blocks)
    body_html = "\n".join(b for b in body if b)

    if tier == sev.TIER_CRITICAL:
        chip_label, evid = _crit_banner(r, checks)
        return ('<div class="card vb crit">'
                '<div class="hdr"><span class="cnum">{cnum}</span>'
                '<span class="ccite">{cite}</span>'
                '<span class="chip" style="background:#fff;color:var(--t1)">'
                '{chip}</span></div>'
                '<div class="cbody">'
                '<div class="evid">{evid}</div>\n{body}</div>'
                '</div>').format(cnum=esc(cnum), cite=esc(headline),
                                 chip=esc(chip_label), evid=evid,
                                 body=body_html)
    return ('<div class="card"><div class="band" style="background:{hex}">'
            '</div><div class="cbody">'
            '<div class="chead"><span class="cnum">{cnum}</span>'
            '<span class="ccite">{cite}</span>'
            '<span class="chip t{t}">{lab}</span></div>\n{body}</div>'
            '</div>').format(hex=sev.TIER_HEX[tier], cnum=esc(cnum),
                             cite=esc(headline), t=tier,
                             lab=esc(sev.TIER_LABEL[tier]), body=body_html)


def _not_checked_reason(results, entry):
    """Run-accurate reason a case's TREATMENT could not be checked, derived
    from the citation's own resolution state so the text matches the run
    (B8 Cit 3 fix -- Doe DID resolve to a cluster, 2026.07.14)."""
    idxs = entry.get("instance_indexes") or []
    r = results[idxs[0]] if idxs and idxs[0] < len(results) else None
    if r is not None:
        src = (getattr(r, "opinion_source", "") or "").lower()
        op_url = getattr(r, "opinion_url", "") or ""
        if "recap" in src or "pacer" in src:
            return ("retrieved as a RECAP/PACER docket, which carries no "
                    "citation graph to scan for treatment")
        if op_url and "/opinion/" in op_url and getattr(
                r, "lookup_status", None) == 404:
            return ("opinion located by case-name search (the reporter cite "
                    "is not indexed on CourtListener), so no treatment "
                    "citation graph was retrieved")
    return entry.get("reason", "") or "treatment citation graph not retrieved"


def _not_checked_html(results, meta):
    tr = (meta or {}).get("treatment") or {}
    ncx = tr.get("not_checked") or []
    if not ncx:
        return ""
    esc = html.escape
    items = []
    for e in ncx:
        items.append('<li><b>{nm}</b>{rc} &mdash; {rs}</li>'.format(
            nm=esc(e.get("name", "")),
            rc=(', ' + esc(e["reporter_cite"]) if e.get("reporter_cite")
                else ''),
            rs=esc(_not_checked_reason(results, e))))
    return ('<div class="notchecked"><h2>&#9888; Not checked ({n})</h2>'
            '<p class="sr">Treatment (good-law) could not be evaluated for '
            'these authorities from free sources. Confirm them on Westlaw or '
            'Lexis before relying on them.</p><ul>' + "\n".join(items)
            + '</ul></div>').format(n=len(ncx))


def render_html(
    results: Sequence[CiteCheckResult],
    meta: dict | None = None,
) -> str:
    """Tabbed, severity-first HTML cite-check report (Phase 8, 2026.07.15).

    Triage tab: 5-tile verdict board + action-grouped summary + problem
    cards (tiers 1-3). All-citations tab: every card. Methodology tab: the
    status key, not-checked reasons, TOA coverage, treatment evidence, and
    non-case references."""
    import time as _time
    meta = meta or {}
    esc = html.escape

    _attach_display_captions(results)
    checks_map, nested_children, nested_set, counts, order = _severity_model(
        results, meta)

    # Meta line.
    pieces = []
    head_bits = " — ".join(x for x in (meta.get("matter"),
                                       meta.get("document_name")) if x)
    if head_bits:
        pieces.append(esc(head_bits))
    pieces.append("checked " + _time.strftime("%Y.%m.%d"))
    pieces.append("{} citations".format(len(results)))
    if meta.get("jurisdiction"):
        pieces.append("jurisdiction " + esc(str(meta["jurisdiction"])))
    meta_line = " · ".join(pieces)

    # 5-tile verdict board (the attorney 2026.07.15, option B).
    tile_ncolor = {1: "var(--t1)", 2: "var(--tu)", 3: "var(--t3)",
                   4: "#9A7508", 5: "var(--t5)"}
    tile_lab = {1: "Critical", 2: "Unverified", 3: "Fix", 4: "Review",
                5: "Clean"}
    tiles = []
    for t in (1, 2, 3, 4, 5):
        tiles.append(
            '<div class="tile"><div class="cap" style="background:{h}">'
            '</div><div class="body"><div class="n" style="color:{nc}">{n}'
            '</div><div class="lab">{lab}</div></div></div>'.format(
                h=sev.TIER_HEX[t], nc=tile_ncolor[t], n=counts[t],
                lab=tile_lab[t]))
    board = '<div class="board">' + "".join(tiles) + '</div>'

    # Action-grouped summary (the attorney 2026.07.15): a to-do list, worst first.
    group_head = {
        1: ("Critical — fix before filing", "var(--t1)"),
        2: ("Could not check — verify on Westlaw or Lexis", "var(--tu)"),
        3: ("Fix before filing", "var(--t3)"),
        4: ("Review by hand", "#9A7508"),
    }
    groups = []
    for t in (1, 2, 3, 4):
        items = [i for i in order
                 if checks_map[i]["tier"] == t and i not in nested_set]
        if not items:
            continue
        head, color = group_head[t]
        lines = []
        for i in items:
            reason = sev.offender_reason(checks_map[i])
            lines.append(
                '<p><a href="#" onclick="jumpTo(\'card-{n}\');return false">'
                '<span class="cn" style="color:{c}">Citation {n}</span></a>'
                ' — <i>{nm}</i> — {why}</p>'.format(
                    n=i + 1, c=color, nm=esc(_short_case(results[i])),
                    why=esc(reason)))
        groups.append('<div class="actgroup"><div class="ghead" '
                      'style="color:{c}">{head} ({k})</div>{lines}'
                      '</div>'.format(c=color, head=esc(head), k=len(items),
                                      lines="".join(lines)))
    n_clean = counts[5]
    if n_clean:
        groups.append('<div class="cleanline">The remaining {k} citations '
                      'pass all four checks (identity, quote, support, '
                      'treatment). Full cards are on the All-citations tab.'
                      '</div>'.format(k=n_clean))
    action_groups = "\n".join(groups)

    # Cards. Triage tab carries tiers 1-3 only; All tab carries everything.
    def _cards(ids, id_prefix):
        out = []
        for i in ids:
            blocks = [_nested_html(j + 1, results[j], checks_map[i])
                      for j in sorted(nested_children.get(i, []))]
            card = _card_html(i + 1, results[i], checks_map[i], blocks)
            out.append('<div id="{p}card-{n}">{c}</div>'.format(
                p=id_prefix, n=i + 1, c=card))
        return "\n".join(out)

    triage_ids = [i for i in order
                  if checks_map[i]["tier"] in (1, 2, 3) and i not in nested_set]
    triage_cards = _cards(triage_ids, "t")
    if triage_ids:
        triage_cards = "<h2>Problem citations</h2>" + triage_cards
    cards_html = _cards(order, "")

    # Status key (Methodology tab).
    key_items = [
        (1, "Tier 1 — Critical (sanction risk):", "fabricated quotation "
         "confirmed against the complete opinion; wrong case; cited as "
         "supporting when the opinion holds the opposite."),
        (2, "Tier 2 — Unverified (check on Westlaw or Lexis):", "the tool "
         "could not check this authority at all — not found in any free "
         "database, wrong document retrieved (coverage gap), or identity "
         "unconfirmable. A case nobody checked outranks a weak-support "
         "flag."),
        (3, "Tier 3 — Fix (before filing):", "cited page does not support "
         "the proposition; material misquote (see the word diff on the "
         "card)."),
        (4, "Tier 4 — Review (confirm by hand):", "moderate support; "
         "pincite page not found; unconfirmed negative-treatment or "
         "caution signal; quote not confirmable against a partial copy."),
        (5, "Tier 5 — Pass:", "correct case, quote exact or permissibly "
         "altered, page supports, no negative-treatment signals."),
    ]
    key_rows = ['<div class="keyrow"><span class="dot" style="background:'
                '{h}"></span><span><b>{head}</b> {txt}</span></div>'.format(
                    h=sev.TIER_HEX[t], head=esc(head), txt=esc(txt))
                for t, head, txt in key_items]
    status_key = ('<h2>Status key</h2><div class="offenders">'
                  + "\n".join(key_rows)
                  + '<p class="sr" style="margin-top:10px">Every citation '
                    'runs four independent checks — Identity, Quote, '
                    'Support, Treatment. The card\'s tier is the worst '
                    'failing check. Treatment rows present evidence only; '
                    'the coverage sentence is the ceiling of the claim. '
                    'Pipeline: eyecite + CourtListener + Isaacus.</p>'
                    '</div>')

    # Methodology-tab sections: TOA coverage, treatment evidence, non-case.
    toa_only = meta.get("toa_only_cases") or []
    body_only = _body_only_display(results, meta)
    toa_cov_html = ""
    if toa_only or body_only:
        parts = [
            '<div style="margin-top:2rem;padding-top:1rem;'
            'border-top:1px solid var(--rule)">',
            '<h2>TOA Coverage</h2>',
            '<p class="sr">Cross-check between the Table of Authorities '
            'and the citations actually extracted from the body.</p>',
        ]
        if body_only:
            parts.append('<h3 style="font-family:'
                         'Georgia,serif;color:var(--t3);font-size:1rem">'
                         'Cited in body, missing from TOA ('
                         + str(len(body_only)) + ')</h3>')
            parts.append('<ul style="font-size:.92rem">')
            for c in body_only:
                name = esc(c.get("name", ""))
                prop = esc((c.get("proposition") or "").strip())
                parts.append('<li><b>' + name + '</b>'
                             + (' <span class="sr">— <i>' + prop
                                + '…</i></span>' if prop else '')
                             + '</li>')
            parts.append('</ul>')
        if toa_only:
            parts.append('<h3 style="font-family:'
                         'Georgia,serif;color:var(--partial);'
                         'font-size:1rem">Listed in TOA, not cited in body ('
                         + str(len(toa_only)) + ')</h3>')
            parts.append('<ul style="font-size:.92rem">')
            for entry in toa_only:
                name = esc(entry.get("name", ""))
                cite = esc(entry.get("cite", ""))
                pages = esc(", ".join(entry.get("pages") or []))
                parts.append('<li><b>' + name + '</b>'
                             + (', ' + cite if cite else '')
                             + (' <span class="sr">(TOA pp. ' + pages
                                + ')</span>' if pages else '')
                             + '</li>')
            parts.append('</ul>')
        parts.append('</div>')
        toa_cov_html = "\n".join(parts)

    non_case = meta.get("non_case_references") or []
    nc_html = ""
    if non_case:
        nc_items = []
        for ref in non_case:
            name = esc(ref.get("name", ""))
            rtype = esc(ref.get("type", "reference"))
            nc_items.append('<li>' + name + ' <span class="sr">(' + rtype
                            + ')</span></li>')
        nc_html = ('<div style="margin-top:2rem;padding-top:1rem;'
                   'border-top:1px solid var(--rule)">'
                   '<h2>Non-Case References Identified</h2>'
                   '<p class="sr">The following references were identified '
                   'in the brief but are not court opinions. They were not '
                   'verified by this tool.</p>'
                   '<ul style="font-size:.92rem">' + "\n".join(nc_items)
                   + '</ul></div>')

    sections = toa_cov_html + _render_treatment_html(meta) + nc_html

    not_checked_html = _not_checked_html(results, meta)

    out = _HTML_TEMPLATE
    for token, value in (
        ("@@META_LINE@@", meta_line),
        ("@@BOARD@@", board),
        ("@@ACTION_GROUPS@@", action_groups),
        ("@@TRIAGE_CARDS@@", triage_cards),
        ("@@CARDS@@", cards_html),
        ("@@NOT_CHECKED@@", not_checked_html),
        ("@@STATUS_KEY@@", status_key),
        ("@@SECTIONS@@", sections),
    ):
        out = out.replace(token, value)
    return out


# --------------------------------------------------------------------------
# DOCX renderer (via docx-js / Node.js)
# --------------------------------------------------------------------------

_DOCX_BUILD_SCRIPT = r"""
// cite_check_report_docx_build.js -- Generated by cite_check_report.py
// Phase 4 (G9, 2026.07.14): severity-first scoreboard + four-row cards,
// mirroring the Phase 3 HTML redesign. Consumes a PRE-COMPUTED model
// (tiers/rows from cc_severity in Python); this script only lays it out.
const fs = require("fs");
const {
    Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
    Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
    ShadingType, PageNumber, PageBreak, ExternalHyperlink, TabStopType,
    TabStopPosition, VerticalAlign,
} = require("docx");

const dataPath = process.argv[2];
const outPath = process.argv[3];
const data = JSON.parse(fs.readFileSync(dataPath, "utf-8"));

// Fonts: neutral serif stack throughout.
const FB = "Palatino Linotype";  // body
const FH = "Georgia";           // headings / title
const INK = "1F2A2E";
const MUTED = "5B6B70";
const TEAL = "0E5A5A";
const WHITE = "FFFFFF";
const RULE = "D8E0E0";

// Phase 8 (2026.07.15): five tiers -- 2 = UNVERIFIED (purple).
const TIER_HEX = { 1: "B00020", 2: "534AB7", 3: "E8870E", 4: "F5C518", 5: "0FA685" };
const TIER_BG  = { 1: "FBEAEC", 2: "EEEDFE", 3: "FDF1E2", 4: "FBF6E3", 5: "E6F4EF" };
// Number color for the amber tile reads better a shade darker than the band.
const TIER_NCOLOR = { 1: "B00020", 2: "534AB7", 3: "E8870E", 4: "9A7508", 5: "0FA685" };
const CHIP = {
    t1: { bg: "B00020", fg: "FFFFFF" },
    tu: { bg: "534AB7", fg: "FFFFFF" },
    t2: { bg: "534AB7", fg: "FFFFFF" },
    t3: { bg: "E8870E", fg: "FFFFFF" },
    t4: { bg: "F5C518", fg: "3D2F00" },
    t5: { bg: "0FA685", fg: "FFFFFF" },
    na: { bg: "B9C4C6", fg: "33403F" },
};

// Legacy 11-verdict label map, kept in sync with Python _VERDICT_ORDER as a
// SECONDARY label only (the four-check tiers are the primary surface now).
const verdictLabels = {
    verified: "VERIFIED", somewhat: "SOMEWHAT SUPPORTS",
    pincite_unconfirmed: "SUPPORTED · PAGE UNVERIFIED",
    prop_not_extracted: "PROPOSITION NOT EXTRACTED",
    partial: "INDETERMINATE – TEXT UNAVAILABLE", flagged: "FLAGGED",
    applied_rule: "APPLIED RULE — VERIFIED ELSEWHERE",
    cited_to_distinguish: "CITED TO DISTINGUISH — REVIEW",
    does_not_support: "DOES NOT SUPPORT", cited_as_contrary: "CITED AS CONTRARY",
    identity_unconfirmed: "IDENTITY UNCONFIRMED",
    pincite_not_found: "PINCITE NOT FOUND", unable: "UNABLE TO VERIFY",
};
const verdictOrder = ["verified", "somewhat", "pincite_unconfirmed", "partial", "flagged", "applied_rule", "cited_to_distinguish", "does_not_support", "cited_as_contrary", "identity_unconfirmed", "pincite_not_found", "prop_not_extracted", "unable"];  // kept in sync with Python _VERDICT_ORDER

function tRun(text, opt) {
    opt = opt || {};
    return new TextRun({
        text: text, font: opt.font || FB, size: opt.size || 20,
        bold: !!opt.bold, italics: !!opt.italics,
        color: opt.color || INK, allCaps: !!opt.allCaps,
        smallCaps: !!opt.smallCaps,
    });
}
function link(url, label, opt) {
    opt = opt || {};
    return new ExternalHyperlink({
        link: url,
        children: [new TextRun({ text: label, font: opt.font || FB,
            size: opt.size || 20, style: "Hyperlink" })],
    });
}
function thin(color) {
    return { style: BorderStyle.SINGLE, size: 1, color: color || RULE };
}
function noBorders() {
    return { top: { style: BorderStyle.NONE }, bottom: { style: BorderStyle.NONE },
        left: { style: BorderStyle.NONE }, right: { style: BorderStyle.NONE } };
}

const children = [];

// ---- Title ----
children.push(new Paragraph({
    children: [tRun("Cite-Check Report", { font: FH, size: 34, bold: true, color: TEAL })],
    spacing: { after: 60 },
}));
children.push(new Paragraph({
    children: [tRun(data.meta_line, { size: 18, color: MUTED })],
    spacing: { after: 200 },
}));

// ---- Scoreboard: one row of four tiles ----
function tile(t) {
    const count = data.scoreboard[String(t)] || 0;
    return new TableCell({
        width: { size: 1872, type: WidthType.DXA },
        margins: { top: 80, bottom: 100, left: 120, right: 120 },
        shading: { fill: TIER_BG[t], type: ShadingType.CLEAR, color: "auto" },
        borders: { top: { style: BorderStyle.SINGLE, size: 18, color: TIER_HEX[t] },
            bottom: thin(RULE), left: thin(RULE), right: thin(RULE) },
        children: [
            new Paragraph({ spacing: { after: 20 },
                children: [tRun(String(count), { font: FH, size: 40, bold: true, color: TIER_NCOLOR[t] })] }),
            new Paragraph({ children: [tRun(data.tier_label[String(t)], { size: 16, bold: true, color: MUTED, allCaps: true })] }),
            new Paragraph({ children: [tRun(data.tier_sub[String(t)], { size: 15, color: MUTED, italics: true })] }),
        ],
    });
}
children.push(new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [1872, 1872, 1872, 1872, 1872],
    borders: noBorders(),
    rows: [new TableRow({ children: [tile(1), tile(2), tile(3), tile(4), tile(5)] })],
}));
children.push(new Paragraph({ spacing: { after: 160 }, children: [] }));

// ---- Offender lists ----
function offenderLine(o, hex, icon) {
    return new Paragraph({
        spacing: { after: 40 },
        children: [
            tRun(icon + " Citation " + o.n, { bold: true, color: hex }),
            tRun("  —  ", { color: MUTED }),
            tRun(o.name, { italics: true }),
            tRun("  —  ", { color: MUTED }),
            tRun(o.reason, { bold: true }),
        ],
    });
}
const offC = data.offenders.critical || [];
const offU = data.offenders.unverified || [];
const offD = data.offenders.defect || [];
const offR = data.offenders.review || [];
if (offC.length || offU.length || offD.length || offR.length) {
    for (const o of offC) children.push(offenderLine(o, TIER_HEX[1], "⛔"));
    for (const o of offU) children.push(offenderLine(o, TIER_HEX[2], "◆"));
    for (const o of offD) children.push(offenderLine(o, TIER_HEX[3], "▲"));
    for (const o of offR) {
        children.push(new Paragraph({ spacing: { after: 40 },
            children: [ tRun("● Citation " + o.n, { bold: true, color: "9A7508" }),
                tRun("  —  ", { color: MUTED }), tRun(o.name, { italics: true }),
                tRun("  —  ", { color: MUTED }), tRun(o.reason, {}) ] }));
    }
    children.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}

// ---- Citations heading ----
children.push(new Paragraph({
    children: [tRun("Citations", { font: FH, size: 26, bold: true, color: TEAL })],
    spacing: { before: 120, after: 120 },
}));

// ---- Card renderer ----
function chipRun(row) {
    const cc = CHIP[row.chip_cls] || CHIP.na;
    // A shaded run is not available; render the chip as a bracketed, bold,
    // tier-colored label so the axis status still reads at a glance.
    return tRun("[ " + row.chip + " ]", { bold: true, size: 18, color: (row.chip_cls === "t4" || row.chip_cls === "na") ? "3D2F00" : cc.bg });
}
function rowParagraphs(row) {
    const paras = [];
    let whyColor = INK;
    if (row.tier === 5) whyColor = "0B7A62";
    else if (row.tier === 1) whyColor = TIER_HEX[1];
    const runs = [
        tRun(row.axis.toUpperCase() + "  ", { size: 16, bold: true, color: MUTED }),
        chipRun(row),
        tRun("  " + row.text, { color: whyColor, bold: row.tier === 1 }),
    ];
    if (row.link_url) {
        if (row.link_prefix) runs.push(tRun(" " + row.link_prefix, { color: whyColor }));
        else runs.push(tRun(" ", {}));
        runs.push(link(row.link_url, row.link_label));
        runs.push(tRun(".", { color: whyColor }));
    }
    if (row.note) runs.push(tRun(" " + row.note, { size: 17, color: MUTED }));
    paras.push(new Paragraph({ spacing: { before: 40, after: 40 }, indent: { left: 60 }, children: runs }));
    // Phase 8: misquote word diff -- flagged words bold red.
    if (row.diff && row.diff.brief && row.diff.opinion) {
        function diffRuns(tokens) {
            const rs = [];
            for (const t of tokens) {
                rs.push(tRun(t[0] + " ", t[1] ? { size: 18, bold: true, color: TIER_HEX[1], italics: true } : { size: 18, italics: true, color: "37474C" }));
            }
            return rs;
        }
        paras.push(new Paragraph({ spacing: { before: 40, after: 20 }, indent: { left: 240 },
            children: [tRun("The brief quotes:  ", { size: 16, bold: true, color: MUTED, allCaps: true })].concat(diffRuns(row.diff.brief)) }));
        paras.push(new Paragraph({ spacing: { after: 60 }, indent: { left: 240 },
            children: [tRun("The opinion reads (red = differs):  ", { size: 16, bold: true, color: MUTED, allCaps: true })].concat(diffRuns(row.diff.opinion)) }));
    }
    return paras;
}
function headerParagraph(card) {
    const t = card.tier;
    const dark = (t === 4);
    const fg = dark ? "3D2F00" : WHITE;
    const runs = [
        tRun((card.icon ? card.icon + " " : "") + "Citation " + card.n, { font: FH, bold: true, size: 22, color: fg }),
        tRun("   " + card.headline, { italics: true, size: 20, color: dark ? "3D2F00" : "F2F6F6" }),
        tRun("   " + card.chip_label, { bold: true, size: 16, color: fg, allCaps: true }),
    ];
    return new Paragraph({
        shading: { fill: TIER_HEX[t], type: ShadingType.CLEAR, color: "auto" },
        spacing: { before: 60, after: 60 }, children: runs,
    });
}
function bodyParagraphs(card) {
    const body = [];
    if (card.authority) {
        body.push(new Paragraph({ spacing: { after: 40 },
            children: [tRun("Authority: ", { size: 17, bold: true, color: MUTED }), tRun(card.authority, { size: 17, color: MUTED })] }));
    }
    if (card.proposition) {
        const pr = [tRun("Proposition (from brief): ", { size: 19, bold: true, color: TEAL }), tRun(card.proposition, { size: 19, italics: true, color: "37474C" })];
        if (card.proposition_source === "agent") pr.push(tRun(" Proposition identified by Claude review of the brief paragraph.", { size: 17, color: MUTED }));
        body.push(new Paragraph({ spacing: { after: 80 }, indent: { left: 120 }, children: pr }));
    } else if (card.proposition_review) {
        body.push(new Paragraph({ spacing: { after: 80 }, indent: { left: 120 },
            children: [tRun("Proposition (from brief): ", { size: 19, bold: true, color: TEAL }), tRun("No verifiable proposition extracted — review required.", { size: 19, italics: true, color: "B00020" })] }));
    }

    if (card.collapse) {
        const runs = [tRun("✓ All four checks pass. ", { bold: true, color: "0B7A62" }), tRun(card.collapse_idb, {})];
        if (card.collapse_op_url) { runs.push(tRun(" (", {})); runs.push(link(card.collapse_op_url, "direct opinion link")); runs.push(tRun(")", {})); }
        runs.push(tRun(" · " + card.collapse_qb + " · supported · " + card.collapse_tb + ".", {}));
        body.push(new Paragraph({ spacing: { after: 40 }, children: runs }));
    } else {

    // Tier-1 Variant B: lead with the fabricated / wrong-case / contrary note.
    if (card.tier === 1 && (card.crit_lead || card.crit_rest)) {
        body.push(new Paragraph({
            shading: { fill: TIER_BG[1], type: ShadingType.CLEAR, color: "auto" },
            border: { left: { style: BorderStyle.SINGLE, size: 18, color: TIER_HEX[1] } },
            spacing: { before: 40, after: 80 }, indent: { left: 120 },
            children: [ tRun(card.crit_lead, { bold: true, color: TIER_HEX[1] }), tRun(card.crit_rest, { color: INK }) ],
        }));
    }

    // Pincite meta line.
    if (card.pincite) {
        let tail = "", pcolor = MUTED;
        if (card.pincite_found === true) { tail = " — located in opinion"; pcolor = "0B7A62"; }
        else if (card.pincite_found === false) { tail = " — " + (card.pincite_note || "not located on source"); pcolor = "E8870E"; }
        body.push(new Paragraph({ spacing: { after: 40 },
            children: [tRun("Pincite (from brief): ", { size: 17, bold: true, color: MUTED }), tRun("p. " + card.pincite + tail, { size: 17, color: pcolor })] }));
    }

    // Four check rows.
    for (const row of card.rows) for (const p of rowParagraphs(row)) body.push(p);

    // Failing-axis Support evidence.
    if (card.support_passage) {
        body.push(new Paragraph({ spacing: { before: 60, after: 40 }, indent: { left: 120 },
            children: [tRun("Extracted passage: ", { size: 18, bold: true, color: TEAL }), tRun(card.support_passage, { size: 18, italics: true, color: "37474C" })] }));
    }
    if (card.passage_page) {
        if (card.passage_page !== "footnotes" && card.support_page_not_found) {
            body.push(new Paragraph({
                shading: { fill: TIER_BG[4], type: ShadingType.CLEAR, color: "auto" },
                border: { left: { style: BorderStyle.SINGLE, size: 18, color: TIER_HEX[4] } },
                spacing: { before: 40, after: 60 }, indent: { left: 120 },
                children: [tRun("Supporting passage located at: *" + card.passage_page, { size: 18, bold: true, color: "5A4600" }),
                    tRun(" — the proposition's support appears on this page, not the cited pincite. Confirm the pincite.", { size: 17, color: "5A4600" })] }));
        } else {
            const ptxt = (card.passage_page === "footnotes") ? "in the opinion's footnotes" : ("*" + card.passage_page);
            const plab = (card.passage_page === "footnotes") ? "Supporting passage located: " : "Supporting passage located at: ";
            body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 120 },
                children: [tRun(plab, { size: 17, bold: true, color: MUTED }), tRun(ptxt, { size: 17, color: MUTED })] }));
        }
    }
    if (card.second_opinion) {
        const sot = (card.second_opinion_score !== null && card.second_opinion_score !== undefined) ? (" (answer strength " + card.second_opinion_score.toFixed(2) + ")") : "";
        body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 120 },
            children: [tRun("Second opinion (Answer Extractor): ", { size: 17, bold: true, color: MUTED }), tRun(card.second_opinion + sot, { size: 17, italics: true, color: MUTED })] }));
    }
    if (card.verification_note) {
        body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 120 },
            children: [tRun("Manual verification (Step 6.6): ", { size: 17, bold: true, color: "0B6E4F" }), tRun(card.verification_note, { size: 17, color: "0B6E4F" })] }));
    }
    if (card.search_detail) {
        body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 120 },
            children: [tRun("Search attempted: ", { size: 17, bold: true, color: MUTED }), tRun(card.search_detail, { size: 17, color: MUTED })] }));
    }
    if (card.notes) {
        body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 120 },
            children: [tRun(card.notes, { size: 17, italics: true, color: MUTED })] }));
    }

    }

    // Nested quoted-source sub-blocks.
    for (const nb of (card.nested || [])) {
        body.push(new Paragraph({
            shading: { fill: "34595F", type: ShadingType.CLEAR, color: "auto" },
            border: { left: { style: BorderStyle.SINGLE, size: 18, color: "1F3D42" } },
            spacing: { before: 80, after: 0 }, indent: { left: 180 },
            children: [ tRun("QUOTED SOURCE · CITATION " + nb.n, { size: 17, bold: true, color: "FFFFFF", allCaps: true }) ] }));
        const runs = [];
        if (nb.head) runs.push(tRun(nb.head + " ", { size: 19, bold: true, italics: true }));
        runs.push(tRun("— appears only inside the parent's “(" + nb.kind + " …)” parenthetical.", { size: 18 }));
        body.push(new Paragraph({
            shading: { fill: "EEF5F5", type: ShadingType.CLEAR, color: "auto" },
            border: { left: { style: BorderStyle.SINGLE, size: 18, color: "4E7076" } },
            spacing: { before: 0, after: 20 }, indent: { left: 180 }, children: runs }));
        const sr = [];
        if (nb.resolved) {
            sr.push(tRun("✓ Source exists. ", { size: 17, color: "0B7A62" }));
            if (nb.url) { sr.push(tRun("(", { size: 17 })); sr.push(link(nb.url, "direct opinion link", { size: 17 })); sr.push(tRun(") ", { size: 17 })); }
            sr.push(tRun(nb.tail, { size: 17, color: MUTED }));
        } else {
            sr.push(tRun("Source could not be retrieved from free databases — confirm it by hand. Not scored as a standalone citation.", { size: 17, color: MUTED }));
        }
        body.push(new Paragraph({ spacing: { after: 40 }, indent: { left: 180 }, children: sr }));
    }
    return body;
}
function buildCard(card) {
    const cellChildren = [headerParagraph(card)].concat(bodyParagraphs(card));
    const bandSize = (card.tier === 1) ? 30 : 18;
    return new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [9360],
        rows: [new TableRow({ children: [new TableCell({
            margins: { top: 40, bottom: 80, left: 160, right: 160 },
            borders: { left: { style: BorderStyle.SINGLE, size: bandSize, color: TIER_HEX[card.tier] },
                top: thin(RULE), right: thin(RULE), bottom: thin(RULE) },
            children: cellChildren,
        })] })],
    });
}
for (const card of data.cards) {
    children.push(buildCard(card));
    children.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}

// ---- Status key ----
children.push(new Paragraph({
    children: [tRun("Status key", { font: FH, size: 24, bold: true, color: TEAL })],
    spacing: { before: 240, after: 120 },
}));
for (const k of data.status_key) {
    children.push(new Paragraph({ spacing: { after: 60 }, indent: { left: 120 },
        children: [tRun(k.head + " ", { bold: true, color: TIER_HEX[String(k.tier)] }), tRun(k.text, { color: INK })] }));
}
children.push(new Paragraph({ spacing: { before: 80 }, indent: { left: 120 },
    children: [tRun("Every citation runs four independent checks — Identity, Quote, Support, Treatment. The card's tier is the worst failing check. Treatment rows present evidence only; the coverage sentence is the ceiling of the claim.", { size: 17, color: MUTED })] }));

// ---- TOA Coverage ----
const toaOnly = data.toa_only_cases || [];
const bodyOnly = data.body_only_cases || [];
if (toaOnly.length > 0 || bodyOnly.length > 0) {
    children.push(new Paragraph({ children: [tRun("TOA Coverage", { font: FH, size: 24, bold: true, color: TEAL })], spacing: { before: 300, after: 100 } }));
    children.push(new Paragraph({ children: [tRun("Cross-check between the Table of Authorities and the citations actually extracted from the body.", { size: 18, italics: true, color: MUTED })], spacing: { after: 120 } }));
    if (bodyOnly.length > 0) {
        children.push(new Paragraph({ children: [tRun("Cited in body, missing from TOA (" + bodyOnly.length + ")", { size: 20, bold: true, color: "E8870E" })], spacing: { before: 120, after: 80 } }));
        for (const c of bodyOnly) {
            const runs = [tRun(c.name, { size: 18, bold: true })];
            if (c.proposition) runs.push(tRun(" — " + c.proposition + "…", { size: 18, italics: true, color: MUTED }));
            children.push(new Paragraph({ children: runs, indent: { left: 360 }, spacing: { after: 40 } }));
        }
    }
    if (toaOnly.length > 0) {
        children.push(new Paragraph({ children: [tRun("Listed in TOA, not cited in body (" + toaOnly.length + ")", { size: 20, bold: true, color: "2E8BC0" })], spacing: { before: 120, after: 80 } }));
        for (const entry of toaOnly) {
            const pages = (entry.pages || []).join(", ");
            const runs = [tRun(entry.name, { size: 18, bold: true })];
            if (entry.cite) runs.push(tRun(", " + entry.cite, { size: 18 }));
            if (pages) runs.push(tRun("  (TOA pp. " + pages + ")", { size: 18, color: MUTED }));
            children.push(new Paragraph({ children: runs, indent: { left: 360 }, spacing: { after: 40 } }));
        }
    }
}

// ---- Non-case references ----
if (data.non_case_references && data.non_case_references.length > 0) {
    children.push(new Paragraph({ children: [tRun("Non-Case References Identified", { font: FH, size: 24, bold: true, color: TEAL })], spacing: { before: 300, after: 100 } }));
    children.push(new Paragraph({ children: [tRun("The following references were identified in the brief but are not court opinions. They were not verified by this tool.", { size: 18, italics: true, color: MUTED })], spacing: { after: 120 } }));
    for (const ref of data.non_case_references) {
        children.push(new Paragraph({ indent: { left: 360 }, spacing: { after: 40 },
            children: [tRun(ref.name, { size: 18 }), tRun(" (" + ref.type + ")", { size: 18, color: MUTED })] }));
    }
}

// ---- Document ----
const docTitle = "Cite-Check Report" + (data.meta.document_name ? " - " + data.meta.document_name : "");
const doc = new Document({
    styles: {
        default: { document: { run: { font: FB, size: 20 } } },
        paragraphStyles: [
            { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
                run: { size: 34, bold: true, font: FH }, paragraph: { spacing: { before: 200, after: 120 }, keepNext: true } },
        ],
    },
    sections: [{
        properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
        headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
            children: [tRun(docTitle, { size: 16, color: "888888", smallCaps: true, bold: true })] })] }) },
        footers: { default: new Footer({ children: [new Paragraph({
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [ tRun(docTitle, { size: 16, color: "888888", smallCaps: true, bold: true }),
                new TextRun({ children: ["\t"], font: FB }),
                tRun("Page ", { size: 16, color: "888888" }),
                new TextRun({ children: [PageNumber.CURRENT], font: FB, size: 16, color: "888888" }) ] })] }) },
        children: children,
    }],
});
Packer.toBuffer(doc).then(buffer => { fs.writeFileSync(outPath, buffer); });
"""


# --------------------------------------------------------------------------
# Severity model shared by the DOCX renderer (mirrors render_html's compute).
# --------------------------------------------------------------------------
def _severity_model(results, meta):
    """(checks_map, nested_children, nested_set, counts, order) computed from
    cc_severity -- the single source of tier truth for the DOCX renderer."""
    by_index, tentries = _treatment_maps(meta)
    checks_map = {}
    for i, r in enumerate(results):
        checks = sev.compute_checks(r, by_index.get(i), tentries.get(i))
        checks["_tentry"] = tentries.get(i)
        checks_map[i] = checks
    span_to_idx = {}
    for i, r in enumerate(results):
        s = getattr(r.citation, "span_start", None)
        if s is not None and s not in span_to_idx:
            span_to_idx[s] = i
    nested_children = {}
    nested_set = set()
    for i, r in enumerate(results):
        if not sev.is_nested(r):
            continue
        parent = span_to_idx.get(getattr(r.citation, "nested_parent_span", None))
        if parent is None or parent == i:
            continue
        nested_children.setdefault(parent, []).append(i)
        nested_set.add(i)
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for i in range(len(results)):
        counts[checks_map[i]["tier"]] += 1
    # Fix 10b (2026.07.29, Session E): cluster an authority's instances
    # adjacently.  Each cluster anchors at its WORST instance's severity-sorted
    # position ((tier, index) of the worst member), so tier-level triage order
    # is unchanged; within a cluster, members render worst-first.  Authorities
    # cited once render exactly as before.
    def _authority_key(i):
        r = results[i]
        tm = getattr(r.citation, "toa_match", None)
        name = (tm.get("name") if tm and tm.get("name") else
                getattr(r.citation, "name", "") or "")
        key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        return key or "__solo_%d" % i
    eligible = [i for i in range(len(results)) if i not in nested_set]
    clusters = {}
    for i in eligible:
        clusters.setdefault(_authority_key(i), []).append(i)
    anchor = {k: min((checks_map[i]["tier"], i) for i in idxs)
              for k, idxs in clusters.items()}
    order = []
    for k in sorted(clusters, key=lambda k: anchor[k]):
        order.extend(sorted(clusters[k], key=lambda i: (checks_map[i]["tier"], i)))
    return checks_map, nested_children, nested_set, counts, order


def _docx_crit(r, checks):
    """(chip_label, lead, rest) for a Tier-1 card -- FABRICATED leads (B1)."""
    q = checks["quote"]
    if q["status"] == "fabricated":
        fq = next((x for x in (getattr(r, "quote_results", None) or [])
                   if x.get("result") == "FABRICATED"), {})
        rest = (" The quoted language — “%s” — does not "
                "appear anywhere in the opinion. Absence of quoted words is "
                "always Critical, even where the case is thematically "
                "consistent." % sev.tidy_passage(fq.get("quote", ""), 220))
        if fq.get("full_text_checked"):
            rest += (" Re-checked against the complete opinion text before "
                     "flagging.")
        return "CRITICAL · FABRICATED QUOTATION", "Fabricated quotation.", rest
    if checks["identity"]["status"] == "wrong_case":
        return ("CRITICAL · WRONG CASE", "Wrong case.",
                " " + checks["identity"]["text"])
    if checks["support"]["status"] == "contrary":
        return ("CRITICAL · CITED AS CONTRARY", "Cited as contrary.",
                " " + checks["support"]["text"])
    return "CRITICAL", "", ""


def _docx_rows(r, checks):
    """Serialize the four check rows with per-row links/notes for DOCX."""
    def base(row):
        return {"axis": row["axis"], "chip": row["chip"],
                "chip_cls": row["chip_cls"], "tier": row["tier"],
                "text": row["text"], "link_url": "", "link_label": "",
                "link_prefix": "", "note": "",
                "diff": row.get("diff")}
    idn = base(checks["identity"])
    op_url = getattr(r, "opinion_url", "") or ""
    if op_url and checks["identity"]["status"] == "confirmed":
        idn["link_url"] = op_url
        idn["link_label"] = "the opinion on CourtListener"
        idn["link_prefix"] = "Resolved to"
    elif op_url:
        idn["link_url"] = op_url
        idn["link_label"] = _retrieved_copy_label(r)
        idn["link_prefix"] = "A copy was retrieved:"
        idn["note"] = "Identity not confirmed against this copy."
    elif (getattr(r, "search_url", "") or ""):
        idn["link_url"] = r.search_url
        idn["link_label"] = "Look up on CourtListener"
    ln = (getattr(r, "lookup_note", "") or "").strip()
    if ln and not _caption_mismatch_suppressed(r):
        idn["note"] = (idn["note"] + " " + ln).strip() if idn["note"] else ln
    q = base(checks["quote"])
    s = base(checks["support"])
    t = base(checks["treatment"])
    sigs = (checks.get("_tentry") or {}).get("signals") or []
    if sigs and sigs[0].get("url"):
        t["link_url"] = sigs[0]["url"]
        t["link_label"] = "Citing case on CourtListener"
    return [idn, q, s, t]


def _docx_nested(n, child, parent_checks):
    kind = (getattr(child.citation, "nested_parenthetical", "") or "quoting")
    name = (child.citation.name or "").strip()
    ct = (getattr(child.citation, "cite_text", "") or "").strip()
    head = name
    if ct and ct.lower() != name.lower():
        head = (head + ", " if head else "") + ct
    op_url = getattr(child, "opinion_url", "") or ""
    resolved = bool(child.opinion_resolved or op_url)
    if parent_checks and parent_checks["quote"]["tier"] == sev.TIER_PASS:
        tail = ("and the quoted language verifies against the parent's "
                "proposition. Not scored as a standalone citation.")
    else:
        tail = ("— see the parent's Quote row for the quoted language. "
                "Not scored as a standalone citation.")
    return {"n": n, "kind": kind, "head": head, "resolved": resolved,
            "url": op_url, "tail": tail}


def render_docx(
    results: Sequence[CiteCheckResult],
    meta: dict | None = None,
    out_path: "str | Path | None" = None,
) -> None:
    """Produce a Word cite-check report on the Phase 3 severity model (G9).

    Mirrors render_html: a severity-first scoreboard, offender lists, and
    four-row cards grouped Critical-first with original numbering. Tiers,
    colors, and row text come from cc_severity (single source of truth).

    Raises:
        RuntimeError: If Node.js or the docx npm package is not available.
    """
    import time as _time
    meta = meta or {}
    out_path = Path(out_path) if out_path else Path("cite_check_report.docx")
    import cite_check_report as _rep

    _attach_display_captions(results)
    checks_map, nested_children, nested_set, counts, order = _severity_model(
        results, meta)

    # Meta line (mirror the HTML).
    pieces = []
    head_bits = " — ".join(x for x in (meta.get("matter"),
                                            meta.get("document_name")) if x)
    if head_bits:
        pieces.append(head_bits)
    pieces.append("checked " + _time.strftime("%Y.%m.%d"))
    pieces.append("%d citations" % len(results))
    if meta.get("jurisdiction"):
        pieces.append("jurisdiction " + str(meta["jurisdiction"]))
    pieces.append("eyecite + CourtListener + Isaacus pipeline")
    meta_line = " · ".join(pieces)

    # Offender lists.
    offenders = {"critical": [], "unverified": [], "defect": [],
                 "review": []}
    key_by_tier = {1: "critical", 2: "unverified", 3: "defect",
                   4: "review"}
    for i in order:
        t = checks_map[i]["tier"]
        if t not in key_by_tier:
            continue
        offenders[key_by_tier[t]].append({
            "n": i + 1, "name": _short_case(results[i]),
            "reason": sev.offender_reason(checks_map[i])})

    # Cards.
    cards = []
    for i in order:
        r = results[i]
        checks = checks_map[i]
        tier = checks["tier"]
        card = {
            "n": i + 1, "tier": tier, "icon": sev.TIER_ICON.get(tier, ""),
            "chip_label": sev.TIER_LABEL[tier], "headline": _headline(r),
            "authority": "", "proposition": "", "proposition_source": "",
            "proposition_review": False, "collapse": False,
            "crit_lead": "", "crit_rest": "", "pincite": "",
            "pincite_found": None, "pincite_note": "", "body_only": False,
            "rows": [], "support_passage": "", "passage_page": "",
            "second_opinion": "", "second_opinion_score": None,
            "verification_note": "", "search_detail": "", "notes": "",
            "legacy_verdict": _rep._verdict(r), "nested": [],
        }
        # Authority context line.
        _ct = (getattr(r.citation, "cite_text", "") or "").strip()
        _nm = (r.citation.name or "").strip()
        if _ct and _nm:
            _nm_head = _nm.split(",")[0].strip().lower()
            if _nm_head and _nm_head not in _ct.lower():
                card["authority"] = _nm
        # Proposition.
        if (r.citation.proposition or "").strip():
            card["proposition"] = r.citation.proposition.strip()
            card["proposition_source"] = getattr(
                r.citation, "proposition_source", "") or ""
        elif getattr(r.citation, "proposition_review", False):
            card["proposition_review"] = True

        collapse = (sev.all_clean(checks)
                    and checks["support"]["status"] == "supported"
                    and not getattr(r, "body_only", False)
                    and not (getattr(r, "lookup_note", "") or "").strip()
                    and not _verification_note(r))
        if collapse:
            card["collapse"] = True
            card["collapse_op_url"] = getattr(r, "opinion_url", "") or ""
            card["collapse_idb"] = "Identity confirmed"
            _pin = (getattr(r.citation, "pincite", "") or "").strip()
            qs = checks["quote"]["status"]
            if qs == "verified":
                card["collapse_qb"] = "quote verbatim" + (
                    " at " + _pin if _pin and getattr(
                        r, "pincite_found", None) else "")
            elif qs in ("verified_alterations", "verified_licensed"):
                card["collapse_qb"] = "quote verified (permitted alterations)"
            else:
                card["collapse_qb"] = "no quote to check"
            card["collapse_tb"] = ("no negative-treatment signals"
                                   if checks["treatment"]["status"] == "clean"
                                   else "treatment not checked")
        else:
            if tier == sev.TIER_CRITICAL:
                chip, lead, rest = _docx_crit(r, checks)
                card["chip_label"] = chip
                card["crit_lead"] = lead
                card["crit_rest"] = rest
            _pin = (getattr(r.citation, "pincite", "") or "").strip()
            if _pin:
                card["pincite"] = _pin
                card["pincite_found"] = getattr(r, "pincite_found", None)
                card["pincite_note"] = (
                    getattr(r, "pincite_note", "") or "").strip()
            card["body_only"] = bool(getattr(r, "body_only", False))
            card["rows"] = _docx_rows(r, checks)
            if checks["support"]["tier"] in (sev.TIER_DEFECT, sev.TIER_REVIEW):
                if (r.passage or "").strip():
                    card["support_passage"] = sev.tidy_passage(r.passage, 480)
                card["passage_page"] = (
                    getattr(r, "passage_page", "") or "").strip()
                card["support_page_not_found"] = (
                    checks["support"]["status"] == "page_not_found")
                _so = (getattr(r, "second_opinion", "") or "").strip()
                if _so:
                    card["second_opinion"] = _so
                    card["second_opinion_score"] = getattr(
                        r, "second_opinion_score", None)
            card["verification_note"] = _verification_note(r)
            if not r.opinion_resolved:
                card["search_detail"] = (
                    getattr(r, "search_detail", "") or "").strip()
            card["notes"] = (r.notes or "").strip()
        # Nested quoted-source children.
        for j in sorted(nested_children.get(i, [])):
            card["nested"].append(_docx_nested(j + 1, results[j], checks))
        cards.append(card)

    status_key = [
        {"tier": 1, "head": "Tier 1 — Critical (sanction risk):",
         "text": "fabricated quotation confirmed against the complete "
                 "opinion; wrong case; cited as supporting when the "
                 "opinion holds the opposite."},
        {"tier": 2, "head": "Tier 2 — Unverified (check on Westlaw or "
                 "Lexis):",
         "text": "the tool could not check this authority at all — not "
                 "found in any free database, wrong document retrieved "
                 "(coverage gap), or identity unconfirmable. A case "
                 "nobody checked outranks a weak-support flag."},
        {"tier": 3, "head": "Tier 3 — Fix (before filing):",
         "text": "cited page does not support the proposition; material "
                 "misquote (see the word diff on the card)."},
        {"tier": 4, "head": "Tier 4 — Review (confirm by hand):",
         "text": "moderate support; pincite page not found; unconfirmed "
                 "negative-treatment or caution signal; quote not "
                 "confirmable against a partial copy."},
        {"tier": 5, "head": "Tier 5 — Pass:",
         "text": "correct case, quote exact or permissibly altered, page "
                 "supports, no negative-treatment signals."},
    ]

    data = {
        "meta": {"jurisdiction": meta.get("jurisdiction") or "",
                 "matter": meta.get("matter") or "",
                 "document_name": meta.get("document_name") or ""},
        "meta_line": meta_line,
        "scoreboard": {str(t): counts[t] for t in (1, 2, 3, 4, 5)},
        "tier_label": {str(t): sev.TIER_LABEL[t]
                       for t in (1, 2, 3, 4, 5)},
        "tier_sub": {str(t): sev.TIER_SUB[t] for t in (1, 2, 3, 4, 5)},
        "offenders": offenders,
        "cards": cards,
        "status_key": status_key,
        "non_case_references": meta.get("non_case_references") or [],
        "toa_only_cases": meta.get("toa_only_cases") or [],
        "body_only_cases": _body_only_display(results, meta),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        data_file = tmp / "cite_check_data.json"
        script_file = tmp / "cite_check_report_docx_build.js"
        data_file.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
        script_file.write_text(_DOCX_BUILD_SCRIPT, encoding="utf-8")
        result = subprocess.run(
            ["node", str(script_file), str(data_file), str(out_path)],
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                "docx build script failed (exit %d): %s"
                % (result.returncode, result.stderr[:500]))


__all__ = ["render_html", "render_markdown", "render_docx"]
