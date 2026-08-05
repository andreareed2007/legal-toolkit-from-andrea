"""Checkpointed, matter-agnostic cite-check runner for Cowork.

WHY THIS EXISTS: the Cowork sandbox caps each shell call at ~45s and wipes /tmp
between restarts, and the RECAP fallback (~12s per gap) can push a one-shot
cite_check() over that cap.  This splits the pipeline into phases that each
finish inside one window, with a RESUMABLE resolve step (saves after every
citation, re-run to drain the rest).

USAGE (run each phase as its own shell call, in order):
    python3 cite_check_runner.py build  "<brief_path>" "<matter label>" "<doc name>"
    python3 cite_check_runner.py resolve            # re-run until "remaining=0"
    python3 cite_check_runner.py phase2
    python3 cite_check_runner.py render "<out_html_path>"

Run from inside the Isaacus Integration folder (so `import cite_check` works).
State is carried between phases via /tmp pickles (single job at a time).
"""
import sys, os, pickle, time, json, re
sys.path.insert(0, ".")

import cite_check as cc
import isaacus_helpers as helpers
import isaacus_chunker as chunker_mod
import cc_proposition as ccprop
from cite_check import CiteCheckResult, extract_verbatim_quote, quote_in_opinion
import cl_resolver as _clr
from cl_resolver import CLResolver, build_search_url
from concurrent.futures import ThreadPoolExecutor

# State dir is env-overridable (2026.07.04): stale root-owned /tmp pickles
# from a prior VM can block the default paths; point CC_STATE_DIR elsewhere.
# Default hardened 2026.07.19: /tmp/cc_state (matches the SKILL.md export), so a
# shell call that forgets the export no longer silently switches state dirs.
_STATE_DIR = os.environ.get("CC_STATE_DIR", "/tmp/cc_state")
os.makedirs(_STATE_DIR, exist_ok=True)
BUILT_PKL = os.path.join(_STATE_DIR, "cc_built.pkl")
RES_PKL = os.path.join(_STATE_DIR, "cc_resolve.pkl")
CKPT = os.path.join(_STATE_DIR, "cc_ckpt.pkl")
RESULT_PKL = os.path.join(_STATE_DIR, "cc_result.pkl")
GAPS_JSON = os.path.join(_STATE_DIR, "cc_gaps.json")
GOODLAW_PKL = os.path.join(_STATE_DIR, "cc_goodlaw.pkl")
PROPS_JSON = os.path.join(_STATE_DIR, "cc_props_review.json")
VERIFY_JSON = os.path.join(_STATE_DIR, "cc_verify_review.json")


def build_citations(brief_text):
    # 2026.07.04: delegate to the SHARED phase function in cite_check --
    # detection is eyecite there and the runner must never drift again
    # (audit 3.6). Return shape unchanged for the pickle checkpoint.
    return cc.build_citations(brief_text)


def _paragraph_at(text, start, end, pad=900):
    """The paragraph (blank-line bounded) around a citation span, capped."""
    s = text.rfind("\n\n", max(0, start - pad), start)
    s = s + 2 if s >= 0 else max(0, start - pad)
    e = text.find("\n\n", end, end + pad)
    e = e if e >= 0 else min(len(text), end + pad)
    return text[s:e].strip()


_HEADING_GLUE_RE = __import__("re").compile(
    r"^(?:(?:[A-Z][A-Za-z'\u2019.]*|and|of|the|for|in|to|on|&)\s+){3,10}"
    r"(?:The|A|An|In|Under|Given|Because|Where|When|While|Here|This|These|"
    r"Those|Courts?|Plaintiffs?|Defendants?|It|As|At|On|To|For)\b")


def _heading_glue(prop):
    """Phase 5 polish (2026.07.04): SUSPECT heuristic for a Title Case section
    heading glued onto the sentence start (the Brief A Alliance Network miss:
    'The Fourteenth and Fifteenth Amendments Given the ongoing...').  A prefix
    of 3-10 capitalized/connector words with NO sentence punctuation, followed
    by a fresh capitalized sentence opener, is heading-shaped.  False positives
    only add a review-manifest entry -- they never change a verdict.  Case-
    agnostic (prime directive)."""
    head = prop[:120]
    m = _HEADING_GLUE_RE.match(head)
    if not m:
        return False
    return not any(ch in m.group(0) for ch in ".!?:;,\u2014")


def _props_review_entries(built):
    """Phase 3 (2026.07.04): failed/suspect proposition extractions.

    Failures are empty extractions; suspects are structurally clean but
    at risk of being the wrong sentence (short fragments, runaways,
    mid-sentence starts, one sentence shared across DIFFERENT authorities
    -- the string-cite walk-back failure mode).  Reasons are heuristic and
    case-agnostic (prime directive: nothing brief-specific).
    """
    cits = built["citations"]
    body = built.get("argument_text", "")
    def _n(s):
        import re as _re
        return _re.sub(r"\s+", " ", (s or "").lower()).strip()
    prop_owners = {}
    for c in cits:
        if (c.proposition or "").strip():
            prop_owners.setdefault(_n(c.proposition), set()).add(_n(c.name))
    out = []
    for i, c in enumerate(cits):
        prop = (c.proposition or "").strip()
        reasons = []
        if not prop:
            reasons.append("not extracted")
        else:
            if len(prop) < 50:
                reasons.append("very short -- may be a fragment")
            if len(prop) > 480:
                reasons.append("runaway -- may span multiple sentences")
            if prop[:1].islower():
                reasons.append("starts mid-sentence")
            if len(prop_owners.get(_n(prop), set())) > 1:
                reasons.append("same sentence attributed to a different authority "
                               "-- possible string-cite walk-back error")
            if _heading_glue(prop):
                reasons.append("possible section heading glued to the sentence "
                               "start -- confirm against the paragraph")
        if not reasons:
            continue
        out.append({
            "index": i,
            "name": c.name,
            "cite_text": c.cite_text or "",
            "occurrence": f"{c.occurrence_index}/{c.occurrence_count}",
            "current_proposition": prop,
            "reason": "; ".join(reasons),
            "paragraph": _paragraph_at(body, c.span_start or 0, c.span_end or 0),
        })
    return out


def build():
    t0 = time.time()
    brief_path = sys.argv[2]
    matter = sys.argv[3] if len(sys.argv) > 3 else ""
    doc_name = sys.argv[4] if len(sys.argv) > 4 else os.path.basename(brief_path)
    # .docx/.dotx intake (2026.07.13, Brief C magic-wand miss): reading a Word
    # file as UTF-8 text throws, and a naive python-docx paragraph extract
    # SILENTLY DROPS every footnote marker and footnote body -- so footnoted
    # citations never enter the pipeline (the Brief C Opposition footnote
    # authorities, incl. the fabricated "magic wand" quote cited in fn.15 as
    # "Id. at 704", were all missed). Route Word files through docx_to_text,
    # which splices each footnote inline at its reference marker so a footnoted
    # cite behaves like an ordinary trailing inline citation.
    if brief_path.lower().endswith((".docx", ".dotx")):
        import docx_to_text
        bt = docx_to_text.extract(brief_path)
    else:
        bt = open(brief_path, encoding="utf-8").read()
    try:
        built = build_citations(bt)
    except cc.DoubledInputError as e:
        print(f"[build] REFUSED -- {e}", file=sys.stderr)
        sys.exit(2)
    # A new build starts a NEW job: clear downstream state from any prior
    # document (2026.07.04 Session 3). The resolve/ckpt maps are keyed by
    # citation INDEX, so stale state from another brief silently feeds the
    # WRONG opinion text to same-numbered citations (observed live: a
    # Brief A cite inherited a Brief B search trail). Resolve resumability
    # within ONE job is unaffected -- resolve windows never re-run build.
    for _p in (RES_PKL, CKPT, RESULT_PKL, GAPS_JSON, GOODLAW_PKL, VERIFY_JSON):
        if os.path.exists(_p):
            os.remove(_p)
    pickle.dump(dict(brief_text=bt, built=built, matter=matter, doc_name=doc_name),
                open(BUILT_PKL, "wb"))
    # Phase 3: write the proposition-review manifest.  The SKILL workflow
    # REQUIRES the in-session Claude pass over it: for each entry, read the
    # paragraph, write the governing assertion the brief attributes to the
    # cite, and ingest via the `props` verb.  Entries may be left blank when
    # the paragraph genuinely attributes nothing checkable (string cite).
    entries = _props_review_entries(built)
    json.dump({
        "doc_name": doc_name,
        "instructions": (
            "For each entry, read `paragraph` and write the proposition the "
            "brief attributes to THIS citation instance: the governing "
            "assertion, quoted or closely paraphrased FROM THE BRIEF -- "
            "never from memory of the case. No signals, no record cites, "
            "no citation strings. 1-2 sentences. If the paragraph "
            "attributes nothing checkable, omit the entry (it renders "
            "'review required'). Submit as JSON list of "
            "{\"index\": N, \"proposition\": \"...\"} via: "
            "python3 cite_check_runner.py props <file.json>"),
        "entries": entries,
    }, open(PROPS_JSON, "w", encoding="utf-8"), indent=1)
    print(f"[build] {len(built['citations'])} citations in {time.time()-t0:.1f}s; "
          f"props review: {len(entries)} -> {PROPS_JSON}")


def _ft_key(c, i):
    """full_texts checkpoint key: the authority's cite key, so id-chain
    members share ONE stored copy of the untrimmed opinion (G11); falls back
    to the instance index for cite-less citations."""
    k = _clr._cite_key(c)
    return k or f"idx:{i}"


def _propagate_opinion_urls(cits, ou):
    """G11 (2026.07.14): id-chain members inherit the authority's opinion URL.

    Short forms and id. instances share the parent full cite's reporter
    address (_cite_key), so any member that resolved carries the direct
    opinion URL for the whole chain. Returns a filled COPY -- the checkpoint
    on disk is not mutated."""
    out = dict(ou or {})
    by_key = {}
    for i, c in enumerate(cits):
        k = _clr._cite_key(c)
        if k:
            by_key.setdefault(k, []).append(i)
    for idxs in by_key.values():
        url = next((out.get(j) for j in idxs if out.get(j)), "")
        if url:
            for j in idxs:
                if not out.get(j):
                    out[j] = url
    return out


def resolve():
    t0 = time.time()
    bd = pickle.load(open(BUILT_PKL, "rb"))
    built = bd["built"]; cits = built["citations"]
    resolver = CLResolver()
    prev = pickle.load(open(RES_PKL, "rb")) if os.path.exists(RES_PKL) else {}
    ot = prev.get("opinion_texts", {}); su = prev.get("search_urls", {})
    sd = prev.get("search_details", {}); nc = prev.get("nc_map", {})
    ru = prev.get("recap_url", {}); rs = prev.get("recap_src", {})
    ls = prev.get("lookup_status", {}); ln = prev.get("lookup_note", {})
    ou = prev.get("opinion_url", {}); ft = prev.get("full_texts", {})
    lookup = prev.get("lookup") or {}
    api_base = prev.get("api_calls", 0)  # cumulative across windows
    todo = [i for i in range(len(cits)) if i not in ot]
    print(f"[resolve] {len(todo)} of {len(cits)} remaining")

    deadline = t0 + 38  # exit before the ~45s shell cap; re-run resumes

    # Phase 2 (2026.07.04): batched citation-lookup is the PRIMARY resolver.
    # One POST covers <=250 cites; per-cite statuses land in lookup["map"].
    # Pacing (60 valid cites/min) is handled inside batch_lookup_step against
    # this window's deadline -- an unfinished lookup resumes next window.
    if not lookup.get("complete"):
        lookup = resolver.batch_lookup_step(cits, lookup, deadline=deadline)
    else:
        resolver._lookup_map = lookup.get("map", {})

    def _one(i):
        c = cits[i]
        try:
            txt = resolver.resolve_opinion_text(c)
        except Exception:
            txt = None
        log = resolver.get_log(c.name)
        # A1 (2026.07.14): capture the resolver's direct opinion URL (set by
        # _record_success on lookup/name-tier wins, by the RECAP path, and by
        # the cache path). Failure-path fallback links are NOT captured -- an
        # unresolved card keeps its search-URL fallback in the report.
        ourl = (log.opinion_url if (log and log.success) else "") or ""
        # E1/G2 (2026.07.14): the resolver cache holds the UNTRIMMED opinion;
        # keep it (keyed by authority) so quote fidelity can search the full
        # text. verify() itself stays on the pincite window (M3: token cost).
        # Phase 7 (2026.07.15): the resolver now records the untrimmed body
        # on the log at EVERY success path -- deterministic, unlike the old
        # post-hoc cache probe, which missed when the lookup ingest cached
        # under CL's caption instead of the brief's (Farmers, Brief C cit 7).
        full = None
        if txt:
            full = (getattr(log, "full_text", None)
                    if (log and log.success) else None)
            if not full:
                try:
                    full = resolver._cache_get_for(c) or None
                except Exception:
                    full = None
        return (i, txt, (log.search_url if log else "") or build_search_url(c),
                log.build_detail() if log else "", getattr(c, "_resolved_name_cite_ok", None),
                getattr(c, "_recap_url", "") or "", getattr(c, "_recap_source", "") or "",
                ourl, full)

    def _save():
        pickle.dump(dict(opinion_texts=ot, search_urls=su, search_details=sd,
                         nc_map=nc, recap_url=ru, recap_src=rs,
                         opinion_url=ou, full_texts=ft,
                         lookup_status=ls, lookup_note=ln, lookup=lookup,
                         api_calls=api_base + getattr(resolver, "_request_count", 0)),
                    open(RES_PKL, "wb"))

    _save()  # persist lookup progress even if no cite resolves this window
    lm = lookup.get("map", {})
    lookup_done = bool(lookup.get("complete"))
    # Sequential: CourtListener throttles concurrent calls, so parallelism stalls.
    for i in todo:
        if time.time() > deadline:
            break
        if not lookup_done:
            k = _clr._cite_key(cits[i])
            if k and k not in lm:
                continue  # this cite's batch lookup is pending; next window
        _, txt, u, d, ncok, rurl, rsrc, ourl, full = _one(i)
        ot[i] = txt; su[i] = u; sd[i] = d; nc[i] = ncok; ru[i] = rurl; rs[i] = rsrc
        ou[i] = ourl
        # Phase 7: store unconditionally. full == txt (no trim) previously
        # arrived at verify_citation as full_text=None -- indistinguishable
        # from "no complete copy", which would wrongly degrade a genuine
        # confirmed fabrication to review.
        if full:
            ft[_ft_key(cits[i], i)] = full
        ls[i] = getattr(cits[i], "_lookup_status", None)
        ln[i] = getattr(cits[i], "_lookup_note", "") or ""
        _save()
    pickle.dump(dict(brief_text=bd["brief_text"], built=built, matter=bd.get("matter", ""),
                     doc_name=bd.get("doc_name", ""), opinion_texts=ot, search_urls=su,
                     search_details=sd, nc_map=nc, recap_url=ru, recap_src=rs,
                     opinion_url=ou, full_texts=ft,
                     lookup_status=ls, lookup_note=ln), open(CKPT, "wb"))
    # Phase 2b (2026.07.04): once every citation has been attempted, write
    # the machine-readable gap manifest.  The SKILL workflow REQUIRES the
    # agent loop over it: WebSearch (domain-restricted) / candidate URLs ->
    # web_fetch -> `patch_gap` -> re-run `phase2`.  Bounded: <=2 fetches/gap.
    if all(i in ot for i in range(len(cits))):
        prev_gaps = {}
        if os.path.exists(GAPS_JSON):
            try:
                prev_gaps = {g["index"]: g for g in json.load(open(GAPS_JSON, encoding="utf-8")).get("gaps", [])}
            except Exception:
                prev_gaps = {}
        gaps = []
        for i, v in ot.items():
            if v:
                continue
            c = cits[i]
            old_g = prev_gaps.get(i, {})
            gaps.append({
                "index": i,
                "name": c.name,
                "cite_text": c.cite_text or "",
                "reporter_cite": _clr.reporter_cite_str(c),
                "pincite": c.pincite or "",
                "candidates": [{"source": s, "url": u} for s, u in _clr.fallback_candidates(c)],
                "search_query": _clr.reported_search_query(c),
                "search_domains": _clr.REPORTED_SEARCH_DOMAINS,
                "max_fetches": 2,
                "attempts": old_g.get("attempts", 0),
                "status": old_g.get("status", "open"),
            })
        json.dump({"doc_name": bd.get("doc_name", ""), "gaps": gaps},
                  open(GAPS_JSON, "w", encoding="utf-8"), indent=1)
        print(f"[resolve] gap manifest: {len(gaps)} open -> {GAPS_JSON}")
    nres = sum(1 for v in ot.values() if v)
    print(f"[resolve] {nres}/{len(cits)} resolved in {time.time()-t0:.1f}s; "
          f"remaining={sum(1 for v in ot.values() if not v)}; "
          f"api_calls_this_window={getattr(resolver, '_request_count', '?')}; "
          f"api_calls_total={api_base + getattr(resolver, '_request_count', 0)}")



# --------------------------------------------------------------------------
# Step 6.6 (2026.07.10): auto-resolved verification loop (must-verify gate).
# Cites that DID resolve but resolved to something untrustworthy -- a wrong CL
# record, or a non-reporter free/slip-op copy -- used to pass silently through
# phase2 and land as an adverse verdict.  The Brief A Doc 89 run (2026.07.09)
# shipped THREE false-negative slip-op verdicts that way (M.A., Bd. of Mgrs.
# of 252 Condominium, Wolf); a human caught them only post-delivery.  This
# mirrors the gap-loop manifest pattern: phase2 writes cc_verify_review.json,
# the agent works each open entry (fetch the actual opinion, read the cited
# pincite), the `verify` verb ingests findings, and render() HARD-BLOCKS on
# open entries (attorney-approved 2026.07.10).  Additive provenance layer:
# verify() semantics and the 11-verdict taxonomy are UNTOUCHED.
# --------------------------------------------------------------------------
_NY_SLIP_U_RE = re.compile(r"N\.?\s*Y\.?\s*Slip\s*Op\.?\s*\d{4,6}\s*\(\s*[Uu]\s*\)")
_VERIFY_ADVERSE = ("does_not_support", "cited_as_contrary",
                   "identity_unconfirmed", "pincite_not_found")
# Tunable (the attorney 2026.07.10): low-confidence Somewhat Supports on high-risk
# sources also enter the manifest.  NOTE: with the current verdict thresholds
# a "somewhat" card carries score > 0.5 by construction, so 0.35 keeps this
# leg dormant; raise it (e.g. 0.60) to sweep the weakest somewhat band.
# 0.0 disables the leg outright.
VERIFY_SOMEWHAT_MAX_CONF = 0.35


def _verify_high_risk(r):
    """(is_high_risk, reasons) -- authority at high risk of auto-resolution
    error.  Case-agnostic (prime directive): source class + identity flags
    only, nothing brief-specific."""
    c = r.citation
    reasons = []
    cite_blob = " ".join(filter(None, [
        getattr(c, "cite_text", "") or "", _clr.reporter_cite_str(c) or ""]))
    if _NY_SLIP_U_RE.search(cite_blob):
        reasons.append("unpublished NY slip op")
    src = (getattr(r, "opinion_source", "") or "").strip()
    if src and src.lower() != "courtlistener":
        reasons.append("non-reporter / free source (%s)" % src)
    if ("patched from free source via gap manifest"
            in (getattr(r, "search_detail", "") or "").lower()):
        reasons.append("patched via the gap loop")
    pnote = (getattr(r, "pincite_note", "") or "").lower()
    if "non-reporter copy" in pnote or "no reporter pagination" in pnote:
        reasons.append("non-reporter copy (pincite note)")
    if getattr(r, "name_cite_ok", None) is False:
        reasons.append("identity gate failed (name_cite_ok=False)")
    if getattr(c, "_cite_contradicted", False):
        reasons.append("cite-address contradiction flag")
    if getattr(c, "_lookup_addr_mismatch", False):
        reasons.append("lookup caption/address mismatch flag")
    return (bool(reasons), reasons)


def _verify_trigger(r):
    """(triggers, verdict, reason) -- True when the card must enter the
    must-verify manifest: adverse/identity-doubtful verdict (or a
    low-confidence Somewhat, per the tunable) AND a high-risk authority."""
    import cite_check_report as rep
    v = rep._verdict(r)
    if v == "does_not_support":
        # G6 (Phase 3, 2026.07.14, locked Option 1): EVERY Does-Not-Support
        # verdict enters the Step 6.6 loop for contrary-vs-unsupportive
        # classification -- Tier-1 "cited as contrary" fires only on the
        # agent's confirmed_contrary finding, never on a raw score.
        # High-risk source reasons still ride along when present.
        hi, hr = _verify_high_risk(r)
        reason = "contrary-vs-unsupportive classification required (G6)"
        if hi:
            reason += "; " + "; ".join(hr)
        return True, v, reason
    # Phase 7 confirmation gate (the attorney 2026.07.15): an unconfirmed
    # FABRICATED (quote missing from a partial copy; complete opinion
    # unavailable) renders review, NOT critical -- and must enter the
    # must-verify loop so the agent fetches the full opinion and settles
    # it before the report renders. Same "confirm before adverse"
    # discipline as the rest of Step 6.6.
    _uf = [q for q in (getattr(r, "quote_results", None) or [])
           if q.get("result") == "FABRICATED"
           and not q.get("confirmed", q.get("full_text_checked"))]
    if _uf:
        return True, v, (
            "unconfirmed fabricated quotation (\u201c%s\u2026\u201d) -- "
            "graded against a partial copy; the complete opinion was "
            "unavailable (Phase 7 confirmation gate). Fetch the full "
            "opinion and check the quotation."
            % (_uf[0].get("quote", "") or "")[:80])
    # 2026.08.04 (v16 Cit 24, In re H-Corp 111 F.4th): a CONFIRMED
    # fabrication is only as trustworthy as the copy's IDENTITY. When the
    # resolution was NOT an exact citation-lookup hit (lookup_status 200),
    # the "complete opinion" the absence was confirmed against may be a
    # same-name DIFFERENT opinion -- the v16 run confirmed absence against
    # the 2022 N-Corp v. H-Corp opinion while the quoted words sit
    # verbatim in the real 2024 111 F.4th opinion (address unindexed on
    # CL, name-tier win). Enqueue so the agent fetches the ACTUAL cited
    # opinion before a CRITICAL ships.
    _cf = [q for q in (getattr(r, "quote_results", None) or [])
           if q.get("result") == "FABRICATED"
           and q.get("confirmed", q.get("full_text_checked"))]
    if _cf and getattr(r, "lookup_status", None) != 200:
        return True, v, (
            "confirmed-fabricated quotation (\u201c%s\u2026\u201d) on a "
            "copy resolved WITHOUT an exact citation-lookup address match "
            "-- the complete copy may be a same-name different opinion. "
            "Fetch the cited opinion and check the quotation before the "
            "Critical stands."
            % (_cf[0].get("quote", "") or "")[:80])
    if v in _VERIFY_ADVERSE:
        v_reason = "adverse verdict (%s)" % v
    elif v == "somewhat" and r.score < VERIFY_SOMEWHAT_MAX_CONF:
        v_reason = ("low-confidence somewhat (%.2f < %.2f)"
                    % (r.score, VERIFY_SOMEWHAT_MAX_CONF))
    else:
        return False, v, ""
    hi, hr = _verify_high_risk(r)
    if not hi:
        return False, v, ""
    return True, v, v_reason + "; " + "; ".join(hr)


def _apply_verification(r, entry):
    """Re-apply an ingested finding to a fresh phase2 card (idempotent --
    phase2 re-runs must never silently discard agent verification work).
    The override flag was evidence-gated at ingest time (identity gate +
    VERBATIM quote on the fetched body); re-application trusts it."""
    import cite_check_report as rep
    r.verification_source = "agent"
    r.verification_finding = entry.get("finding", "")
    r.verification_note = entry.get("note", "")
    r.verification_url = entry.get("url", "")
    r.verification_quote = entry.get("quote", "")
    if entry.get("override"):
        r.verification_machine_verdict = rep._verdict(r)
        r.verification_override = True


def _verify_review_entries(results, cits, doc_name):
    """Build/refresh the must-verify manifest at the END of phase2.

    Preserves worked entries from a prior manifest (same doc) and re-applies
    their findings to the fresh cards FIRST (an evidence-gated override
    changes the verdict, so re-application precedes the trigger re-check);
    only still-triggering, un-worked cards stay open.  Returns open count."""
    prev = {}
    if os.path.exists(VERIFY_JSON):
        try:
            m = json.load(open(VERIFY_JSON, encoding="utf-8"))
            if m.get("doc_name") == doc_name:
                prev = {int(e["index"]): e for e in m.get("entries", [])}
        except Exception:
            prev = {}
    for i, e in prev.items():
        if e.get("status") in ("worked", "unable") and 0 <= i < len(results):
            _apply_verification(results[i], e)
    entries = []
    for i, r in enumerate(results):
        old = prev.get(i)
        if old is not None and old.get("status") in ("worked", "unable"):
            entries.append(old)
            continue
        trig, v, reason = _verify_trigger(r)
        if not trig:
            continue
        c = cits[i]
        entries.append({
            "index": i,
            "name": c.name,
            "cite_text": c.cite_text or "",
            "reporter_cite": _clr.reporter_cite_str(c),
            "pincite": c.pincite or "",
            "verdict": v,
            "confidence": round(r.score, 2),
            "inextractability": round(r.inextractability_score, 2),
            "reason": reason,
            "located_passage": (r.passage or "")[:500],
            "opinion_source": getattr(r, "opinion_source", "") or "",
            "opinion_url": getattr(r, "opinion_url", "") or "",
            "candidates": [{"source": s, "url": u}
                           for s, u in _clr.fallback_candidates(c)],
            "search_query": _clr.reported_search_query(c),
            "search_domains": _clr.REPORTED_SEARCH_DOMAINS,
            "max_fetches": 2,
            "attempts": 0,
            "status": "open",
        })
    n_open = sum(1 for e in entries if e.get("status") == "open")
    json.dump({
        "doc_name": doc_name,
        "instructions": (
            "Step 6.6 must-verify loop: these cards drew an adverse "
            "verdict on a high-risk source (unpublished NY slip op, "
            "non-reporter/free copy, identity-flagged record) OR a "
            "Does-Not-Support verdict needing contrary-vs-unsupportive "
            "classification (G6). For EACH open "
            "entry: fetch the actual opinion (candidates[].url first, else "
            "WebSearch search_query restricted to search_domains; <=2 fetch "
            "attempts), read the cited pincite, and record the finding with "
            "the located verbatim quote. On a does_not_support entry, "
            "classify: does the opinion merely fail to back the proposition "
            "(confirmed_does_not_support), or does it hold the OPPOSITE of "
            "what the brief claims (confirmed_contrary -- Tier 1 sanction "
            "risk)? Ingest as JSON list of "
            "{\"index\": N, \"finding\": \"confirmed_supports|"
            "confirmed_does_not_support|confirmed_contrary|"
            "confirmed_wrong_case|unable\", "
            "\"note\": \"...\", \"url\": \"...\", \"quote\": "
            "\"...\", \"text_file\": \"<fetched text path>\"} via: "
            "python3 cite_check_runner.py verify <answers.json>. "
            "render() is HARD-BLOCKED while entries stay open."),
        "entries": entries,
    }, open(VERIFY_JSON, "w", encoding="utf-8"), indent=1)
    return n_open


def phase2():
    t0 = time.time()
    ck = pickle.load(open(CKPT, "rb"))
    built = ck["built"]; cits = built["citations"]
    ot = ck["opinion_texts"]; su = ck["search_urls"]; sd = ck["search_details"]
    nc_map = ck.get("nc_map", {}); ru = ck.get("recap_url", {}); rs = ck.get("recap_src", {})
    ls_map = ck.get("lookup_status", {}); ln_map = ck.get("lookup_note", {})
    # A1/G11 (2026.07.14): direct opinion URLs (id-chain members inherit the
    # authority's URL) + untrimmed opinion texts for the quote-fidelity pass.
    ou = _propagate_opinion_urls(cits, ck.get("opinion_url", {}))
    ft = ck.get("full_texts", {})
    ftc = ck.get("full_text_complete", {})
    toa_index = built["toa_index"]
    client = helpers.get_client()

    def _fold(i, r):
        # Phase 2: citation-lookup status + reviewer note onto the result.
        r.lookup_status = ls_map.get(i)
        r.lookup_note = ln_map.get(i, "") or ""
        # Phase 5 (2026.07.04): report renders the lookup note itself.
        return i, r

    def _v(i):
        # Phase 6 (2026.07.04): thin wrapper over the SHARED implementation
        # in cite_check.verify_citation() -- the runner never re-implements
        # pipeline logic (audit 3.6).
        c = cits[i]
        r = cc.verify_citation(
            c, ot.get(i),
            client=client,
            opinion_url=ou.get(i) or ru.get(i, ""), opinion_source=rs.get(i, ""),
            nc_ok=nc_map.get(i), search_url=su.get(i, ""),
            search_detail=sd.get(i, ""),
            lookup_status=ls_map.get(i), lookup_note=ln_map.get(i, "") or "",
            full_text=ft.get(_ft_key(c, i)),
            full_text_complete=ftc.get(_ft_key(c, i)))
        return i, r

    rmap = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, r in ex.map(_v, list(range(len(cits)))):
            rmap[i] = r
    results = [rmap[i] for i in range(len(cits))]

    # Phase 6: shared TOA-coverage cross-check + result assembly.
    result = cc.finalize_results(built, results)
    # Step 6.6: build/refresh the must-verify manifest (re-applies prior
    # agent findings first, so a phase2 re-run never discards them).
    _n_open = _verify_review_entries(results, cits, ck.get("doc_name", ""))
    pickle.dump(dict(result=result, matter=ck.get("matter", ""), doc_name=ck.get("doc_name", "")),
                open(RESULT_PKL, "wb"))
    import cite_check_report as rep
    from collections import Counter
    print("[phase2] verdicts:", dict(Counter(rep._verdict(r) for r in results)), f"in {time.time()-t0:.1f}s")
    print(f"[phase2] verify manifest: {_n_open} open -> {VERIFY_JSON}")


def patch_gap():
    """Ingest agent-fetched opinion text for one gap-manifest entry.

    USAGE: python3 cite_check_runner.py patch_gap <index> <fetched_text_file> <url> [source] [note]
    Gated by _looks_like_opinion + _name_or_cite_match (Phase 2b contract);
    bounded at max_fetches attempts per gap.  On success the checkpoint is
    updated in place -- re-run `phase2` (then `render`) to fold it in.
    """
    idx = int(sys.argv[2])
    text_path = sys.argv[3]
    url = sys.argv[4] if len(sys.argv) > 4 else ""
    source = sys.argv[5] if len(sys.argv) > 5 else _clr._source_for_url(url)
    # Session E (2026.07.29): optional provenance note rendered on the card
    # (e.g., SCOTX decision date / parallel cite backfilled from the official
    # court site).
    note = sys.argv[6] if len(sys.argv) > 6 else ""

    ck = pickle.load(open(CKPT, "rb"))
    cits = ck["built"]["citations"]
    c = cits[idx]

    manifest = {"gaps": []}
    if os.path.exists(GAPS_JSON):
        manifest = json.load(open(GAPS_JSON, encoding="utf-8"))
    entry = next((g for g in manifest.get("gaps", []) if g.get("index") == idx), None)
    if entry is None:
        entry = {"index": idx, "attempts": 0, "max_fetches": 2, "status": "open"}
        manifest.setdefault("gaps", []).append(entry)
    if entry.get("status") == "patched":
        print(f"[patch_gap] #{idx} already patched; nothing to do")
        return
    if entry.get("attempts", 0) >= entry.get("max_fetches", 2):
        entry["status"] = "exhausted"
        json.dump(manifest, open(GAPS_JSON, "w", encoding="utf-8"), indent=1)
        print(f"[patch_gap] #{idx} REFUSED: fetch budget exhausted "
              f"({entry['attempts']}/{entry.get('max_fetches', 2)}) -- leave as Unable")
        return
    entry["attempts"] = entry.get("attempts", 0) + 1

    raw = open(text_path, encoding="utf-8", errors="replace").read()
    body = _clr.extract_opinion_body(raw, source, url)
    reasons = []
    if not _clr.CLResolver._looks_like_opinion(body):
        reasons.append("does not look like an opinion (too short / no opinion vocabulary)")
    if not _clr._name_or_cite_match(c, body):
        reasons.append("failed the name-or-cite identity gate")
    if _clr._looks_like_case_search_page(body):
        # Session E: the docket-number identity branch would accept a
        # txcourts case-search CASE page (it prints the docket); reject it.
        reasons.append("looks like a txcourts case-search CASE page, not an "
                       "opinion -- follow its opinion media link and patch "
                       "that document instead")
    if reasons:
        if entry["attempts"] >= entry.get("max_fetches", 2):
            entry["status"] = "exhausted"
        json.dump(manifest, open(GAPS_JSON, "w", encoding="utf-8"), indent=1)
        print(f"[patch_gap] #{idx} REJECTED ({entry['attempts']}/{entry.get('max_fetches', 2)}): "
              + "; ".join(reasons))
        return

    pin = _clr._pincite_from_citation(c)
    trimmed, _ = _clr._trim_to_pincite(
        body, pin, footnote_ref=_clr._pincite_footnote(c))
    ot = ck["opinion_texts"]; su = ck["search_urls"]; sd = ck["search_details"]
    nc = ck.setdefault("nc_map", {}); ru = ck.setdefault("recap_url", {})
    rs = ck.setdefault("recap_src", {})
    ot[idx] = trimmed
    ru[idx] = url
    rs[idx] = source
    ck.setdefault("opinion_url", {})[idx] = url
    ck.setdefault("full_texts", {})[_ft_key(c, idx)] = body
    # Fix 8 (Finding 1): record whether this stored copy plausibly runs to
    # the END of the opinion, at patch time. verify_citation uses this
    # attestation -- NOT a length comparison -- to decide whether a "quote
    # absent" result may be CONFIRMED (CRITICAL) or must degrade to review.
    ck.setdefault("full_text_complete", {})[_ft_key(c, idx)] = cc._opinion_is_complete(body)
    nc[idx] = True  # passed the identity gate on the full fetched body
    sd[idx] = (sd.get(idx, "") + " Patched from free source via gap manifest "
               f"({source}).").strip()
    if note:
        sd[idx] = (sd[idx] + " " + note).strip()
    pickle.dump(ck, open(CKPT, "wb"))
    # keep RES_PKL in sync so a later resolve re-run does not re-open the gap
    if os.path.exists(RES_PKL):
        rp = pickle.load(open(RES_PKL, "rb"))
        rp.setdefault("opinion_texts", {})[idx] = trimmed
        rp.setdefault("recap_url", {})[idx] = url
        rp.setdefault("recap_src", {})[idx] = source
        rp.setdefault("opinion_url", {})[idx] = url
        rp.setdefault("full_texts", {})[_ft_key(c, idx)] = body
        rp.setdefault("full_text_complete", {})[_ft_key(c, idx)] = cc._opinion_is_complete(body)
        rp.setdefault("nc_map", {})[idx] = True
        pickle.dump(rp, open(RES_PKL, "wb"))
    entry["status"] = "patched"
    entry["url"] = url
    entry["source"] = source
    json.dump(manifest, open(GAPS_JSON, "w", encoding="utf-8"), indent=1)
    print(f"[patch_gap] #{idx} PATCHED via {source} ({len(trimmed)} chars) -- re-run phase2")


def statutes():
    """I3 (2026.07.29): list statute-quote check targets.

    USAGE: python3 cite_check_runner.py statutes
    Writes cc_statutes.json: citations whose proposition both quotes text and
    cites a Texas code section, with candidate statute URLs.  The agent
    fetches the CURRENT section text and ingests via `statute_check`.
    """
    ck = pickle.load(open(CKPT, "rb"))
    cits = ck["built"]["citations"]
    targets = cc.statute_quote_targets(
        cits, ck.get("built", {}).get("argument_text") or "")
    out = os.path.join(_STATE_DIR, "cc_statutes.json")
    json.dump({"doc_name": ck.get("doc_name", ""), "targets": targets},
              open(out, "w", encoding="utf-8"), indent=1)
    print(f"[statutes] {len(targets)} target(s) -> {out}")


def statute_check():
    """I3 (2026.07.29): check quoted statutory text against a fetched section.

    USAGE: python3 cite_check_runner.py statute_check <index> <statute_textfile> [url]
    Appends a STATUTE CHECK note to the card's search detail (rendered on the
    report); NEVER changes a verdict.  Re-run `phase2` (then `render`) to fold
    the note in.
    """
    idx = int(sys.argv[2])
    path = sys.argv[3]
    url = sys.argv[4] if len(sys.argv) > 4 else ""
    ck = pickle.load(open(CKPT, "rb"))
    cits = ck["built"]["citations"]
    text = open(path, encoding="utf-8", errors="replace").read()
    note = cc.statute_check_note(cits[idx], text, url)
    if not note:
        print(f"[statute_check] #{idx}: no quoted statutory text to check")
        return
    sd = ck["search_details"]
    sd[idx] = ((sd.get(idx, "") + " ") if sd.get(idx) else "") + note
    pickle.dump(ck, open(CKPT, "wb"))
    if os.path.exists(RES_PKL):
        rp = pickle.load(open(RES_PKL, "rb"))
        rp.setdefault("search_details", {})[idx] = sd[idx]
        pickle.dump(rp, open(RES_PKL, "wb"))
    print(f"[statute_check] #{idx}: {note}")


def props():
    """Ingest Claude-written propositions from the review manifest pass.

    USAGE: python3 cite_check_runner.py props <answers.json>
    answers.json: [{"index": N, "proposition": "..."}]
    Light validation only (never verify a bare cite): min length, not a
    citation string.  Updates BUILT_PKL and (when present) CKPT in place;
    re-run `phase2` afterwards.  Provenance is recorded on the citation
    (proposition_source="agent") and surfaced as a reviewer note.
    """
    import re as _re
    answers = json.load(open(sys.argv[2], encoding="utf-8"))
    if isinstance(answers, dict):
        answers = answers.get("answers") or answers.get("entries") or []
    bd = pickle.load(open(BUILT_PKL, "rb"))
    ck = pickle.load(open(CKPT, "rb")) if os.path.exists(CKPT) else None
    targets = [bd["built"]["citations"]] + ([ck["built"]["citations"]] if ck else [])
    applied, rejected = 0, []
    for a in answers:
        i = int(a.get("index", -1))
        prop = (a.get("proposition") or "").strip()
        if i < 0 or i >= len(targets[0]):
            rejected.append((i, "index out of range")); continue
        if len(prop) < 25:
            rejected.append((i, "too short to be a checkable assertion")); continue
        bare = _re.fullmatch(r"[\w.,'&()\s-]*\d+\s+[A-Za-z.0-9]+\s+\d+[\w.,()\s-]*", prop)
        if bare and len(prop) < 80:
            rejected.append((i, "looks like a bare citation string")); continue
        for cits in targets:
            c = cits[i]
            c.proposition = prop
            c.proposition_review = False
            try:
                c.proposition_source = "agent"
            except Exception:
                pass
        applied += 1
    pickle.dump(bd, open(BUILT_PKL, "wb"))
    if ck is not None:
        pickle.dump(ck, open(CKPT, "wb"))
    # refresh manifest statuses
    if os.path.exists(PROPS_JSON):
        m = json.load(open(PROPS_JSON, encoding="utf-8"))
        done = {int(a.get("index", -1)) for a in answers}
        for e in m.get("entries", []):
            if e["index"] in done:
                e["status"] = "supplied"
        json.dump(m, open(PROPS_JSON, "w", encoding="utf-8"), indent=1)
    print(f"[props] applied {applied}; rejected {len(rejected)}"
          + (f" -> {rejected}" if rejected else "") + " -- re-run phase2")


def verify():
    """Ingest agent findings for the Step 6.6 must-verify manifest.

    USAGE: python3 cite_check_runner.py verify <answers.json>
    answers.json: [{"index": N,
                    "finding": "confirmed_supports|confirmed_does_not_support|confirmed_contrary|confirmed_wrong_case|unable",
                    "note": "...", "url": "...", "quote": "...",
                    "text_file": "<path to the fetched opinion text, optional>"}]

    Additive provenance layer -- verify()/verdict taxonomy UNTOUCHED.  An
    OVERRIDE to Verified is granted ONLY on finding=confirmed_supports with
    the fetched opinion text supplied AND passing the same evidence bar as
    the Connaughton quote override: _looks_like_opinion + the
    _name_or_cite_match identity gate on the fetched body, and
    cc_quote_matcher.verify_quote() VERBATIM (>0.85, alteration-normalized)
    locating the agent's quote in it.  Everything else lands as a loud
    reviewer note beside the unchanged machine verdict.  Idempotent
    re-apply, like `props`.
    """
    import cc_quote_matcher as qm
    answers = json.load(open(sys.argv[2], encoding="utf-8"))
    if isinstance(answers, dict):
        answers = answers.get("answers") or answers.get("entries") or []
    ck = pickle.load(open(RESULT_PKL, "rb"))
    results = ck["result"]["citations"]
    manifest = {"entries": []}
    if os.path.exists(VERIFY_JSON):
        manifest = json.load(open(VERIFY_JSON, encoding="utf-8"))
    ents = {int(e["index"]): e for e in manifest.get("entries", [])}
    _FINDINGS = ("confirmed_supports", "confirmed_does_not_support",
                 "confirmed_contrary", "confirmed_wrong_case", "unable")
    applied, overrides, rejected = 0, 0, []
    for a in answers:
        i = int(a.get("index", -1))
        finding = (a.get("finding") or "").strip()
        if i < 0 or i >= len(results):
            rejected.append((i, "index out of range")); continue
        if finding not in _FINDINGS:
            rejected.append((i, "unknown finding %r" % finding)); continue
        e = ents.get(i)
        if e is None:
            rejected.append((i, "not in the must-verify manifest")); continue
        r = results[i]
        c = r.citation
        note = (a.get("note") or "").strip()
        url = (a.get("url") or "").strip()
        quote = (a.get("quote") or "").strip()
        # Evidence-gated override (attorney-approved 2026.07.10).
        override = False
        if finding == "confirmed_supports" and a.get("text_file"):
            raw = open(a["text_file"], encoding="utf-8", errors="replace").read()
            body = _clr.extract_opinion_body(raw, _clr._source_for_url(url), url)
            gates = []
            if not _clr.CLResolver._looks_like_opinion(body):
                gates.append("fetched text does not look like an opinion")
            if not _clr._name_or_cite_match(c, body):
                gates.append("fetched text failed the name-or-cite identity gate")
            if len(quote) < 25:
                gates.append("no >=25-char verbatim quote supplied")
            elif qm.verify_quote(quote, body).result is not qm.QuoteMatch.VERBATIM:
                gates.append("quote not located VERBATIM in the fetched opinion")
            if gates:
                note = (note + " " if note else "") + \
                    "(override refused: " + "; ".join(gates) + ")"
            else:
                override = True
        e.update({"finding": finding, "note": note, "url": url,
                  "quote": quote, "override": override})
        e["attempts"] = e.get("attempts", 0) + 1
        e["status"] = "unable" if finding == "unable" else "worked"
        _apply_verification(r, e)
        applied += 1
        overrides += 1 if override else 0
    pickle.dump(ck, open(RESULT_PKL, "wb"))
    json.dump(manifest, open(VERIFY_JSON, "w", encoding="utf-8"), indent=1)
    still = sum(1 for e in manifest.get("entries", [])
                if e.get("status") == "open")
    print(f"[verify] applied {applied} ({overrides} evidence-gated overrides); "
          f"rejected {len(rejected)}" + (f" -> {rejected}" if rejected else "")
          + f"; open remaining={still}")


def goodlaw():
    # Treatment-signal pass (2026.07.06, attorney-approved design). Runs AFTER
    # phase2. Additive: own state file, own module -- verify()/verdicts
    # untouched. Resumable: re-run until it prints "treatment: done".
    t0 = time.time()
    import cc_goodlaw as gl
    ck = pickle.load(open(CKPT, "rb"))
    rp = pickle.load(open(RES_PKL, "rb")) if os.path.exists(RES_PKL) else {}
    lookup_map = (rp.get("lookup") or {}).get("map", {})
    state = gl.run(ck, lookup_map, deadline=t0 + 38)
    done = sum(1 for a in state["authorities"].values()
               if a["status"] in ("done", "not_checked"))
    print(f"[goodlaw] {done}/{len(state['authorities'])} authorities done "
          f"in {time.time()-t0:.1f}s; "
          f"api_calls_total={state.get('api_calls_total', 0)}")
    if gl.is_done(state):
        from collections import Counter
        print("[goodlaw] classes:", dict(Counter(
            (a["classification"] or "none")
            for a in state["authorities"].values())))
        print("treatment: done")
    else:
        print("treatment: incomplete -- re-run goodlaw")


def render():
    import cite_check_report as rep
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/citecheck_final.html"
    ck = pickle.load(open(RESULT_PKL, "rb")); result = ck["result"]
    # Step 6.6 HARD GATE (the attorney 2026.07.10, gap-loop precedent): render
    # refuses while must-verify entries are open.  Work the loop, ingest via
    # the `verify` verb, then re-run render.
    if os.path.exists(VERIFY_JSON):
        try:
            _vm = json.load(open(VERIFY_JSON, encoding="utf-8"))
        except Exception:
            _vm = {}
        if _vm.get("doc_name") == ck.get("doc_name"):
            _open = [e["index"] for e in _vm.get("entries", [])
                     if e.get("status") == "open"]
            if _open:
                print(f"[render] BLOCKED: {len(_open)} must-verify entries open "
                      f"(indexes {_open}) in {VERIFY_JSON} -- run the Step 6.6 "
                      "loop (fetch each entry's opinion, read the cited "
                      "pincite) and ingest findings via: "
                      "python3 cite_check_runner.py verify <answers.json>")
                sys.exit(2)
    meta = {"jurisdiction": result.get("jurisdiction"), "chunking": result.get("chunking"),
            "matter": ck.get("matter", ""), "document_name": ck.get("doc_name", ""),
            "non_case_references": result.get("non_case_references", []),
            "toa_only_cases": result.get("toa_only_cases", []),
            "body_only_cases": result.get("body_only_cases", [])}
    if os.path.exists(GOODLAW_PKL):
        import cc_goodlaw as gl
        _st = gl.load_state()
        if _st and _st.get("doc_name") == ck.get("doc_name"):
            meta["treatment"] = gl.summary(_st)
    # Application-sentence build (2026.08.04): re-run the detector +
    # verified-sibling cross-check at render time so Step 6.6 overrides
    # ingested since phase2 update sibling status (idempotent).
    import cc_application as ccapp
    ccapp.attach(result["citations"], result.get("application_roster"))
    html = rep.render_html(result["citations"], meta)
    open(out_path, "w", encoding="utf-8").write(html)
    print(f"[render] {len(html)} bytes -> {out_path}")


if __name__ == "__main__":
    _CMDS = {"build": build, "resolve": resolve, "phase2": phase2, "render": render,
             "patch_gap": patch_gap, "props": props, "goodlaw": goodlaw,
             "verify": verify, "statutes": statutes, "statute_check": statute_check}
    if len(sys.argv) < 2 or sys.argv[1] not in _CMDS:
        print("usage: cite_check_runner.py {%s} [args]" % "|".join(_CMDS),
              file=sys.stderr)
        sys.exit(2)
    _CMDS[sys.argv[1]]()
