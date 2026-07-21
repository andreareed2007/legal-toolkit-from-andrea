# Jury Charge Library Builder — Adding a Jurisdiction

Part of the legal-filing-toolkit `jury-charge` skill.

The jury-charge skill drafts only from verbatim pattern text printed in-session by `scripts/pjc_lookup.py`. That script reads a *library*: a folder holding per-section verbatim text files, an index, a claim matrix, and a `library.json` manifest. **The skill ships with no library.** Every jurisdiction gets its own, built with `scripts/build_library.py` through the gated pipeline below. A jurisdiction with no library CANNOT be drafted for — never from memory, no workarounds.

## Library registry

Libraries live under the configured `jury_library_root` (default `~/.legal-skills/jury-instruction-libraries/`, set via the `environment-setup` skill) and are listed in `libraries.json` there (jurisdiction key → path, label, status). Registry status must be `validated` before a library may be used.

## The build pipeline (gates are mandatory)

1. **Identify the official source.** The publisher of record only: state judicial council / supreme court publications (e.g., CACI — Judicial Council of California, free PDF on courts.ca.gov). Commercial or bar-copyrighted sets (e.g., Texas PJC) cannot be fetched; the user must supply their licensed copy. Record the source URL and edition in the manifest. Mirrors (Justia, etc.) may be used for convenience lookups but the LIBRARY is built from the official publication.
2. **Stage the source.** `build_library.py init` creates the folder + `_staging/`. The user downloads the official file into `_staging/` (Claude's fetch tool cannot save large binaries; a one-click download by the user is the normal path). `inventory` verifies and hashes it.
3. **Parse.** `build_library.py parse --format <fmt>` extracts text (pdftotext) and segments it into per-section verbatim files, each stamped with source file, page, edition, and extraction date. The parser only segments extracted text — it never composes or "fixes" instruction language. A new source format means writing a new parser derived from the ACTUAL document structure, never from an assumed one.
4. **Index.** `build_library.py index` builds the section index from the parsed files.
5. **Spot-check gate.** `build_library.py spot-check --n 10` prints random sections; the attorney compares each, word for word, against the official source at the cited page. Any mismatch → fix the parser, re-parse, re-check. The manifest stays `validated: false` and the lookup script REFUSES the library (exit 3) until:
6. **Attorney approval.** `build_library.py approve --confirm ATTORNEY-APPROVED` — given only after the attorney personally reviewed the spot-check output. This flips `validated: true` and updates the registry status.
7. **Claim matrix.** `build_library.py matrix-draft` scaffolds a first-pass claim matrix from series/topic groupings. EVERY entry carries an `ATTORNEY JUDGMENT` flag: instruction selection, variants, defenses, and damages routing are legal judgment. The draft matrix is a finding aid, not a mapping. Curate before relying on `--claim` lookups.

## Per-jurisdiction routing styles

`routing_map.py` phrasing is controlled by the charge map's `routing_style` key: `tx` (default, proven live) or `ca-vf` (California verdict-form phrasing — FIRST CUT, drafted 2026.07.11, not yet used in a live matter; the audit HTML flags it). Before filing in a new jurisdiction, compare generated routing prose against that jurisdiction's official verdict forms and extend the style if needed. Charge-map nodes may use `sources` (jurisdiction-neutral) or the historical `pjc` field; `source_label` sets the citation label (e.g., `CACI`).

## California (CACI) specifics

- Official source: Judicial Council of California Civil Jury Instructions, 2026 edition (adopted December 2025), free PDF: https://courts.ca.gov/system/files/file/judicial_council_of_california_civil_jury_instructions_2026.pdf
- CACI text is © Judicial Council of California but freely published; the library may be shared inside the firm. Never bundle any library into the shareable toolkit package.
- Parse format: `caci-pdf`. Instruction ids like `303`, `430`, `VF-300`. The 2026 PDF has a TOC that repeats every id; the parser keeps the LAST occurrence of each id (body follows TOC). Verify this assumption during spot-check.
- CACI supplements (May/July) amend individual instructions. A supplement means re-staging and re-parsing — never hand-editing section files.

## What never happens

- No section text typed from memory, "obvious" or not.
- No library used with `validated: false`.
- No hand edits inside `sections/` — fix the parser and re-run.
- No blanket trust of the draft claim matrix — attorney curation first.
