---
name: caselaw-analyst
description: "Produce formatted legal research deliverables from retrieved case law data. Use this skill whenever the user wants a research memo, case summary document, or any formatted Word deliverable based on cases that have been (or need to be) retrieved from CourtListener or other legal databases. Triggers on: 'write a research memo', 'format these results as a memo', 'summarize these cases in a Word doc', or any request for a polished legal document based on case law research. This skill reads structured JSON output from the companion skill 'caselaw-retriever' and produces Word documents formatted to the user's specifications. It never calls external APIs directly. NOT for cite-checking a brief or motion — route 'cite-check this' requests to the cite-check skill (eyecite + CourtListener + Isaacus), which produces its own report."
---

> **Version:** v2026.07.22-1 (shared edition) · **Last updated:** 2026.08.05

## HARD RULE: Never Read PDFs Directly in Cowork

**NEVER use the Read tool on a .pdf file. NEVER use bash/pdfplumber/pdftotext to extract PDF text inline.** Always look for a pre-converted `_COWORK.md` file in the matter's `_cowork_txt/` subfolder first. If no converted version exists, invoke the `pdf-to-cowork-txt` skill to create one before proceeding. The Read tool renders PDFs as images, which burns the entire session's token budget on large legal documents and produces garbled output from PDFs with custom font encodings. This rule has no exceptions.

---

# Caselaw Analyst — Formatted Legal Research Deliverables

Reads structured case data from the caselaw-retriever skill and produces polished Word documents: research memos and case summaries. Never calls APIs directly — only works with cases the retriever has already found. **Fail-closed:** interpretive content renders only for cases the retriever independently read this session (see The Render Gate).

## Architecture

```
CASELAW-RETRIEVER OUTPUT (JSON)
  │
  ▼
MODE SELECTION
  Research Memo  /  Quick Summary
  │
  ▼
RENDER GATE (fail-closed)
  independently_read + full_text + confirmed pincites → interpretive content
  anything else → existence-only row or drop
  │
  ▼
READ USER PROFILE
  ~/.legal-skills/config.json
  │
  ▼
GENERATE WORD DOCUMENT (docx skill / docx-js)
  │
  ▼
VALIDATE → DELIVER (with truthful verification footer)
```

## Dependencies

- **caselaw-retriever skill** — Provides the structured JSON input. If the retriever hasn't run yet, invoke it first.
- **docx skill** — Read its SKILL.md before generating any Word document. Follow docx-js patterns, validation pipeline, and all formatting rules.
- **User profile** — Read `~/.legal-skills/config.json` (written by the environment-setup skill) before every document. Apply all preferences. Key ones for these documents:
  - Font: Century Schoolbook
  - Body text: 12pt, black, justified, first-line indent 0.5"
  - Headings: Real Word styles, black, Keep with Next + Keep Lines Together
  - H1: Small Caps for formal documents
  - Tables: Navy header (#1F4E79) with white text, thin gray borders between rows
  - Citations: Bluebook format — case name italicized through comma before reporter
  - Footer: Document title (small caps, bold) + page number, left aligned
  - Header: "Draft" in small caps, dark red, left aligned (for internal work product)
  - One space after periods. Always.
  - No end-of-document markers
  - No blank paragraphs for spacing — use paragraph style spacing

## Input

The retriever skill writes a JSON file with this structure (research mode):

```json
{
  "metadata": { "mode": "research", "query": "...", "jurisdiction": "...", ... },
  "cases": [
    {
      "case_name": "...", "full_citation": "...", "parallel_citations": [...],
      "court": "...", "date_filed": "...", "cluster_id": ..., "cite_count": ...,
      "courtlistener_url": "...",
      "provenance": "Read-Verified|Citation-Confirmed|Unable to Verify",
      "provenance_detail": "...",
      "independently_read": true,
      "read_full_text_source": "opinions/9679765 html_with_citations",
      "snippet": "...", "full_text": "...|null",
      "quotations": [ { "text": "...", "pincite": "...", "pincite_confirmed_against_read_text": true } ],
      "citation_graph": { "total_citing": ..., "overruled_signal": false },
      "holding_summary": "...|null", "relevance_to_query": "...|null"
    }
  ]
}
```

If the JSON file doesn't exist yet, invoke the caselaw-retriever skill first to produce it.

---

## THE RENDER GATE (fail-closed — run before any document build)

Before building any deliverable, run this assertion over every case in the JSON and report the result:

**A case may be presented with a holding summary, quotation, or relevance paragraph ONLY if ALL of the following hold:**

1. `independently_read == true`;
2. `full_text` is non-null (this case's own opinion text);
3. every quotation to be rendered has `pincite_confirmed_against_read_text == true`.

**If any case fails the assertion,** the analyst MUST either (a) downgrade it to an existence-only row carrying the red flag "not independently read — confirm before relying," or (b) drop it from the deliverable. It NEVER renders the failing case's holding, quotes, or relevance analysis — even if those strings are present in the JSON. The delivered output and the chat summary both state which cases were downgraded or dropped and why.

**Do not trust an unsubstantiated tag.** A `Read-Verified` provenance value the analyst cannot substantiate from the JSON itself (non-null `full_text`, confirmed pincites) is treated as Citation-Confirmed (existence only). The default is inverted deliberately: render interpretive content only for substantiated reads — never "render everything the JSON contains."

This gate exists because a 2026.07.21 research memo shipped 6 of 11 cases whose opinions were never independently read, including one reversed holding. See the caselaw-retriever skill's "No Second-Hand Cases" hard rule for the sourcing side of the same gate.

## Provenance Taxonomy

| Tag | Display | Meaning | Interpretive content? |
|-----|---------|---------|----------------------|
| Read-Verified | Teal badge (#0FA685 on #E6F4ED) | This case's own full opinion retrieved and read this session; quotes and pincites confirmed against it. | **Yes — only these.** |
| Citation-Confirmed (existence only) | Amber badge (#E8870E on #FFF3E0) | Case exists — citation, court, date confirmed — but its own full text was NOT read this session. | No. Existence row only, flagged "not independently read — confirm before relying." |
| Unable to Verify | Purple badge (#7E57C2 on #F0EBF5) | Not found in free databases. Coverage gap, not necessarily wrong. | No. Check Westlaw. |

"Partially Verified" is retired. There is no tier that both lacks the full text and carries a holding — that combination is the failure mode this taxonomy exists to prevent.

**NEVER say "these cases are not hallucinated"** and NEVER use blanket "all citations verified" language unless every single case is Read-Verified. Present the evidence and let the attorney decide.

---

## Output Mode 1: Research Memo

A polished Word document (.docx) that synthesizes research findings. **Bottom line up front:** the answer leads; the support follows.

**When to use:** User asked a legal research question and wants a formatted memo.

### Document Structure

```
HEADER: "Draft" (small caps, dark red, 10pt, left aligned)

TITLE: "Legal Research Memo: [Topic]" (centered, 14pt, bold)
SUBTITLE: "Prepared for [attorney] | [firm]" (centered, 10pt — attorney from the profile's attorneys[0].name, firm from firm.name_lines joined; OMIT this line entirely if the profile is missing or empty)

TABLE: Case/matter info, date, jurisdiction, sources used

HEADING 1: Short Answer
  2-5 sentences. The answer to the research question, stated first, with the
  leading authorities cited inline. A reader who stops here has the conclusion.
  End with the read count: "N of M cases cited below were independently read
  in full this session." If N < M, name the exceptions here, not just in the footer.

HEADING 1: Research Question
  Restate the research question as the skill understood it. One short paragraph.

HEADING 1: Cases at a Glance
  TABLE with columns:
    Case Name (italicized) | Citation | Court | Date | Cite Count | Provenance
  Color-code provenance per the taxonomy table (teal / amber / purple).

HEADING 1: Case Analysis
  Read-Verified cases only. For each case (Heading 2 = case name), short labeled
  elements — bold run-in labels, one paragraph or less each, never undifferentiated blocks:
    Citation:  Bluebook format, parallel cites.
    Court/Date:  one line.
    Facts:  2-4 sentences, from the opinion text actually read.
    Holding:  1-3 sentences, from the opinion text, clearly attributed.
    Key language:  confirmed quotations with pincites (only from quotations[] with
      pincite_confirmed_against_read_text: true).
    Relevance:  how it bears on the question.
    Good law:  cite count + any overruling/caution signals from the citation graph.
    Source:  provenance badge + read_full_text_source + CourtListener URL.

HEADING 1: Cases Not Independently Read  (include ONLY if any exist)
  Existence-only table: Case Name | Citation | Court | Date | Why not read.
  Each row flagged: "Not independently read — confirm against the opinion / Westlaw
  before relying." NO holdings, NO quotes, NO relevance text in this section.

HEADING 1: Synthesis
  How the Read-Verified cases relate to each other and to the question.
  Trends, splits, gaps. Recommended Westlaw follow-ups.

HEADING 1: Verification and Limitations
  The truthful verification footer (see below) + standard coverage disclosure.

FOOTER: "Legal Research Memo" (small caps, bold) - Page X (10pt, left aligned)
```

### Writing Guidelines for Analysis

- **Clearly distinguish retrieval from analysis.** Citation, court, date, and source URL are verifiable facts. Holding summaries and relevance assessments are the skill's interpretation, subject to attorney review.
- **Ground every holding statement in this case's own text, read this session.** Never in the user's brief, another opinion's characterization, or a snippet. If it wasn't read, it goes in the Cases Not Independently Read section with no holding at all.
- **Never overstate holdings.** Dicta is labeled dicta. A holding narrower than the question is said to be narrower.
- **Flag gaps honestly.** If the search likely missed relevant authority, say so.
- **Use Bluebook citation format** per the global formatting preferences.
- **Short labeled blocks beat prose walls.** Every case entry uses the bold run-in labels above. No multi-topic paragraphs.

---

## Output Mode 2: Cite-Check Report — RETIRED

This mode is retired. **Route every cite-check request ("cite-check this brief," "verify these citations") to the `cite-check` skill** — the eyecite + CourtListener + Isaacus pipeline, which produces its own report with a locked verdict taxonomy, a mandatory verification loop, and a render hard-block. Do not build cite-check reports from caselaw-retriever JSON. If the Isaacus pipeline is unavailable, the caselaw-retriever skill's legacy fallback mode applies and says so explicitly in its output; format that fallback output as a research-memo-style document using the structures in this skill, never as a lookalike of the cite-check skill's report.

---

## Output Mode 3: Quick Summary

A brief conversational summary when the user doesn't need a full document.

**When to use:** User did a quick lookup and wants results in the conversation, not a file.

Format — BLUF, same discipline as the memo:

1. **Bottom line** — the answer, 1-3 sentences.
2. **Read count** — "N of M independently read this session"; name existence-only cases.
3. **Per case** — ***Case Name*, cite (court year)** — **Holding:** one sentence (Read-Verified only). **Status:** badge text.
4. **Caveats** — gaps, signals, Westlaw recommendations.

The render gate applies in conversation too: no holding language for a case that wasn't independently read. No Word document unless the user asks.

---

## Document Generation

Use the **docx skill** for all Word document creation. Follow these steps:

1. **Run the Render Gate first** and note the downgrade/drop report for the chat summary.
2. **Read the docx SKILL.md** for docx-js patterns and the full validation pipeline.
3. **Read the global formatting preferences** for Century Schoolbook, black text, spacing rules, table design, etc.
4. **Set page size** to US Letter (12240 × 15840 DXA), not A4.
5. **Set margins** to 1.0" (1440 DXA) all sides for internal work product.
6. **Use real Heading styles** (Heading1, Heading2) with `outlineLevel` for TOC support.
7. **Use the docx skill's table patterns** with dual widths (columnWidths + cell width), navy headers, gray borders.
8. **Validate** with `validate.py` after generation.
9. **Detect the matter, then save to that matter's `Internal Work Product/` subfolder.** Determine the matter from (a) the source file path — if inputs came from `Documents/Matters/[Matter Name]/`, use that matter; then (b) the case captions, cause numbers, or party names in the source citations, matched against the Matter Tag Registry in CLAUDE.md; if still ambiguous, ask with AskUserQuestion (one question). Then save using the file naming convention:

   ```
   IF matter identified:
       [matter_root]/[Matter Folder]/Internal Work Product/YYYY.MM.DD Document Name.docx
   ELSE:
       the session outputs folder/YYYY.MM.DD Document Name.docx
   ```

   Create `Internal Work Product/` if it doesn't exist yet — don't save to the matter root, and don't provision the rest of the matter's subfolder set (routing rule, not provisioning rule).

### File Naming Convention

All files follow the pattern from CLAUDE.md:
```
YYYY.MM.DD DOCUMENT NAME.docx
```
Example: `2026.07.22 Legal Research Memo - Agreed Judgment Collateral Attack.docx`

---

## Truthful Verification Footer (required in every deliverable)

The footer reports the ACTUAL counts from the data — never boilerplate confidence:

> **Verification: N of M cases cited in this memo were independently read in full opinion text this session.** [If N == M: "Every holding and quotation was confirmed against the cited case's own opinion."] [If N < M, name each exception: "The following were NOT independently read and appear as existence-only entries: *Case A*; *Case B*. Confirm each against the opinion or on Westlaw before relying."]

Rules:

- Blanket "all citations verified" language is BANNED unless every case is Read-Verified.
- Existence-only cases are named individually, every time, in both the footer and the Short Answer.
- Chat delivery repeats the N-of-M line — it is never buried in the document alone.

## Limitations Disclosure (standard text, adapt as needed)

> This memo was prepared using the CourtListener REST API (authenticated via cl_api.py — search, opinions, clusters, and citation-graph endpoints) supplemented where necessary by storage.courtlistener.com, txcourts.gov, and other free full-text sources. Cases marked Read-Verified had their own full opinion text retrieved and read during preparation; quoted language and pincites were confirmed against that text. Cases marked Citation-Confirmed were confirmed to exist (citation, court, date) but their opinions were not independently read — no holding or characterization is offered for them, and they should be confirmed on Westlaw before use. "Unable to Verify" means the case was not located in free databases — a coverage gap, not a judgment about validity. Free-database coverage is narrower than Westlaw/Lexis; treatment signals are evidence, not a Shepard's/KeyCite substitute.

---

## Integration with Retriever

If the user asks for a formatted deliverable but the retriever hasn't run yet (no JSON intermediate file exists), the analyst skill should:

1. Invoke the caselaw-retriever skill to produce the JSON (research mode)
2. Read the JSON output
3. Run the Render Gate
4. Generate the formatted document

The analyst just ensures the retriever runs first. Cite-check requests route to the cite-check skill instead — see Output Mode 2.
