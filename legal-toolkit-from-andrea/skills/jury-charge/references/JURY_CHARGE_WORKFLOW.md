# Jury Charge Workflow — Matter-Agnostic Checklist

**Companion doc:** `JURY_CHARGE_CONDITIONAL_ROUTING.md` (the routing-graph model this workflow depends on)
**Enforced by:** the `jury-charge` skill. This document is the human-readable statement of the process; the skill mechanically enforces the gates.

## Why the gates exist

A jury charge drafted from a model's training-data approximation of pattern-instruction language — instead of the parsed, published pattern text — produces a charge full of open verification items, each a potential appellate error. The two failure modes this workflow eliminates:

1. **Hallucinated pattern language.** Fixed by the mandatory pattern-lookup gate (Phase 2). No instruction is drafted until the verbatim section file has been read in the current session.
2. **Routing cascade errors.** A single wrong question number in an "If you answered..." instruction silently corrupts every downstream answer. Fixed by never hand-writing routing text: the routing map is data, and routing instructions are generated and validated by script (Phase 3–4).

## Phase 0 — Setup and posture (STOP points)

- [ ] **Jurisdiction gate:** ask which jurisdiction's pattern instructions govern. Resolve the library via the `libraries.json` registry under the configured `jury_library_root`. If the library is missing or its manifest says `validated: false`, STOP — build/validate it first per `JURY_CHARGE_LIBRARY_BUILDER.md`. Never draft instructions from memory for an unsupported jurisdiction.
- [ ] Identify the matter, court, and cause number. Determine court type (`tx-state`, `business`, `federal`, `ny-state`, `ca-state`) — this selects the court-filing module used at assembly time.
- [ ] Determine charge posture: **Requested charge** (party's proposed charge, GRANTED/REFUSED lines on every instruction and question, footnoted source citations) or **Court's charge** (clean shell, no granted/refused lines). If the user has a preferred house-exemplar format, record its location in the profile and follow it; otherwise follow the pattern publisher's standard shell.
- [ ] Locate the matter's claim summary / charge prompt document. If none exists, build one first (Phase 1) — the claim map is the source of truth for what claims exist, and it gets updated FIRST when a claim changes, then the charge.
- [ ] Read the matter's posture notes for anything that moots claims (dispositive rulings, orders on motions to strike, nonsuits).
- [ ] **STOP:** list every open question that requires an attorney decision (claims possibly resolved by dispositive ruling, DJ claims that may not go to the jury, specific performance / equitable issues, late-added defenses subject to a motion to strike). Get the user's answer on each before drafting. Do not draft around an open question; a question with an unresolved predicate is not draftable.

## Phase 1 — Claim map intake

- [ ] Build or update the structured claim map: every claim, by every party, against every party. For each claim record: cause of action, elements source (pattern section or statute), asserting/defending parties, which affirmative defenses attach to it (defenses attach to CLAIMS, not globally), damages theory, fees basis.
- [ ] Check each claim against the library's `claim_matrix.json`. Every entry flagged `ATTORNEY JUDGMENT` marks a fact-specific call (defense selection, damages routing, variant choice like 101.2A vs. 101.2B) that belongs to the attorney, not the skill. Surface every applicable flag as a question.
- [ ] Claims with no pattern instruction (e.g., a statutory cause of action for which the library has no pattern): record the statute as the elements source and note "no pattern exists; instruction tracks statutory elements" for the footnote.

## Phase 2 — Pattern source lookup (MANDATORY, MECHANICAL)

**The gate: no question, instruction, or definition may be drafted unless its pattern section file was read in the current session via the lookup script. Pattern language from memory is prohibited — even for sections that "obviously" say something.**

- [ ] For every claim, run `scripts/pjc_lookup.py` against the section(s) mapped in the claim matrix. The script prints the verbatim section text from the parsed library and the section's cross-references from its index.
- [ ] Follow every cross-reference the COMMENT gives (accompanying instructions, definitions, damages sections, predicate requirements). Read those too.
- [ ] Extract per section: (a) pattern question text, (b) pattern instruction text, (c) COMMENT "when to use" guidance, (d) cross-references.
- [ ] **Index gate:** the lookup script only trusts the library's index. If a section is not in the index, the script fails loudly (exit 2). STOP and tell the user which volume is needed. Exit 3 means the library's manifest is unvalidated — run the builder gates (spot-check + attorney approval) before any drafting. A volume that is parsed on disk but NOT indexed or cross-reference-validated may be used only with the attorney's explicit go-ahead, flagged as unvalidated in the audit. Never substitute training-data recall for a missing volume. No workarounds.
- [ ] Record the lookup ledger: claim → sections read → files read. This becomes the pattern source audit input.

## Phase 3 — Routing map construction

- [ ] Build the charge map JSON per the schema in `JURY_CHARGE_CONDITIONAL_ROUTING.md`. Nodes get stable string keys — never question numbers. Question numbers are assigned by the renderer and can change on every regeneration without touching the map.
- [ ] Encode predication exactly: which answer on which node, any-vs-per-party scope, unanimity requirements (exemplary damages), ALTERNATIVE_TO chains (quantum meruit / unjust enrichment reached only if the contract claim fails), defense BLOCKS edges.
- [ ] Broad-form check (e.g., Tex. R. Civ. P. 277 where it governs): combine related theories into single disjunctive questions where feasible and permitted. Compressing the graph is a drafting decision — record it in the node's notes.
- [ ] Run `scripts/routing_map.py validate`. Fix every ERROR. Justify or fix every WARNING. Do not proceed to drafting with a failing map.

## Phase 4 — Drafting

- [ ] Draft the shell from the pattern library's admonitory series (read it — the shell is pattern text like everything else): admonitory instructions, burden definitions ("preponderance of the evidence"; "clear and convincing evidence" where exemplary predicates exist), circumstantial evidence instruction, presiding juror and verdict certificate.
- [ ] Populate each question from the verbatim pattern text read in Phase 2, substituting party names and case-specific facts only where the pattern brackets call for it. Any deviation from pattern language is a flagged, footnoted decision — not a silent paraphrase.
- [ ] Routing instructions: generated by `scripts/routing_map.py render` — never typed by hand. The renderer assigns question numbers and emits the "If you answered..." text from the graph.
- [ ] Footnote every question/instruction to its source: the pattern section id or statute cite with a "No pattern exists" note where applicable. Follow the house-exemplar format if one is configured.
- [ ] Requested-charge posture: GRANTED: ________ REFUSED: ________ line after every instruction and question.
- [ ] One space after every period, including inside generated strings. Smart quotes on "Answer 'Yes' or 'No.'" per house convention.

## Phase 5 — Verification (three audits + assembly gate)

- [ ] **Completeness audit:** every claim and defense in the claim map maps to a question number (or a documented decision to omit). Every ATTORNEY JUDGMENT flag resolved.
- [ ] **Pattern source audit:** every instruction and definition compared against the verbatim section text read in Phase 2. Deviations listed with justification.
- [ ] **Routing audit:** run `scripts/routing_map.py audit` to produce the HTML routing-audit deliverable, and `scripts/routing_map.py crosscheck` against the drafted charge text to verify every rendered "If you answered" instruction matches the graph (this catches a routing instruction referencing a stale question number).
- [ ] Renumbering protocol: if questions are added or removed, change the MAP, re-render, and re-run crosscheck. Never manually edit question numbers in prose.
- [ ] **Assembly:** build the .docx through the court-filing skill pipeline (caption, title, signature block, certificates per court-type module; body is the rendered charge). Run `validate_court_filing.py --court-type <type> --spacing <single|double>` and paste the TOTALS line. Not 0 failures = not finished.
- [ ] Deliverables: (1) the filing-ready charge .docx; (2) the routing audit HTML; (3) the validation TOTALS lines from both validators.

## Standing rules

- The claim map is updated first; the charge follows the map.
- Track-changes protocol for edits to a filed charge: OOXML `<w:ins>`/`<w:del>` with `w:author="Claude"` for substantive edits; direct edit only for mechanical fixes.
- Save matter charge work to the matter folder; skill and library work stays under the configured `jury_library_root`.

## Phase 6 — Remediation (findings → fixes → amended charge)

The audits are not the end state; an amended, verified document is. Findings from every audit flow into one ledger and out through an attorney gate.

- [ ] **Findings ledger.** `routing_map.py validate/crosscheck --findings <ledger.json>` appends routing findings automatically; language-audit and manual findings are added via `scripts/findings.py add/import`. Records carry category, severity, evidence (charge text vs. verbatim pattern text), and a disposition that starts `pending`. Fingerprint dedupe means re-running audits never duplicates or resurrects a dispositioned finding.
- [ ] **Fix proposals.** For each finding, draft the exact replacement language and record it with `findings.py propose`. Proposed text comes ONLY from the verbatim pattern section (already in the Phase 2 ledger) or disclosed authority — never composed from memory. Judgment calls (architecture, equitable defenses, bracketed-language choices) get options with tradeoffs, category ATTORNEY_DECISION.
- [ ] **Attorney gate (STOP).** Generate the Fix Proposal report (`findings.py report -o <html>`) and get the user's disposition on every finding: approved / rejected / modified / deferred (`findings.py disposition`). Nothing is applied while a finding is pending. `findings.py status` exits 2 while any critical finding is pending — that is the gate.
- [ ] **Implementation.** Apply approved fixes to the charge map and text. Routing changes go through the map (re-render; never hand-edit numbers). Edits to a previously filed charge use the track-changes protocol (OOXML `<w:ins>`/`<w:del>`, `w:author="Claude"`).
- [ ] **Re-verification.** Re-run ALL audits (validate, crosscheck against the revised text, language spot-check on every changed question). Then assemble through the court-filing pipeline and run its validator. Paste both TOTALS lines. Warnings must be justified in node notes (prefix `Justified:` — suppresses checks 5/10 once a human reason is recorded).
- [ ] **Closure.** The ledger IS the punch list: every finding ends with a disposition and history from detection → proposal → decision → verification.
