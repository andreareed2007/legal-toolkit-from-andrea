---
name: title11-bankruptcy
description: >-
  Look up and trace the U.S. Bankruptcy Code (Title 11) from a bundled, offline copy of the
  official statute — pull a section's operative text, resolve a defined term to the section that
  defines it, and follow cross-references across sections. USE THIS SKILL whenever the user asks
  about a Title 11 / Bankruptcy Code section by number ("what does § 547 say", "pull section 362",
  "the automatic stay statute"), asks what a bankruptcy term means as defined in the Code ("how is
  'claim' defined", "definition of insider under the Code"), or wants to chase references between
  sections ("what does 547 point to", "trace the cross-references from 1129"). Also triggers on
  casual phrasing like "the preference statute", "avoidance powers section", "what section covers
  exemptions". This is the STATUTE counterpart to caselaw-retriever (which handles case law) — use
  it for the Bankruptcy Code text itself. NOT for other titles of the U.S. Code, state statutes,
  or case research.
---

> **Version:** v2026.07.09-1 · **Last updated:** 2026-07-09, 11:45 AM CT
> Changelog: v2026.07.09-1 — initial build. Section lookup, defined-term resolution, cross-reference tracing over the bundled Title 11 corpus (280 sections, 95 defined terms), with a hosted-canvas link.

# Title 11 (U.S. Bankruptcy Code) Research

This skill answers questions about the *text* of the Bankruptcy Code from a bundled, offline
copy — no network, no account, no case-law database. The data was harvested from the official
USLM XML of Title 11 published by the Office of the Law Revision Counsel and flattened into clean,
quotable statutory prose with cross-references and defined terms tagged. Everything runs through
three small Python scripts so the output is deterministic and safe to quote.

## When to reach for which script

All scripts live in `scripts/` and read the bundled corpus in `assets/data/`. Run them with
`python3` from the `scripts/` directory (they resolve the data path relative to themselves).

**Section lookup** — the user wants what a section says.
```
python3 scripts/lookup.py 547            # full operative text + cross-refs + defined terms
python3 scripts/lookup.py 362 --refs-only  # just the cross-references and defined terms
```

**Defined-term resolution** — the user wants how the Code defines a term, and where.
```
python3 scripts/define.py claim
python3 scripts/define.py "domestic support obligation"
```
This returns the definition text plus the exact section/subsection that defines it (e.g. § 101(5))
and the scope. Terms are matched case-insensitively; a near-miss prints similar defined terms.

**Cross-reference tracing** — the user wants to follow the trail between sections.
```
python3 scripts/trace.py 547            # default: 2 hops
python3 scripts/trace.py 1129 --depth 3
```
This walks each section's outbound Title 11 cross-references, expands them recursively, and shows
the heading of every section reached (cycles are pruned and marked). It is the text version of the
canvas's "chase the reference across sections" workflow — useful for scoping a research question or
building the skeleton of a memo before you read anything in full.

## Using the output

The section text is verbatim statutory language with subsection numbering preserved, so it can be
quoted directly into a memo or brief. Amendment history and editorial notes are kept out of the
operative `text` (they live in a separate field in the corpus), so a quote won't accidentally pick
up "Pub. L." history. When you hand a result to a formatted deliverable, pair this with
`caselaw-analyst` for the Word memo, exactly as `caselaw-retriever` does for case law.

Cite Bankruptcy Code sections as `11 U.S.C. § [number]` (the scripts already print this form).

## The visual canvas

Every script prints a link to the hosted spatial canvas (https://rlfordon.github.io/bankruptcy-canvas/),
which is the same underlying data arranged as draggable, cross-linked cards. The canvas has no
deep-link URL, so the link tells the user to search the section number in the app's top bar. Offer
it when a research question is sprawling enough that a visual layout would help the user see the
structure — competing definitions, a web of cross-references, a plan-confirmation chain — rather
than for a single-section lookup.

## Scope and freshness

The corpus is a snapshot of Title 11 (280 sections, 95 defined terms). It does not include other
titles of the U.S. Code, state law, local bankruptcy rules, or the Federal Rules of Bankruptcy
Procedure. If a section number isn't found, say so rather than guessing — the script lists nearby
sections to help. Title 11 is amended over time; if a section looks stale, regenerate the corpus from the
official source (Office of the Law Revision Counsel, uscode.house.gov, Title 11 XML/XHTML)
and rebuild `assets/data/title11_sections.json` with the same key structure.
