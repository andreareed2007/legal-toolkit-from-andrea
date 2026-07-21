---
name: cite-check
description: "Cite-check a brief or motion using the eyecite + CourtListener + Isaacus pipeline. Use this skill whenever the user asks to cite-check, verify citations, check the cites, or validate citations in a document. Triggers on: 'cite-check this', 'cite-check this brief', 'run a cite-check', 'verify these citations', 'check the cites in this', 'cite-check [filename]', 'verify the citations', 'check this filing', or any upload of a .docx or .pdf followed by 'check this' or 'verify this'. Also triggers on 'are these cites right', 'validate the citations', or 'do these cases support what they say'. This skill uses eyecite for citation detection, CourtListener citation-lookup for opinion resolution, and Isaacus verify() for AI-powered support classification. For topic-based case research or case lookups, use caselaw-retriever instead."
---

> **Version:** v2026.07.20-1 (shared edition) · **Last updated:** 2026.07.20

## HARD RULE: Never Read PDFs Directly in Cowork

**NEVER use the Read tool on a .pdf file. NEVER use bash/pdfplumber/pdftotext to extract PDF text inline.** Always look for a pre-converted `_COWORK.md` file in the matter's `_cowork_txt/` subfolder first. If no converted version exists, invoke the `pdf-to-cowork` skill to create one before proceeding. This rule has no exceptions.

---

# Cite-Check — eyecite + CourtListener + Isaacus Verification Pipeline

Verifies every citation INSTANCE in a brief against its source opinion. Detection and parsing: **eyecite** (structural, offline, exact spans, pincites, short-form/Id./supra grouping). Resolution: **CourtListener citation-lookup** (batched, primary) with tiered name search, RECAP, and a mandatory free-source gap loop behind it. Proposition support: **Isaacus verify()** (universal classifier) with star-page passage location and an Answer Extractor second opinion on close calls.

**This is the primary cite-check path.** For topic research, case lookups, or research memos, use caselaw-retriever + caselaw-analyst.

| User Request | Skill |
|---|---|
| "Cite-check this brief" / "verify these citations" | **This skill** |
| "Find cases on [topic]" / "look up [case]" | caselaw-retriever |
| "Write a research memo" | caselaw-analyst |

## THE WORKFLOW (one path — run every step, in order)

All commands run from this skill's `scripts/` folder. Every step below is REQUIRED; the gap loop (Step 5) and the proposition pass (Step 6) are not optional extras — skipping them is the "gives up too early" and "1/3 wrong propositions" failure the 2026.07 rebuild eliminated.

### Step 0 — Environment (every session; nothing persists)

```bash
pip install isaacus "httpx[socks]" "semchunk>=4.1" requests "eyecite==2.7.8" pytest --break-system-packages -q 2>&1 | tail -1
export CC_STATE_DIR=/tmp/cc_state    # set in EVERY shell call (calls share no env); runner default is /tmp/cc_state since v2026.07.19-1, but stay explicit
mkdir -p /tmp/cc_state
```

Verify imports before running anything (from the Integration folder):

```bash
cd "<this skill's scripts/ folder>" && python3 -c "
from eyecite import get_citations; import cite_check, cite_check_report, cl_resolver, cite_check_runner
from isaacus_config import get_client; get_client(); print('pipeline ready')"
```

If this fails, STOP and report the actual traceback. eyecite is HARD-pinned at 2.7.8 (validated; see ENVIRONMENT_SETUP.md). Upgrading eyecite is a deliberate act: bump the pin, then re-validate against a brief you know well BEFORE any real brief.

**If CC_STATE_DIR ever returns PermissionError:** a stale state dir from a previous VM survived with a different owner. Point CC_STATE_DIR at a fresh directory (e.g. `/tmp/cc_state_2`) and re-run. Old state files are usually still readable — saved props answers can be copied out of them.

### Step 1 — Intake

1. **Uploaded file:** use it from the uploads folder. `.docx`/`.dotx` → auto-extracted by `cite_check_runner.build()` via `docx_to_text` (footnotes spliced INLINE at their reference markers); do NOT pre-extract with python-docx and NEVER append footnotes as a trailing block (they get stripped as back matter and detached from their sentences). `.pdf` → the `_COWORK.md` per the HARD RULE; `.txt`/`.md` → direct.
2. **Named file / matter:** search the matter's folder under the profile's `matter_root` (`~/.legal-skills/config.json`); "latest" = most recent .docx/.pdf.
3. **Ambiguous:** ONE AskUserQuestion.

Also ask (or infer) the report format — HTML (default), .docx, or .txt — and detect the matter (file path first, then caption/party match; ask if ambiguous).

### Step 2 — build

```bash
python3 cite_check_runner.py build "<brief_path>" "<MATTER TAG>" "<doc name>"
```

Eyecite detection + structural proposition extraction, no API calls. Writes the citation checkpoint AND `$CC_STATE_DIR/cc_props_review.json` — the proposition-review manifest for Step 6. A new build CLEARS all downstream state (resolve/ckpt/gaps/result) — state is index-keyed, so never mix documents in one state dir.

### Step 3 — resolve (repeat until the gap manifest is written)

```bash
python3 cite_check_runner.py resolve    # re-run; each window resumes where the last stopped
```

Batched CourtListener citation-lookup is the PRIMARY resolver (unique reporter cites, ≤250/request, ≤64k chars, paced 60 valid cites/min against the ~45s window — it resumes across windows). Per-cite statuses: 200 exact cluster; 300 "Ambiguous citation" note; 400 "Reporter not recognized — possible typo" note (a real cite-check catch — surface it); 404 → tiered name search → RECAP → gap manifest. Re-run `resolve` until it prints `gap manifest: N open` (or remaining=0 with no gaps). It prints `api_calls_this_window` / cumulative total.

### Step 4 — MANDATORY gap loop (bounded, automatic — no user input)

`$CC_STATE_DIR/cc_gaps.json` lists every unresolved cite with candidate URLs, a search query, and allowed domains. For EACH open gap, in order:

1. **Candidates first.** `web_fetch` each `candidates[].url` (nycourts_reporter / Justia for NY Slip Op (U); HTML preferred; a single targeted reporter PDF fetch is acceptable when no HTML variant exists). Save the fetched text to a file.
2. **No candidates (reported cites, WL orders):** run WebSearch for `search_query`, RESTRICTED to the gap's `search_domains` (= `cl_resolver.REPORTED_SEARCH_DOMAINS`: law/cases/supreme.justia.com, caselaw.findlaw.com, txcourts.gov — plus nycourts.gov on the candidate leg). Fetch the best hit.
3. **Patch it in (the ONLY path for external opinion text):**

```bash
python3 cite_check_runner.py patch_gap <index> <fetched_text_file> "<url>" [source]
```

`patch_gap` gates on `_looks_like_opinion` + the `_name_or_cite_match` identity gate, trims to the pincite, and updates the checkpoint in place. It REFUSES wrong opinions and enforces the budget: **≤2 fetch attempts per gap**, then the gap stays Unable — honestly. Never edit pickles by hand; `patch_gap` is idempotent (refuses already-patched entries).

A gap left Unable after the loop genuinely means: not on CL Opinions, RECAP, Justia, FindLaw, nycourts, txcourts, or a domain-restricted web search — and the card says what was tried.

### Step 5 — MANDATORY proposition pass (automatic — no user input)

Open `$CC_STATE_DIR/cc_props_review.json`. For EACH entry (failed or suspect extraction — includes heading-glue, short-fragment, runaway, mid-sentence, and shared-sentence suspects), read the entry's `paragraph` and write the proposition the brief attributes to THIS citation instance:

Citations INSIDE a brief footnote are handled automatically (2026.07.04 footnote fix): a footnote with substantive content supplies its own sentence; a bare cite-drop footnote falls back to the body sentence at the marker and, failing that, lands in this manifest. When an entry's paragraph IS a footnote, write the proposition from the footnote's content.

- The governing assertion, quoted or closely paraphrased **FROM THE BRIEF** — never from memory of the case. HEW TO THE BRIEF'S WORDING: a "the defendant"-for-"[name]" paraphrase measurably degrades verification (Session 2 lesson).
- No signals, no record cites, no citation strings. 1–2 sentences.
- If the paragraph attributes nothing checkable, omit the entry — it renders "review required," which beats confident garbage.

Write the answers as JSON `[{"index": N, "proposition": "..."}]` and ingest (the ONLY path for hand-written propositions — validated, provenance-marked `proposition_source="agent"`, surfaced on the card):

```bash
python3 cite_check_runner.py props <answers.json>
```

`props` re-application overwrites cleanly. NOTE: a re-`build` resets propositions — re-apply the saved answers file after any rebuild (answers live in CC_STATE_DIR, which does NOT persist across VM restarts; regenerate via the manifest if lost).

### Step 6 — phase2 (verification)

```bash
python3 cite_check_runner.py phase2
```

Isaacus verify() per instance (universal classifier, >0.5 supports, quote override, thin-text guard — semantics LOCKED), with `chunking_options={"overlap_ratio": 0.15}` and `scoring_method="auto"`; star-page location of the supporting passage (nearest preceding `*N` before the top chunk's offset); Answer Extractor second opinion on Flagged / Somewhat / Does-Not-Support cards. Never verifies a bare case name — empty propositions short-circuit to "Proposition Not Extracted — Review Required."

### Step 6.5 — goodlaw (treatment-signal pass; repeat until "treatment: done")

```bash
python3 cite_check_runner.py goodlaw    # re-run; resumes like resolve
```

Good-law EVIDENCE pass (2026.07.06, author-approved design): for each unique resolved authority, a CourtListener `cites:()` full-text probe searches ALL citing opinions for treatment terms (overruled, abrogated, disapproved, superseded by statute, receded from, no longer good law; caution: distinguished, declined to follow, limited to its facts, called into doubt); only probe hits are fetched and scanned for PROXIMITY to the case's name/cite, behind four guards (procedural "objections overruled," negation, shared-surname, nearest-cite incl. WL/LEXIS). Classes per authority: NEGATIVE SIGNAL (strong verb, same-or-higher court or later history), CAUTION, NO SIGNAL FOUND (with the coverage sentence), NOT CHECKED (patched-gap/RECAP/unresolved — no citation graph). State: `cc_goodlaw.pkl` in CC_STATE_DIR, own file, resumable windows, deterministic order.

**Language discipline (LOCKED):** the report presents evidence — quote, citing case, link — never a verdict. The coverage sentence is the ceiling of the claim. Treatment is a SEPARATE AXIS from the 11 verdicts; it never changes a verdict.

### Step 6.6 — MANDATORY auto-resolved verification loop (bounded, automatic — no user input)

`$CC_STATE_DIR/cc_verify_review.json` (written by phase2, refreshed on every re-run) lists every card whose verdict is adverse or identity-doubtful (Does Not Support, Cited as Contrary, Identity Unconfirmed, Page Not Found — plus low-confidence Somewhat per the `VERIFY_SOMEWHAT_MAX_CONF` tunable) AND whose authority is high-risk to auto-resolution: unpublished NY slip op `(U)`, non-reporter/free-source copy (justia, nycourts, findlaw, RECAP, patched-gap), or identity-flagged record. On these sources an adverse verdict is as likely a resolution artifact as a real defect. For EACH open entry, in order:

1. **Fetch the actual opinion.** `web_fetch` each `candidates[].url` first (same domain rules as the Step 4 gap loop); no candidates → WebSearch `search_query` RESTRICTED to `search_domains`. **≤2 fetch attempts per entry.** SAVE the fetched text to a file — the file is what enables the override.
2. **Read the cited pincite** in the fetched opinion and decide: CONFIRMED-SUPPORTS / CONFIRMED-DOES-NOT-SUPPORT / CONFIRMED-WRONG-CASE / UNABLE. For confirmed-supports, copy the supporting language VERBATIM from the opinion.
3. **Ingest (the ONLY path for verification findings):**

```bash
python3 cite_check_runner.py verify <answers.json>
# [{"index": N, "finding": "confirmed_supports|confirmed_does_not_support|confirmed_contrary|confirmed_wrong_case|unable",
#   "note": "...", "url": "...", "quote": "<verbatim from the opinion>", "text_file": "<saved fetch>"}]
```

An OVERRIDE to Verified is granted ONLY on `confirmed_supports` with `text_file` supplied and passing the same evidence bar as the quote override: `_looks_like_opinion` + the name-or-cite identity gate on the fetched body, and `cc_quote_matcher.verify_quote()` VERBATIM. Everything else lands as a loud "Manual verification (Step 6.6)" note beside the unchanged machine verdict — and the machine verdict stays visible either way ("machine: Does Not Support → agent-verified: Supports"). Same "leave honestly Unable with a trail" rule as the gap loop. **render() is HARD-BLOCKED while entries stay open** — this loop cannot be skipped (that dependency on a human catching it post-delivery WAS the Doc 89 bug (see do-not-regress notes)).

### Step 7 — render + save

```bash
python3 cite_check_runner.py render "<out_path>.html"
```

render REFUSES (exit 2) while Step 6.6 must-verify entries are open — work the loop and ingest via `verify` first.

For .docx: load the result pickle and call `cite_check_report.render_docx(result["citations"], meta, out_path)` (requires `npm install docx`; set `NODE_PATH` if installed outside the project). For .txt: `render_markdown`.

**Save with the matter's files:**

```
IF matter identified:  [matter_root]/[Matter Folder]/YYYY.MM.DD Cite Check Report - [Brief Name].{ext}
ELSE:                  the current working folder: YYYY.MM.DD Cite Check Report - [Brief Name].{ext}
```

Dots in the date. Title case.

### Step 8 — Present to the user

1. `computer://` link to the saved report.
2. One summary line: "{n} instances checked: {verified} verified, {somewhat} somewhat, {page-unverified}, {flagged} flagged, {dns} does not support, {unable} unable."
3. Call out: does-not-support cards, NEGATIVE/CAUTION treatment rows (quote the strongest passage), gaps left Unable (with what was tried), TOA coverage discrepancies, typo notes (400s), agent-supplied propositions count, and agent-verified corrections from Step 6.6 (e.g. “3 machine-adverse cards manually verified: 3 confirmed supporting” — name the overridden cards).
4. Do NOT repeat the report contents in chat.

### One-shot path (claude.ai / small briefs only)

`cite_check(brief_text, resolver.resolve_opinion_text)` runs the SAME shared pipeline in one call (it routes through the batched citation-lookup automatically when given a CLResolver-bound callback). Use it only where there is no shell cap and the brief is small; in Cowork, always use the runner. Both paths call the same `build_citations` / `verify_citation` / `finalize_results` implementations — there is ONE pipeline.

## Verdict taxonomy (locked — all eleven render, incl. in the DOCX/JS maps)

Verified · Somewhat Supports · Supported · Page Unverified · Text Unavailable · Flagged · Does Not Support · Cited as Contrary · Identity Unconfirmed · Page Not Found · Proposition Not Extracted — Review Required · Unable to Verify. Keep Python `_VERDICT_ORDER` and the JS `verdictOrder`/label/color maps in sync. Pincite verdicts are SOURCE-GATED: "Page Not Found" only on reporter-paginated CL Opinions copies; RECAP/PACER/free copies → "Supported · Page Unverified." Cards show: full brief cite + pincite → verdict → proposition (+ agent provenance) → confidence (two decimals on CLOSE CALLS only) → passage (+ "Supporting passage located at *N") → second opinion → links → notes (reviewer note, citation-lookup note). Status Key, TOA Coverage, and Non-Case sections stay.

## Do-not-regress notes (the expensive lessons)

- **Detection is eyecite on EXACTLY the preprocessed argument text** — never apply eyecite's `clean_text` after our preprocessing (length-changing transforms break spans; TOA 29→28 regression class). No hardcoded reporters or case names anywhere (prime directive).
- **Resolver: no first-result fallback, ever.** Identity gate on the FULL opinion (`_resolved_name_cite_ok`); `_trim_to_pincite` keeps the `_HEAD_KEEP` head slice. Resolution key is `reporter_cite_str()` (eyecite body cite primary, TOA fallback); cache keys on the normalized reporter cite — no prefix fuzzy matching.
- **Resolver identity hardening (2026.07.09, do not regress):** a citation-lookup 200 proves the cite ADDRESS exists, never that it belongs to the case the brief names — the caption check (`_lookup_name_check`, zero-shared-token mismatch only, abbreviation-expanded) must stay on every lookup acceptance path (batched + Tier 0). A name-tier win never proved the address — `_cite_address_check` (reporter-family normalized: N.E.2d ≡ N.E.3d, U.S. ≠ S. Ct.) fires identity treatment on same-family-different-address, and a lookup-200 caption mismatch carries through transitively (`_lookup_addr_mismatch`) to any later name-tier/RECAP win (the TIG/fake-06 class). Resolution is KEPT on contradiction (the locked taxonomy renders Identity Unconfirmed; a located verbatim quote still overrides); only the caption-mismatched lookup cluster itself is rejected.
- **Quote fidelity (2026.07.09, do not regress):** the quote override runs on `cc_quote_matcher.verify_quote()` — legal alterations ([T]he openings, [bracketed substitutions], ellipses, smart quotes) are normalized BEFORE matching, so a faithful-but-altered quote is VERBATIM and keeps the Connaughton override; never revert to the raw-substring check (it false-negatived on all three alteration forms). FABRICATED (<0.6 similarity on a ≥25-char quote) and CLOSE (0.6–0.85, with the closest passage) produce `quote_note`, which is ADDITIVE and must LEAD `_reviewer_note` output — a misquote warning never hides behind a moderate-support observation and never changes the verdict. `quote_in_opinion` stays as the legacy fallback; do not delete (runner import).
- **Five-tier severity + report tabs (Phase 8, 2026.07.15, do not regress):** tiers are 1 CRITICAL / 2 UNVERIFIED / 3 FIX / 4 REVIEW / 5 PASS. UNVERIFIED (purple) is its own bucket for authorities the tool could not check at all and OUTRANKS Fix; treatment signals never exceed REVIEW; bracket substitutions/dash variants are permitted alterations (never MISQUOTE); every misquote card carries the word diff; every displayed passage goes through clean_passage(). Proposition rule: a quoted rule applied to facts is propositioned as the RULE the brief attributes, never the argumentative application.
- **Atlas as-filed QA (2026.07.15-4, do not regress):** (a) the TOA parser keeps entries whose caption has no " v. " via `_NON_ADVERSARY_CAPTION_RE` (Estate of / Matter of / In re / In the Matter of / Ex parte / Application of / Petition of / In the Interest of / Guardianship of / Conservatorship of / Marriage of) — the old " v. "/"In re"-only gate silently dropped two 'Estate of …' / 'Matter of …' captions that WERE in the brief's TOA, producing false body-only flags. A case genuinely absent from the brief's TOA must still surface — that is a real finding, not a bug. (b) The 25-character quote floor is retired in `check_quote`: an empty `quote_results` WITH quote marks present renders `present_unlocated` (REVIEW — "verify by hand"), never "nothing to check"; a Step 6.6 `verification_override` + `confirmed_supports` renders `verified_agent` (the hammer/nail Cit 19 miss). (c) `verify_brackets` (REVIEW, `must_verify`) fires when a CLOSE result's brief-side diff changes are bracket-dominated (≥ the plain-word changes) and/or ellipsis-driven — the quoted words track the opinion, only bracket substitutions/ellipses differ, so it is a fair-substitution manual check, NOT a misquote; a genuine unbracketed word-swap still lands MISQUOTE (DEFECT). (d) `_balance_display_quotes` (report, display only) restores a stripped leading quote mark so the reader sees where the quoted span starts — it never alters verified text.
- **Fabrication confirmation gate (Phase 7, 2026.07.15, do not regress):** a FABRICATED grade on ANY quoted span (long or short) may drive Tier-1 ONLY when the absence was confirmed against the COMPLETE, untrimmed opinion (`confirmed: true` in quote_results). No full text → 'NOT CONFIRMED' REVIEW + auto-enqueue in the Step 6.6 must-verify loop; never a confident CRITICAL against a partial copy. Confirmed-absent SHORT quotes ARE Critical (author QA 2026.07.15) but stay out of the support override. `full_text` must remain threaded on every resolution path (ResolutionLog.full_text) and stored unconditionally in the checkpoint. confirmed_wrong_case + lookup-404 renders coverage REVIEW, not CRITICAL, and suppresses Quote/Support escalation.
- **Star pages:** `(?!\d)` after the page number, never `\b` (star-page glue). PACER "Page N of M" stamps are NOT reporter pagination. A date parenthetical must never ship the year as a pincite.
- **Footnotes (2026.07.04, do not regress):** brief-side — `find_footnotes` blocks END at the paragraph break (the old next-footnote-only rule swallowed all following body text), and `foots` MUST be passed to `extract()` on every path (it was silently dead on the live path — a wrong-proposition card in the gold set). Case-side — a pincite carrying `n.N` triggers the endnote-tail append in `_trim_to_pincite` (CL renders footnotes AFTER the body; the page window otherwise excludes the cited text — a false "Does Not Support" in the gold set); the endnote block is found by its own lead pattern, never by the last star marker (footnote citations carry star-pincites of their own). The report renders "Supporting passage located: in the opinion's footnotes" for passages behind the sentinel.
- **docx intake (2026.07.13, do not regress):** Word files (.docx/.dotx) are auto-extracted by `cite_check_runner.build()` via `docx_to_text`, which splices each footnote INLINE at its reference marker — footnotes MUST NOT be appended as a trailing block (stripped as back matter, detached from their sentences). A QA-brief run lost ALL footnote authorities that way (6 cites detected vs 21 real), including a fabricated quotation cited in a footnote as a bare "Id." pincite pointing at an entirely different case. The prior flow choked on .docx (UnicodeDecodeError); a naive python-docx paragraph extract silently dropped every footnote.
- **Quote-adjacent id. / spec #6 (2026.07.13, do not regress):** an `id.` is KEPT (not folded) when a direct quotation sits in its OWN or the immediately preceding sentence (the footnote-drops-a-bare-cite-for-a-body-quote pattern); the quote detector (`_ID_QUOTE_RE`) is curly-quote aware. Verified against the author's regression harness post-change.
- **Quote-fidelity dangling tail (2026.07.13, do not regress):** `cite_check.extract_verbatim_quote` falls back to a DANGLING opening-quote tail when American-style punctuation (period inside the closing quote) splits a quotation across sentences — without it the FABRICATED quote-fidelity check silently skipped and a fabricated "quotation" rode thematic support to a false Verified. The FABRICATED note stays additive (never changes the verdict — locked taxonomy).
- **Citation-mask name arm is TOKEN-BOUNDED** (`_NAME_TOKEN`, 2026.07.04): never revert to `[A-Z][^*\n]+?` — an unbounded name arm masks entire prose sentences in front of short-form cites, corrupting sentence segmentation and shipping citation sentences as propositions (extractor answer key was silently degraded from 18/20 to 11/20 by this one regex).
- **TOA excised positionally with a loud post-condition; parser tries every occurrence.** TOA is a coverage index, off the resolution path.
- **Never verify a bare case name.** "" → review-required verdict, no score shown.
- **Regression protection:** the author's regression-gate harness (gate scripts, recorded resolver cassettes, and graded gold sets) is NOT included in this distribution — the fixtures are built from real briefs and cannot ship. If you modify pipeline code, build your own gold set first: run the pipeline on a brief you know thoroughly, hand-grade the results, and re-run after every change. Do not trust any code change that has not been checked against a known-good brief.
- **Runner ops:** state in CC_STATE_DIR; resolve windows respect the ~45s cap (never block past ~38s); lookup chunks are deterministic so resume windows agree.
- **Step 6.6 must-verify loop (2026.07.10, do not regress):** auto-resolved adverse verdicts on unpublished NY slip ops / free-source copies are as likely resolution artifacts as real defects — the loop exists because a 2026.07.09 production run shipped three false-negative slip-op verdicts that a human caught only post-delivery. The render hard-block is the point: never bypass it by editing the manifest by hand, and never grant the override without the fetched text passing the identity gate + VERBATIM quote match. The manifest preserves worked findings across phase2 re-runs (`_apply_verification`) — a re-run must never silently discard agent verification work. Any change to the trigger predicate or verdict layer must be re-checked against a hand-graded brief (see Regression protection).
- **Goodlaw (2026.07.06, additive — do not couple):** verify() and the verdict taxonomy are UNTOUCHED; Treatment is its own axis, own module (`cc_goodlaw.py`), own state file (`cc_goodlaw.pkl`), own regression check (re-grade a known brief before AND after every goodlaw change). A hollow scan (fetch failure, empty cluster) must land NOT CHECKED, never "no signal" (the Anderson bug). The four scan guards exist because the good-law CONTROL fixture false-positived without them — never remove one without re-checking against a known brief. `build` clears cc_goodlaw.pkl with the rest of downstream state. Without goodlaw state, render output is unchanged (additive by construction).

## Credential discipline

- Isaacus: `ISAACUS_API_KEY` env var → `api_keys.isaacus` in `~/.legal-skills/config.json` (environment-setup skill) → `ISAACUS_CONFIG.txt` next to the scripts (via `isaacus_config.py`).
- CourtListener: `COURTLISTENER_API_TOKEN` env var → `api_keys.courtlistener` in the same profile → `CL_CONFIG.txt` next to the scripts (via `cl_resolver.py`).

`Path.read_text()` only. Never echo, print, log, or include in errors. If exposed: first sentence of the next response is "rotate [service] now."

## Known limitations

1. **Coverage ceiling** = CL Opinions + RECAP + the free-source loop. Some NY trial-court slip ops and unposted WL orders genuinely are not there; UNABLE with the search trail is correct behavior. The Step 6.6 loop now backstops free-source RESOLUTION errors (wrong record, trimmed copy missing the operative sentence) — but its verification is bounded to the same free-source coverage as the gap loop: an opinion the loop cannot fetch stays at the machine verdict with an honest UNABLE note.
2. **Textual support ≠ factual applicability.** Moderate-confidence cards get a reviewer note, not an automatic downgrade.
3. **Treatment pass is evidence-only** (Step 6.5): probe-first over CourtListener's citation graph; it cannot see treatment outside CL coverage, court-level weighting is approximate (an MD/NY-style "Court of Appeals" high court ranks as intermediate — a true NEGATIVE can render as CAUTION), and patched-gap/RECAP authorities are honestly NOT CHECKED. It is not a Shepard's/KeyCite substitute and never claims one.
4. **TOA parsing is best-effort;** empty `toa_index` disables the coverage section, it never false-flags. Non-adversary captions (Estate of / Matter of / In re / Ex parte, ...) are captured (2026.07.15-4); a case truly missing from the brief's TOA is reported as a real coverage finding, not suppressed.
5. **Non-case section is structural** (§/rule tokens) since the enricher left the live path — thinner than the enricher era. Statutes and record cites are filtered, not verified.
