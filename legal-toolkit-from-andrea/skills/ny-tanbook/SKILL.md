---
name: ny-tanbook
description: >
  Format, convert, and validate New York citations to the NY Law Reports Style
  Manual (the "Tanbook"), 2022 ed. USE THIS SKILL whenever the user wants to
  Tanbook-format NY cites, convert Bluebook-style NY citations to NY official
  style, cite-check a brief for Tanbook conformity, or fill in missing NY
  parallel citations. Triggers on: 'Tanbook', 'NY official citation style',
  'New York Law Reports Style Manual', 'fix my New York cites', 'convert these
  cites to NY style', 'is this cite in Tanbook format', 'Bluebook to Tanbook',
  'NY Slip Op format', 'check the New York citations in this brief', 'cite to
  the official reports', or any New York state filing where citation style must
  follow the Official Reports (CPLR 5529 [e]; 22 NYCRR 500.1 [g]). NOT for
  general topic-based case research (use caselaw-retriever) or non-NY citation
  styling.
---

# NY Tanbook Citation Formatter

> **First-run check (untested v1).** This skill has not yet been validated on a
> real brief. The FIRST time it is invoked, tell the user up front: *"Heads-up —
> this is the untested first version of the Tanbook skill. Want to spot-check
> the output against a citation you know is correct before relying on it?"* Then
> proceed with the task. Once the user confirms it has worked well on a real
> document, this note can be removed.


Formats New York citations to the **New York Law Reports Style Manual** (2022
ed., the "Tanbook"). The authoritative rule distillation is in
`reference/TANBOOK_RULES.md` -- READ IT before doing any non-trivial formatting
or before resolving a judgment call. The deterministic engine is
`scripts/tanbook.py`.

## The three modes

### 1. Convert (Bluebook -> Tanbook)
Mechanical transform. Run the engine:

```
python3 scripts/tanbook.py convert "<citation text>"
```

It de-periodizes NY/federal/regional reporters (`N.Y.3d`->`NY3d`,
`A.D.3d`->`AD3d`, `Misc.3d`->`Misc 3d`), fixes `v.`->`v`, converts the
date/court parenthetical from `(...)` to `[...]`, and de-truncates page ranges
(`316-17`->`316-317`).

The engine does NOT drop an improper NYS2d/NE2d parallel from an officially
reported NY case -- that is flagged in validate mode because removing data is a
judgment call. After running convert, eyeball the result against
`TANBOOK_RULES.md` for the items the engine leaves to judgment (department from
county, pertinent appellate history, Appendix 1 case-name abbreviations).

### 2. Validate (cite-check for Tanbook conformity)
Reports nonconformities without altering text (JSON list):

```
python3 scripts/tanbook.py validate "<citation text>"
```

Flags: periods in NY reporters, `v.`, date-in-parens, truncated page ranges,
comma after a signal, improper unofficial parallels on officially-reported NY
cases, and suspected `supra` short-forms. For a whole brief, extract each NY
citation, run validate on each, and present a table of {citation, issue,
suggested fix}. For a `.docx` input, use the docx skill to pull text first.

### 3. Enrich (assemble missing parallel cites)
When the user gives a case name or a single cite and wants the full citation,
use the **CourtListener MCP** (`mcp__...__search` / `read_document` /
`get_endpoint_schema`+`call_endpoint`) to resolve the case and pull reporter
citations, then format per `TANBOOK_RULES.md`.

IMPORTANT NY-specific rule when enriching (section 7 and rule 6 of the
reference): NY does NOT use parallel unofficial cites for officially reported NY
cases -- so for a NY Court of Appeals / App Div / Misc case, return ONLY the
official cite (NY3d / AD3d / Misc 3d) with the bracketed court+year. Parallel
cites are for OUT-OF-STATE cases (official state report + National Reporter,
e.g. `333 Conn 1, 214 A3d 361 [2019]`) and for US Supreme Court when the US
Reports cite is unavailable. Do not invent a reporter, volume, page, or history
abbreviation -- if CourtListener does not return it, say so and flag it.

## Key rules the engine cannot fully automate (always verify in the reference)
- **Bracketed parenthetical by court level** (rule 3): Court of Appeals = year
  only `[2019]`; App Div = `[dept year]`; trial courts = `[Court, County year]`.
  County->department mapping is a judgment call; confirm the department.
- **Slip opinions** (rule 5): `[U]`/`[A]` markers, em-dash blank reporters, `*`
  star pages. The engine does not synthesize these -- format by hand from the
  reference.
- **Statutes** (rule 8): subdivisions in brackets; section-symbol-less statutes
  (CPL, CPLR, ECL, RPTL, EPTL, etc.); comma vs. semicolon hierarchy.
- **Case-name abbreviations** keep their periods (`Univ.`, `Assn.`, `Co.`) --
  do NOT strip them. Only reporter abbreviations lose periods.

## Workflow notes
- Default to the within-parentheses display style (the manual's default). For
  citational-footnote style, convert internal brackets back to parens (rule 9).
- Follow the user's file-naming and save conventions for any deliverable, if
  they have stated them.
- Run `python3 scripts/tanbook.py selftest` to confirm the engine is healthy.
