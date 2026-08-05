---
name: caselaw-retriever
description: "Retrieve and verify case law from CourtListener and free legal databases. Use whenever the user asks to find cases, search caselaw by topic, pull opinions, check if a case is good law, or retrieve court opinions. Triggers: 'search CourtListener', 'find cases on [topic]', 'pull cases about [issue]', 'is this case good law', 'what cases support [proposition]', 'look up [case name]', and casual requests like 'can you find the Alvarado case' or 'what does the law say about automatic exclusion'. Handles all API calls and source lookups; outputs structured data the companion skill 'caselaw-analyst' turns into formatted Word deliverables. Can be used standalone for quick lookups. NOT for cite-checking a brief or motion — use the cite-check skill; this skill's legacy cite-check mode is only a manual fallback when the Isaacus pipeline is unavailable."
---

> **Version:** v2026.07.22-1 (shared edition) · **Last updated:** 2026.08.05

## HARD RULE: No Second-Hand Cases — Independent Retrieval Required

**Every case that will appear in any deliverable as authority — carrying a holding summary, a quotation, or a relevance/analysis paragraph — MUST have its own opinion independently retrieved this session (Priority-0 opinions endpoint `html_with_citations`, or another full-text source) and actually read.** A case that was not independently read may appear ONLY as an existence-only row (Citation-Confirmed) with no interpretive content.

**Prohibited sourcing — NEVER the basis for a holding or quote:**

- The user's brief, motion, outline, or prior work product.
- A quotation or characterization embedded in a DIFFERENT opinion that was read. Reading *Boufaissal* does not verify *Baw* or *Gillum* — pull each case's own opinion.
- A headnote, syllabus, snippet, or search-result excerpt.
- Prior-session memory or the Case Index.

If a case is known only through a quote in another document, independently pull it and re-confirm every borrowed sentence against its own text before use. Why this rule exists: a 2026.07.21 research memo shipped 6 of 11 cases second-hand. Reading them all surfaced a reversed holding (a case cited for a consent-waiver rule that holds the opposite on its facts) and a missed on-point high-court holding. Second-hand sourcing produces wrong statements of law, not just bad pincites.

## HARD RULE: Never Read PDFs Directly in Cowork

**NEVER use the Read tool on a .pdf file. NEVER use bash/pdfplumber/pdftotext to extract PDF text inline.** Always look for a pre-converted `_COWORK.md` file in the matter's `_cowork_txt/` subfolder first. If no converted version exists, invoke the `pdf-to-cowork-txt` skill to create one before proceeding. The Read tool renders PDFs as images, which burns the entire session's token budget on large legal documents and produces garbled output from PDFs with custom font encodings. This rule has no exceptions.

---

# Caselaw Retriever — CourtListener API + Free Source Pipeline

Searches CourtListener and free legal databases to find, retrieve, and verify court opinions. Outputs structured case data for the caselaw-analyst skill or directly to the user for quick lookups.

## Architecture

```
USER REQUEST
  │
  ▼
INTAKE & MODE DETECTION ─── Research / Cite-check / Quick lookup
  │
  ▼
COURTLISTENER API (authenticated via cl_api.py)
  Search: keyword + semantic → metadata, snippets, cluster_id, cite counts
  Citation-lookup (POST): batch-resolve up to 250 citations from document text
  Clusters: full citation arrays, sub-opinions, parallel cites
  │
  ▼
FULL TEXT RETRIEVAL (priority hierarchy)
  0. opinions/{id} → html_with_citations (best: structured, hyperlinked)
  1. storage.courtlistener.com PDFs
  2. txcourts.gov PDFs
  3. Web search → PDF fetch from other sources
  │
  ▼
CITATION GRAPH (opinions-cited endpoint)
  Cite count, citing cases, depth scores
  │
  ▼
STRUCTURED OUTPUT
  JSON intermediate file + Case Index append → caselaw-analyst skill
```

### API Access — cl_api.py

All CourtListener API calls go through the bundled Python wrapper at
`scripts/cl_api.py` inside this skill. The API token is discovered
automatically: `COURTLISTENER_API_TOKEN` env var → `api_keys.courtlistener`
in `~/.legal-skills/config.json` (environment-setup skill) → a
`CL_CONFIG.txt` file placed next to the script. `--config <file>` still
works as an explicit override.

**Calling convention (Bash):**
```bash
SCRIPT="<this skill's folder>/scripts/cl_api.py"

# Search opinions
python3 "$SCRIPT" search --q '"830 S.W.2d 911"' --type o

# Get cluster metadata (parallel citations, sub-opinions)
python3 "$SCRIPT" cluster 1766885

# Get full opinion text (html_with_citations field — Priority 0 for full text)
python3 "$SCRIPT" opinion 9679765

# Batch citation lookup (POST — cite-check mode Step 2A)
python3 "$SCRIPT" citation-lookup --text-file extracted_text.txt
# Or from stdin:
cat brief.txt | python3 "$SCRIPT" citation-lookup

# Opinions cited by a given opinion
python3 "$SCRIPT" opinions-cited --cited_opinion 9679765

# Paginate all results
python3 "$SCRIPT" search --q arbitration --type o --court tex --paginate

# Write large output to file
python3 "$SCRIPT" opinion 9679765 -o /tmp/opinion.json
```

The script handles token auth, rate limits (5,000/hr with backoff), cursor-based pagination, and error handling. **NEVER display the token in conversation or output.**

## Dependencies

- **docx skill** — For extracting text from uploaded .docx files in cite-check mode. Read its SKILL.md for utilities.
- **pdf skill** — For extracting text from uploaded .pdf files in cite-check mode. Read its SKILL.md for utilities.
- **User profile** — `~/.legal-skills/config.json` (written by the environment-setup skill). Read it for identity, fonts, and folder preferences if producing any direct output.
- **cl_api.py** — CourtListener API wrapper bundled at `scripts/cl_api.py` inside this skill. All authenticated API calls go through this script via Bash. See "API Access" section above.
- **API token** — discovered automatically (env var → profile → local CL_CONFIG.txt; see "API Access"). **NEVER display the token in conversation or output.**

## API Endpoints

### Search API

The primary workhorse. Works unauthenticated via `web_fetch` or authenticated via `cl_api.py` (preferred — higher rate limits).

```
GET https://www.courtlistener.com/api/rest/v4/search/?q={query}&type=o&format=json
```

**Parameters:**

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `q` | Search query — supports quoted phrases, booleans, field search | `"830 S.W.2d 911"`, `Alvarado+Farah`, `caseName:Alvarado` |
| `type` | Result type — `o` for opinions (default) | `o` |
| `court` | Court filter code | `tex`, `texapp5`, `ca5`, `scotus` |
| `filed_after` | Start date (YYYY-MM-DD) | `2020-01-01` |
| `filed_before` | End date (YYYY-MM-DD) | `2025-12-31` |
| `semantic` | Enable natural-language semantic search | `true` |
| `highlight` | Enable result highlighting | `on` |
| `format` | Response format | `json` |

**Response structure:**
```json
{
  "count": 405,
  "next": "cursor URL for next page",
  "previous": null,
  "results": [
    {
      "caseName": "Short case name",
      "caseNameFull": "Full party names",
      "citation": ["830 S.W.2d 911", "35 Tex. Sup. Ct. J. 570"],
      "court": "Texas Supreme Court",
      "court_id": "tex",
      "dateFiled": "1992-03-11",
      "cluster_id": 1766885,
      "docket_id": 1707967,
      "docketNumber": "C-8405",
      "citeCount": 408,
      "judge": "Hecht, Mauzy, Doggett",
      "absolute_url": "/opinion/1766885/alvarado-v-farah-manufacturing-co/",
      "opinions": [
        {
          "id": 9679765,
          "type": "lead-opinion",
          "snippet": "First ~400 chars of opinion text...",
          "cites": [1495642, 1637169],
          "download_url": "https://storage.courtlistener.com/..."
        }
      ]
    }
  ]
}
```

**Pagination:** Cursor-based via `next` URL. First page returns up to 20 results.

**Search strategies for different scenarios:**

- **Exact citation lookup:** Quote the reporter cite — `q="830+S.W.2d+911"`. This is the most reliable way to find a specific case.
- **Case name search:** Use key party names — `q=Alvarado+Farah`. Add court filter for precision.
- **Docket number search:** Use the docket number — `q="05-12-01256-CV"`. Add court filter.
- **Topic research (keywords):** Use legal terms — `q=arbitration+non-signatory+compel&court=tex`.
- **Topic research (semantic):** Ask naturally — `q=can a non-signatory be compelled to arbitrate&semantic=true`. Case law only.

### Opinions-Cited API

Returns which later cases cite a given opinion. Use to check if a case remains good law.

```bash
# Via cl_api.py:
python3 "$SCRIPT" opinions-cited --cited_opinion {opinion_id}
# Or via web_fetch (unauthenticated):
# GET https://www.courtlistener.com/api/rest/v4/opinions-cited/?cited_opinion={opinion_id}&format=json
```

**Response fields per result:**
- `citing_opinion` — URL to the opinion that cites this one
- `cited_opinion` — URL to the cited opinion
- `depth` — Citation prominence (1 = most prominent, higher = less prominent)

**Pagination:** Cursor-based. Heavily-cited cases (400+ citations) have many pages. Use `--paginate` flag.

### Authenticated Endpoints — via cl_api.py

These endpoints require token authentication. Call them through `cl_api.py` via Bash.

**Citation Lookup (POST)** — Batch-resolve citations from document text. Accepts up to 64,000 chars, matches up to 250 citations per request. Returns normalized cites with cluster IDs.

```bash
python3 "$SCRIPT" citation-lookup --text-file extracted_text.txt
```

Response is a JSON array. Each element has: `citation` (matched string), `normalized_citations`, `start_index`, `end_index`, `status` (200 = matched, 404 = not found), and `clusters` (array of matching cluster objects with full metadata).

**Opinions (GET)** — Full opinion text in `html_with_citations` field. This is **Priority 0** in the full-text retrieval hierarchy — use before PDF fallbacks.

```bash
python3 "$SCRIPT" opinion {opinion_id}
```

Key fields: `html_with_citations` (best — hyperlinked to other opinions), `html`, `plain_text`, `html_columbia`, `html_lawbox`. Check fields in this order; use the first non-empty one.

**Clusters (GET)** — Cluster metadata including sub-opinions, full citation arrays (all reporters), and parallel citations.

```bash
python3 "$SCRIPT" cluster {cluster_id}
```

Key fields: `sub_opinions` (array of opinion URLs), `citations` (array with volume/reporter/page for each reporter), `case_name`, `case_name_full`, `judges`, `date_filed`.

## Data Model

CourtListener organizes data hierarchically:

```
Courts → Dockets → Clusters → Opinions
```

- **Cluster** groups all opinions for one decision (majority + dissent + concurrence). `cluster_id` appears in CourtListener URLs: `courtlistener.com/opinion/{cluster_id}/...`
- **Opinion** is individual text with author, type, and citation references. `opinion_id` ≠ `cluster_id` — always use `cluster_id` for URLs.

## Texas Court Codes

| Code | Court |
|------|-------|
| `tex` | Texas Supreme Court |
| `texcrimapp` | TX Court of Criminal Appeals |
| `texapp1`–`texapp14` | TX Courts of Appeals, 1st–14th Districts |

Key mappings: `texapp2` = Fort Worth, `texapp5` = Dallas, `texapp8` = El Paso. For the full list or other jurisdictions, query `https://www.courtlistener.com/api/rest/v4/courts/?format=json`.

## Provenance Taxonomy (Research and Quick Lookup Modes)

Three tiers. The gate separates "authority I can cite" from "existence only." There is no middle tier — "Partially Verified" is retired and must not be emitted.

| Tag | Meaning | May carry a holding / quote / relevance? |
|-----|---------|------------------------------------------|
| **Read-Verified** | This case's own full opinion was retrieved and read this session; every quoted sentence and pincite confirmed against it. | **Yes — and ONLY these may.** |
| **Citation-Confirmed (existence only)** | Case exists — citation, court, date confirmed via clusters / citation-lookup — but its own full text was NOT read this session. | **No.** Bare existence row only, visibly flagged "not independently read — confirm before relying." |
| **Unable to Verify** | Not found in free sources. | No. Coverage gap; check Westlaw. |

(The legacy cite-check fallback mode uses the cite-check skill's status vocabulary, which is a separate axis from this taxonomy.)

## Mode Detection

Determine the mode from the user's request:

**Research mode** — User asks a legal question or wants cases on a topic. Examples: "find cases on automatic exclusion under Rule 193.6," "what authority supports [proposition]."

**Cite-check requests** ("cite-check this brief," "verify the citations in this motion") — route to the **cite-check** skill. Only fall back to this skill's legacy mode if Isaacus is unavailable.

**Quick lookup mode** — User asks about a specific known case. Examples: "pull Alvarado v. Farah," "what's the cite count on [case]?"

---

## Research Mode Workflow

### Step 1: Intake

Use the `AskUserQuestion` tool to ask one or two clarifying questions — only what's genuinely ambiguous. If the request is already specific ("Find recent Texas Supreme Court cases on arbitration clause enforceability"), skip directly to search.

Possible clarifications:
- **Jurisdiction** — "Which jurisdiction?" (Texas state, Federal 5th Circuit, all, etc.)
- **Date range** — "Any date range?" (only if topic is time-sensitive)
- **Court level** — "Supreme Court only, or all appellate courts?"

### Step 2: Search CourtListener

Run multiple search strategies via `cl_api.py`:

**Keyword search:**
```bash
python3 "$SCRIPT" search --q "{keywords}" --type o --court {code} --filed_after {date}
```

**Semantic search** (for natural-language questions):
```bash
python3 "$SCRIPT" search --q "{question}" --type o --semantic true
```

**Query construction guidance:**
- Wrap exact phrases in single-then-double quotes: `--q '"830 S.W.2d 911"'`
- Combine name fragments: `--q "Alvarado Farah"`
- Filter by court code: `--court texapp5` for Dallas 5th District
- Date ranges: `--filed_after 2020-01-01 --filed_before 2025-12-31`
- For large result sets: add `--paginate` to merge all pages

### Step 3: Filter and rank

From results, identify the most relevant cases using:
1. Citegeist relevance score (built into result ordering)
2. Cite count (`citeCount` field — higher = more authoritative)
3. Court level (Supreme Court > Appeals > Trial)
4. Recency

For each case, extract: `caseName`, `citation` array, `court`, `dateFiled`, `cluster_id`, `citeCount`, `absolute_url`, and the lead opinion's `id`, `snippet`, `type`, and `download_url`.

### Step 3A: Enrich with cluster metadata (optional)

For the top-ranked cases, pull cluster data for richer metadata — parallel citations, sub-opinions, full citation arrays:

```bash
python3 "$SCRIPT" cluster {cluster_id}
```

This is especially valuable for: (a) confirming parallel citations, (b) identifying dissents and concurrences, and (c) getting the full list of reporters a case appears in.

### Step 4: Retrieve full text

Follow this priority hierarchy for each case worth retrieving. **Stop as soon as full text is obtained.**

**4a. Opinions endpoint (Priority 0 — best source)** — Get structured opinion text via cl_api.py:
```bash
python3 "$SCRIPT" opinion {opinion_id}
```
Check fields in this order: `html_with_citations` (best — contains hyperlinks to cited cases), `html`, `plain_text`, `html_columbia`, `html_lawbox`. Use the first non-empty field. If `html_with_citations` is available, it provides the richest text with embedded citation links. READ the retrieved opinion text — retrieval without reading is not verification. Mark provenance as "Read-Verified — CourtListener opinions endpoint" ONLY after the opinion has actually been read this session, and record `read_full_text_source` (e.g., `opinions/9679765 html_with_citations`).

**4b. CourtListener PDF** — If the opinions endpoint returned no text fields, check `download_url` in the search result's opinion object. If it points to `storage.courtlistener.com`:
```
web_fetch URL: {download_url}
Prompt: "Extract the complete text of this court opinion including all holdings, analysis, and citations."
```

**4c. txcourts.gov** — For Texas cases, use web search:
```
web_search: "{case_name}" site:txcourts.gov filetype:pdf
```
Then `web_fetch` the PDF URL found.

**4d. Web search fallback:**
```
web_search: "{full citation}" full text opinion PDF
```
Attempt `web_fetch` on any PDF links from accessible domains.

**4e. Snippet only — the case is NOT verified.** If no full text is retrievable, the case is **Citation-Confirmed (existence only)**: report its citation, court, and date, but NO holding summary, NO quotations, and NO relevance analysis. Set `independently_read: false` and flag it "not independently read — confirm before relying." Never promote a snippet, headnote, or another opinion's characterization into interpretive content.

### Step 5: Citation graph

For the most important cases (leading authorities, cases the user specifically asked about), check the citation graph:

```bash
python3 "$SCRIPT" opinions-cited --cited_opinion {lead_opinion_id}
# For heavily-cited cases, paginate:
python3 "$SCRIPT" opinions-cited --cited_opinion {lead_opinion_id} --paginate
```

Report:
- Total citing opinions (from `count` field)
- Depth distribution
- Scan citing opinion names for overruling or distinguishing signals

### Step 6: Check the matter's document vault (optional)

If a document-vault or DMS connector is available in the session (any tool
that can answer questions about a matter's stored documents), ask whether
retrieved cases are already in the matter file. Skip this step silently
when no such connector exists.

### Step 7: Output

Write a JSON intermediate file to the working directory with this structure:

```json
{
  "metadata": {
    "mode": "research",
    "query": "user's original query",
    "jurisdiction": "Texas",
    "date_range": "2020-present",
    "date_retrieved": "2026-03-16",
    "sources_checked": ["CourtListener API", "storage.courtlistener.com", "txcourts.gov"]
  },
  "cases": [
    {
      "case_name": "Alvarado v. Farah Manufacturing Co.",
      "full_citation": "830 S.W.2d 911 (Tex. 1992)",
      "parallel_citations": ["35 Tex. Sup. Ct. J. 570", "1992 Tex. LEXIS 24"],
      "court": "Texas Supreme Court",
      "court_id": "tex",
      "date_filed": "1992-03-11",
      "cluster_id": 1766885,
      "opinion_id": 9679765,
      "cite_count": 408,
      "courtlistener_url": "https://www.courtlistener.com/opinion/1766885/alvarado-v-farah-manufacturing-co/",
      "provenance": "Read-Verified",
      "provenance_detail": "Full opinion retrieved via opinions endpoint and read this session.",
      "independently_read": true,
      "read_full_text_source": "opinions/9679765 html_with_citations",
      "snippet": "First 400 chars from search...",
      "full_text": "Complete opinion text or null",
      "full_text_source": "courtlistener opinions endpoint",
      "quotations": [
        {
          "text": "Exact quoted sentence as it will appear in the deliverable.",
          "pincite": "830 S.W.2d at 915",
          "pincite_confirmed_against_read_text": true
        }
      ],
      "citation_graph": {
        "total_citing": 408,
        "overruled_signal": false
      },
      "holding_summary": "Summary from opinion text the skill actually read. MUST be null unless independently_read is true.",
      "relevance_to_query": "How this case relates. MUST be null unless independently_read is true."
    }
  ]
}
```

**Schema rules (hard):** `independently_read: true` REQUIRES a non-null `full_text` taken from THIS case's own opinion. Every quotation destined for a deliverable must appear in `quotations[]` with `pincite_confirmed_against_read_text: true` — confirmed against the read text, not against the document the quote was first seen in. `holding_summary` and `relevance_to_query` MUST be null for any case with `independently_read: false`. Provenance for such cases is "Citation-Confirmed" (or "Unable to Verify"), never "Read-Verified."

### Chat Summary Format (BLUF)

Lead with the answer, not the process. Every in-conversation research or quick-lookup summary follows this structure:

1. **Bottom line** — 1-3 sentences answering the research question first.
2. **Read count** — "N of M cases independently read in full this session." Name every case that is existence-only.
3. **Per case** — short labeled entries, never paragraphs: ***Case Name*, cite (court year)** — **Holding:** one sentence. **Relevance:** one sentence. **Status:** Read-Verified / Citation-Confirmed / Unable to Verify.
4. **Gaps and caveats** — coverage gaps, overruling signals, recommended Westlaw checks.

Bold labels, short lines, no walls of undifferentiated text. If the caselaw-analyst skill will produce a formatted deliverable, pass the JSON file path.

Also append each retrieved case to the Case Index spreadsheet (see Case Index section).

---

## Cite-Check Mode (LEGACY FALLBACK ONLY)

The primary cite-check path is the **cite-check** skill (eyecite + CourtListener + Isaacus). Route all "cite-check this brief" requests there. Use this skill's manual mode ONLY when the Isaacus pipeline is unavailable, and say so explicitly in the output. The manual workflow in brief: extract text (docx/pdf skills), extract citations with eyecite, batch-resolve via `citation-lookup` (`cl_api.py`), per-citation search fallback for 404s, cross-reference discrepancies (case name, reporter, court, date, spelling), assign provenance (Verified / Flagged / Unable to Verify — every status with a rationale), and emit the same JSON format as Research Mode with cite-check metadata.

## Source Hierarchy

Check sources in this order. Stop when full text is obtained.

| Priority | Source | Provides | Auth? | Via |
|----------|--------|----------|-------|-----|
| 0 | CourtListener Opinions API | Full opinion text (html_with_citations) | Yes | cl_api.py |
| 0 | CourtListener Citation-Lookup API | Batch citation resolution (cite-check mode) | Yes | cl_api.py |
| 0 | CourtListener Clusters API | Parallel citations, sub-opinions, full metadata | Yes | cl_api.py |
| 1 | CourtListener Search API | Metadata, snippets, cite counts, citation graph | No* | cl_api.py or web_fetch |
| 2 | storage.courtlistener.com | Opinion PDFs (full text) | No | web_fetch |
| 3 | txcourts.gov | Texas court opinion PDFs | No | web_fetch |
| 4 | Google Scholar (via web search) | Case discovery — full text blocked | No | web_search |
| 5 | CaseLaw Access Project (case.law) | Historical cases through 2020 | No | web_fetch |
| 6 | Web search (FindLaw, Justia, blogs) | Snippets and secondary verification | No | web_search |

*Search works unauthenticated but authenticated access gets higher rate limits (5,000/hr vs. lower).

## Case Index

Optionally, keep a running index of every case this skill retrieves. If
the user's profile (`~/.legal-skills/config.json`) has a `case_index_path`
set, append a row there for each retrieved case:

**Columns:** Case Name | Citation | Parallel Citations | Court | Date Decided | Source | CourtListener URL | Cluster ID | Cite Count | Date Retrieved | Matter | Full Text?

If the spreadsheet doesn't exist yet, create it with these headers using
the xlsx skill. If `case_index_path` is empty or absent, skip this — it is
an optional convenience, not a requirement.

## Edge Cases

- **Wrong reporter series:** Texas S.W.3d began with volume 1 in 1999. Any pre-1999 Texas case citing S.W.3d is a typo. Search both variants.
- **Misspelled case name:** Search by reporter cite (most reliable) rather than name.
- **Memo opinions (no reporter cite):** Search by docket number + court code.
- **String cites:** Multiple citations for one proposition. Parse and verify each independently.
- **Id. references:** Track the last full citation and resolve "Id." to it.
- **Supra references:** Locate the earlier full citation in the document.
- **Rate limits:** Authenticated access via cl_api.py allows 5,000 requests/hour. The script handles backoff automatically. For large cite-check jobs (50+ citations), the citation-lookup endpoint resolves most in a single POST, dramatically reducing API calls.
- **Opinions endpoint empty fields:** Some older opinions have no `html_with_citations`. Check fields in order: `html_with_citations`, `html`, `plain_text`, `html_columbia`, `html_lawbox`. Fall back to PDF if all are empty.

## Endpoint Permission Notes (April 15, 2026)

The following endpoints return **403 Forbidden** with the current API token tier:
- `docket-entries` — requires RECAP membership or higher tier
- `recap-documents` — requires RECAP membership or higher tier

These endpoints are supported by cl_api.py but will fail until the account is upgraded. They are primarily needed for Workstream 2 (docket monitoring), not for caselaw retrieval. All caselaw-retriever endpoints (search, opinions, clusters, citation-lookup, opinions-cited) work at the current tier.