---
name: caselaw-analyst
description: "Produce formatted legal research deliverables from retrieved case law data. Use this skill whenever the user wants a research memo, cite-check report, case summary document, or any formatted Word deliverable based on cases that have been (or need to be) retrieved from CourtListener or other legal databases. Triggers on: 'write a research memo', 'draft the cite-check report', 'format these results as a memo', 'create the verification report', 'summarize these cases in a Word doc', or any request for a polished legal document based on case law research. Also triggers when the user asks to 'cite-check this' and expects a formatted report (this skill will invoke caselaw-retriever first if needed, then produce the formatted output). This skill reads structured JSON output from the companion skill 'caselaw-retriever' and produces Word documents formatted to the user's specifications. It never calls external APIs directly."
---

## HARD RULE: Never Read PDFs Directly in Cowork

**NEVER use the Read tool on a .pdf file. NEVER use bash/pdfplumber/pdftotext to extract PDF text inline.** Always look for a pre-converted `_COWORK.md` file in the matter's `_cowork_txt/` subfolder first. If no converted version exists, invoke the `pdf-to-cowork` skill to create one before proceeding. The Read tool renders PDFs as images, which burns the entire session's token budget on large legal documents and produces garbled output from PDFs with custom font encodings. This rule has no exceptions.

---

# Caselaw Analyst — Formatted Legal Research Deliverables

Reads structured case data from the caselaw-retriever skill and produces polished Word documents: research memos, cite-check reports, and case summaries. Never calls APIs directly — only works with cases the retriever has already found and verified.

## Architecture

```
CASELAW-RETRIEVER OUTPUT (JSON)
  │
  ▼
MODE SELECTION
  Research Memo  /  Cite-Check Report  /  Quick Summary
  │
  ▼
READ USER PROFILE
  ~/.legal-skills/config.json (environment-setup skill)
  │
  ▼
GENERATE WORD DOCUMENT (docx skill / docx-js)
  │
  ▼
VALIDATE → DELIVER
```

## Dependencies

- **caselaw-retriever skill** — Provides the structured JSON input. If the retriever hasn't run yet, invoke it first.
- **docx skill** — Read its SKILL.md before generating any Word document. Follow docx-js patterns, validation pipeline, and all formatting rules.
- **User profile** — Read `~/.legal-skills/config.json` (written by the environment-setup skill) before every document, and apply its identity and font settings. House defaults for these documents:
  - Font: the profile's `filing_font` (default Century Schoolbook)
  - Body text: 12pt, black, justified, first-line indent 0.5"
  - Headings: Real Word styles, black, Keep with Next + Keep Lines Together
  - H1: Small Caps for formal documents
  - Tables: Navy header (#1F4E79) with white text, thin gray borders between rows
  - Citations: Bluebook format — case name italicized through comma before reporter
  - Footer: Document title (small caps, bold) + page number, left aligned
  - Header: "Draft" in small caps, dark red, left aligned (for internal work product)
  - No end-of-document markers
  - No blank paragraphs for spacing — use paragraph style spacing

## Input

The retriever skill writes a JSON file with this structure:

**Research mode:**
```json
{
  "metadata": { "mode": "research", "query": "...", "jurisdiction": "...", ... },
  "cases": [
    {
      "case_name": "...", "full_citation": "...", "parallel_citations": [...],
      "court": "...", "date_filed": "...", "cluster_id": ..., "cite_count": ...,
      "courtlistener_url": "...", "provenance": "Verified|Partially Verified|Unable to Verify",
      "provenance_detail": "...", "snippet": "...", "full_text": "...|null",
      "citation_graph": { "total_citing": ..., "overruled_signal": false },
      "holding_summary": "...|null", "relevance_to_query": "..."
    }
  ]
}
```

**Cite-check mode:**
```json
{
  "metadata": { "mode": "cite-check", "document_title": "...", "total_citations_found": 7, "verified": 5, "flagged": 2, "unable_to_verify": 0, "verification_rate": "71%" },
  "citations": [
    {
      "citation_number": 1, "as_cited": "...", "proposition_cited_for": "...",
      "status": "Verified|Flagged|Unable to Verify", "rationale": "...",
      "discrepancies": [...], "case_data": { ... }
    }
  ],
  "rule_citations": [{ "rule": "TRCP 194.2", "status": "Confirmed", "note": "..." }]
}
```

If the JSON file doesn't exist yet, invoke the caselaw-retriever skill first to produce it.

---

## Output Mode 1: Research Memo

A polished Word document (.docx) that synthesizes research findings.

**When to use:** User asked a legal research question and wants a formatted memo.

### Document Structure

```
HEADER: "Draft" (small caps, dark red, 10pt, left aligned)

TITLE: "Legal Research Memo: [Topic]" (centered, 14pt, bold)
SUBTITLE: "Prepared for [attorney] | [firm]" (centered, 10pt — attorney from the profile's attorneys[0].name, firm from firm.name_lines joined; OMIT this line entirely if the profile is missing or empty)

TABLE: Case/matter info, date, jurisdiction, sources used

HEADING 1: Research Question
  Restate the research question as the skill understood it.

HEADING 1: Cases Found
  TABLE with columns:
    Case Name (italicized) | Citation | Court | Date | Cite Count | Provenance

  Color-code provenance: green background for Verified, yellow for Partially Verified, red for Unable to Verify.

HEADING 1: Case Analysis
  For each case (use Heading 2 for case name):
    - Citation (Bluebook format)
    - Court and date
    - Facts (brief, from opinion text the skill actually read)
    - Holding (from opinion text, clearly attributed)
    - Relevance to research question
    - Provenance tag with source URL
    - Citation graph data if available (cite count, any overruling signals)

HEADING 1: Synthesis
  Analytical section: how the cases relate to each other and to the research question.
  Identify trends, splits, or gaps in the authority.
  Note any areas where Westlaw verification is recommended.

HEADING 1: Limitations
  Standard disclosure about free database coverage gaps.

FOOTER: "Legal Research Memo" (small caps, bold) - Page X (10pt, left aligned)
```

### Writing Guidelines for Analysis

The analysis sections are where the skill adds legal judgment. Important guardrails:

- **Clearly distinguish retrieval from analysis.** The case citation, court, date, and source URL are verifiable facts. The holding summary and relevance assessment are the skill's interpretation, subject to attorney review.
- **Ground every holding statement in text the retriever actually read.** If the retriever only had a snippet, say so: "Based on the available excerpt, the court appears to hold that..."
- **Never overstate holdings.** If a case mentions a topic in dicta, say it's dicta. If the holding is narrower than the research question, say so.
- **Flag gaps honestly.** If the search likely missed relevant authority (e.g., unpublished trial court orders), note it.
- **Use Bluebook citation format** per the global formatting preferences: case name italicized through the comma before the reporter volume number, standard abbreviations per Bluebook tables.

---

> **Note (April 2026):** For cite-checking briefs and motions, the Isaacus cite-check pipeline (`cite-check` skill) is now the preferred path. It uses AI-powered enrichment for citation detection and proposition extraction, with automatic CourtListener opinion resolution and Isaacus verify() for support classification. This skill (caselaw-analyst) remains the primary path for research memos, case summaries, and any formatted deliverable from caselaw-retriever output.

## Output Mode 2: Cite-Check Report

A structured Word document that verifies each citation in a filing.

**When to use:** User uploaded a brief/motion for citation verification.

### Document Structure

```
TITLE: "CITATION VERIFICATION REPORT" (centered, 14pt, bold)
SUBTITLE: "Prepared for [attorney] | [firm]" (centered, 10pt — attorney from the profile's attorneys[0].name, firm from firm.name_lines joined; OMIT this line entirely if the profile is missing or empty)

TABLE: Case info
  Case | [case name and number]
  Document | [document title as it appears on the face of the document]
  Date of Review | [current date]
  Sources | CourtListener REST API (v4), txcourts.gov, Google Scholar, FindLaw, Justia, secondary sources
  Method | API-assisted (CourtListener Search API + citation graph; unauthenticated endpoints)

HEADING 1: Summary Dashboard
  TABLE with columns: Status | Count
    Total case citations | [count]
    Verified | [count] (teal background #E6F4ED)
    Partially Verified | [count] (blue background #E3F2FD)
    Flagged | [count] (amber background #FFF3E0)
    Does Not Support | [count] (red background #FDECEC)
    Unable to Verify | [count] (purple background #F0EBF5)
    Rule citations checked | [count] — All Confirmed (or note issues)
    Verification rate | [percentage]

HEADING 1: API-Enhanced Data Summary
  TABLE showing what the API added beyond what manual review could:
    CourtListener cluster IDs, cite counts, parallel citations, citation graph data,
    reporter typo detection, entity name corrections.
  Only include rows where the API actually added something.

HEADING 1: Detailed Citation Analysis
  For EACH citation (numbered), a two-part display:

  SUMMARY ROW (table):
    # | Citation | Status (color-coded) | CourtListener ID

  DETAIL ROW (below the summary):
    Proposition cited for: [what the document used this case for]
    Analysis: [detailed assessment — was the case found? Does the holding support the proposition?]
    CourtListener data: [cluster_id, cite count, parallel cites, case name as listed]
    Citation graph: [if pulled — cite count, good law status]
    CourtListener URL: [direct link]
    Discrepancies: [any differences between the document's citation and what the API returned]

HEADING 1: Rule Citations
  Brief table confirming rule citations are correct (TRCP, FRCP, etc.)

HEADING 1: Limitations
  Standard disclosure:
  - Sources used (free databases only)
  - What each status means (include the Status Key table)
  - Pinpoint cites not verified
  - Recommendation to check Flagged, Does Not Support, and Unable to Verify on Westlaw
```

### Status Color Coding (Default Palette)

| Status | Background | Text Color |
|--------|-----------|------------|
| Verified | #E6F4ED | Teal #0FA685 |
| Partially Verified | #E3F2FD | Blue #2E8BC0 |
| Flagged | #FFF3E0 | Amber #E8870E |
| Does Not Support | #FDECEC | Red #D44040 |
| Unable to Verify | #F0EBF5 | Purple #7E57C2 |

### Rationale Requirements

Every citation's analysis must explain:
1. **Was the case found?** In which database? What cluster_id?
2. **Do the citation details match?** Reporter, court, date, case name.
3. **Does the holding support the proposition?** Based on what text (full opinion, snippet, or secondary source)?
4. **Any discrepancies?** Reporter typos, entity name mismatches, spelling errors, date issues.

A Flagged, Does Not Support, or Unable to Verify status without rationale is never acceptable.

### Status Key (include at end of every report)

| Status | Meaning |
|--------|---------|
| Verified | Case found, citation confirmed, holding supports the proposition as cited. |
| Partially Verified | Case found and citation confirmed, but full opinion text was unavailable to check whether the holding supports the proposition. |
| Flagged | Case found, but an issue was detected — reporter discrepancy, nuanced or distinguishable holding, or dicta cited as holding. |
| Does Not Support | Case found, but the holding contradicts or does not support the proposition as cited. Review required. |
| Unable to Verify | Case not found in free databases. This is a coverage gap, not necessarily an error. Verify on Westlaw. |

---

## Output Mode 3: Quick Summary

A brief conversational summary when the user doesn't need a full document.

**When to use:** User did a quick lookup and wants results in the conversation, not a file.

Format: Present results directly in conversation text. Include case name, citation, court, date, cite count, CourtListener URL, and a brief note on the holding. No Word document generated unless the user asks for one.

---

## Document Generation

Use the **docx skill** for all Word document creation. Follow these steps:

1. **Read the docx SKILL.md** for docx-js patterns and the full validation pipeline.
2. **Read the user profile** (`~/.legal-skills/config.json`) for font, identity, and folder settings; apply the document design rules in this skill.
3. **Set page size** to US Letter (12240 × 15840 DXA), not A4.
4. **Set margins** to 1.0" (1440 DXA) all sides for internal work product.
5. **Use real Heading styles** (Heading1, Heading2) with `outlineLevel` for TOC support.
6. **Use the docx skill's table patterns** with dual widths (columnWidths + cell width), navy headers, gray borders.
7. **Validate** with `validate.py` after generation.
8. **Detect the matter, then save with the matter's files.** This skill is the companion to `cite-check`, and its output routes the same way. Determine the matter from (a) the source file path — if inputs came from a folder under the profile's `matter_root`, use that matter; then (b) the case captions, cause numbers, or party names in the source citations; if still ambiguous, ask with AskUserQuestion (one question). Then save using the file naming convention:

   ```
   IF matter identified:
       [matter_root]/[Matter Name]/Internal Work Product/YYYY.MM.DD Document Name.docx
   ELSE:
       the current working folder: YYYY.MM.DD Document Name.docx
   ```

   Create `Internal Work Product/` (or your own equivalent work-product subfolder) if it doesn't exist yet — don't save to the matter root, and don't provision the rest of the matter's subfolder set (routing rule, not provisioning rule).

### File Naming Convention

All files follow this default pattern:
```
YYYY.MM.DD DOCUMENT NAME.docx
```
Examples:
- `2026.03.16 Legal Research Memo - Automatic Exclusion.docx`
- `2026.03.16 Cite Check Report - ACME Mot to Exclude.docx`

---

## Provenance Display

The analyst skill displays provenance information the retriever collected, but does not modify it. The provenance system exists to provide evidence for the attorney to evaluate — not to make confidence claims.

| Tag | Display | Meaning |
|-----|---------|---------|
| Verified | Teal badge (#0FA685 on #E6F4ED) | Case found, citation confirmed, holding supports proposition |
| Partially Verified | Blue badge (#2E8BC0 on #E3F2FD) | Case found, citation confirmed, full text not available for holding check |
| Flagged | Amber badge (#E8870E on #FFF3E0) | Case found but issue detected (nuanced holding, discrepancy, dicta) |
| Does Not Support | Red badge (#D44040 on #FDECEC) | Case found, but holding contradicts or does not support the cited proposition |
| Unable to Verify | Purple badge (#7E57C2 on #F0EBF5) | Not found in free databases — coverage gap, not necessarily wrong |

**NEVER say "these cases are not hallucinated."** Present the evidence and let the attorney decide.

---

## Integration with Retriever

If the user asks for a formatted deliverable but the retriever hasn't run yet (no JSON intermediate file exists), the analyst skill should:

1. Determine what mode is needed (research or cite-check)
2. Invoke the caselaw-retriever skill to produce the JSON
3. Read the JSON output
4. Generate the formatted document

This means the user can say "cite-check this brief" to either skill and get the full pipeline. The analyst just ensures the retriever runs first.

---

## Limitations Disclosure (Standard Text)

Include this at the end of every report, adapted as needed:

> This report was prepared using the CourtListener REST API (unauthenticated search and citation-graph endpoints) supplemented by web searches of txcourts.gov, Google Scholar, FindLaw, Justia, and secondary sources. Full opinion text retrieval was limited to PDF sources from storage.courtlistener.com and txcourts.gov.
>
> A "Partially Verified" result means the case was found and the citation confirmed, but the full opinion text was unavailable to check whether the holding supports the proposition. A "Flagged" result means an issue was detected — a reporter discrepancy, nuanced or distinguishable holding, or dicta cited as holding. A "Does Not Support" result means the case was found but the holding contradicts or does not support the proposition as cited. An "Unable to Verify" result means the case could not be located in any free database — this is a source coverage issue, not a judgment about the citation's validity. Any citation classified as Flagged, Does Not Support, or Unable to Verify should be checked on Westlaw before relying on this report.
>
> This report does not verify pinpoint citations to specific pages. Where CourtListener URLs are provided, the reviewing attorney should verify pincites independently.
