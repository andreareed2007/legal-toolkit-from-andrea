# Isaacus Integration -- Environment Setup (S2)

**Project:** Isaacus Integration, Plan v3
**Chunk:** S2 -- Environment setup and version pinning
**Last updated:** 2026-07-06 (Good-Law Engine — goodlaw verb, cc_goodlaw gate)

This document is the ground truth for what needs to be installed before any
cite-check code in this folder will run.  It complements (but does not
duplicate) the project plan.  If the plan and this file disagree, this file
wins -- it is the one that gets executed.

---

## 1. Python version

Python **3.9 or newer**.  All modules use:

- PEP 604 `X | None` unions (`3.10+` idiom, but via `from __future__ import annotations` we stay 3.9-compatible)
- `dataclasses` and `typing` features available in 3.9
- No match statements, no `tomllib`, no 3.11-only features

Verify:

```bash
python --version
# Python 3.9.x or newer
```

---

## 2. Required packages

Four pip packages, pinned at the versions validated against plan v3.
Pins prevent silent API drift from breaking cite-check mid-filing.

| Package          | Pinned version | Purpose                                    |
|------------------|----------------|--------------------------------------------|
| `isaacus`        | latest (0.21.x validated 2026.07.19) | Core SDK -- rerank, QA, classify, embed, enrich. Install unpinned; SKILL.md Step 0 is the operative installer. |
| `httpx[socks]`   | `>=0.27,<0.29` | HTTP client with SOCKS proxy support       |
| `semchunk`       | `>=4.1,<5.0`   | Token-aware + AI chunking; offsets. Use a char-based token counter to avoid the HF tokenizer 403. |
| `text2markdown`  | `==0.1.5`      | Structured Markdown conversion (wraps enricher). Only 0.1.x versions exist on PyPI as of 2026-04-16; pin the exact latest. |

### Install command

From the project folder:

```bash
pip install \
  "isaacus" \
  "httpx[socks]>=0.27,<0.29" \
  "semchunk>=4.1,<5.0" \
  "text2markdown==0.1.5" \
  "eyecite==2.7.8"
```

In the Cowork sandbox specifically, pip may require `--break-system-packages`:

```bash
pip install --break-system-packages \
  "isaacus" \
  "httpx[socks]>=0.27,<0.29" \
  "semchunk>=4.1,<5.0" \
  "text2markdown==0.1.5" \
  "eyecite==2.7.8"
```

**eyecite pinned 2026.07.03, HARD pin 2026.07.19:** validated against eyecite 2.7.8 (parses NY Slip Op (U) forms natively on the Gold-Set-A fixture). Citation detection backbone as of the July 2026 rebuild. The pin is exact (==2.7.8): the old floating install would silently adopt a future eyecite release with no gate run. Upgrade deliberately — bump the pin, then run pytest + all six gates before any real brief.

### Runner state directory (2026.07.04)

The checkpointed runner (`cite_check_runner.py`) keeps its state pickles in
`CC_STATE_DIR` (default `/tmp`). ALWAYS set it explicitly per session:

```bash
export CC_STATE_DIR=/tmp/cc_state && mkdir -p /tmp/cc_state
```

Stale state dirs owned by a previous VM's user survive with a different owner
and block writes (PermissionError) — point CC_STATE_DIR at a fresh directory;
old files usually remain readable for salvage. A new `build` clears all
downstream state in the dir (resolve/ckpt/gaps/result) because the maps are
index-keyed per document.

### Runner verbs (2026.07.04)

```
build "<brief>" "<MATTER>" "<doc name>"   # eyecite detection + props manifest (no API)
resolve                                    # batched citation-lookup primary; re-run until gap manifest prints
patch_gap <idx> <text_file> <url> [src]    # gated ingest of agent-fetched opinion text (<=2 attempts/gap)
props <answers.json>                       # gated ingest of agent-written propositions (provenance-marked)
phase2                                     # Isaacus verify + star-page mapping + second opinion
goodlaw                                    # treatment-signal pass (2026.07.06); re-run until "treatment: done"
render "<out.html>"                        # HTML report (+ Treatment section when goodlaw state exists)
```

### DOCX rendering

`cite_check_report.render_docx` requires Node + the `docx` npm package:
`npm install docx` (set `NODE_PATH` to the install's `node_modules` if it is
not resolvable from the script's temp dir).

---

## 3. Credential file

The SDK never receives a bare API key argument in this project.  All six
Python modules resolve credentials via `isaacus_config.get_client()`, which
reads the key in this order:

```
1. ISAACUS_API_KEY environment variable
2. api_keys.isaacus in ~/.legal-skills/config.json (environment-setup skill)
3. ISAACUS_CONFIG.txt next to the scripts (one line, bare key)
```

Key files are **not** tracked in git and are **not** read by any shell
command in this project.  The only allowed read path is
`Path.read_text()` inside `isaacus_config.py`.

If no key is found anywhere, `isaacus_config.py` raises
`IsaacusConfigError` with a path-only message (never file contents).

---

## 4. Known quirks

### 4.1 SOCKS proxy import error

If the environment does not have `httpx[socks]` (just plain `httpx`),
the SDK fails at import time with:

```
ImportError: Using SOCKS proxy, but the 'socksio' package is not installed.
```

Fix: always install `httpx[socks]` rather than `httpx`.  This is why the
pin above includes the `[socks]` extra explicitly.

### 4.2 semchunk deferred import

`isaacus_chunker.build_chunker()` imports `semchunk` **inside the function
body**, not at module import.  This is intentional: it lets the rest of
the cite-check pipeline import cleanly in environments where semchunk
isn't yet installed (e.g., running `py_compile` as part of CI before
install).

### 4.3 text2markdown deferred import

Same pattern -- `isaacus_helpers.to_markdown()` imports `text2markdown`
inside the call.  Nothing else in the cite-check pipeline depends on it,
so skipping the install will not break cite-check.  It is only needed
when converting raw filings to Markdown for display/review.

### 4.4 Tokenizer availability

`isaacus/kanon-2-tokenizer` is downloaded on first use by semchunk.
First run after install will take a few seconds to fetch; subsequent
runs are instant.  If behind a proxy, ensure Hugging Face Hub is
reachable or pre-download into the local cache.

---

## 5. Verification

After install, the following should all succeed:

```bash
cd "<this skill's scripts/ folder>"

# Syntax check -- no network, no credentials
python -m py_compile isaacus_config.py
python -m py_compile isaacus_helpers.py
python -m py_compile isaacus_chunker.py
python -m py_compile cite_check.py
python -m py_compile cite_check_report.py
python -m py_compile cite_check_runner.py
python -m py_compile cl_resolver.py
python -m py_compile cc_goodlaw.py
python -m py_compile cc_goodlaw_gate.py

# Import check -- tests package installation, still no credentials
python -c "import isaacus_config, isaacus_helpers, isaacus_chunker"
python -c "import cite_check, cite_check_report, cite_check_runner, cl_resolver"
python3 cc_prop_gate.py        # regression gate -- must print PASS
python3 cc_goodlaw_gate.py     # treatment-engine gate (offline fixtures) -- must print PASS
python3 -m pytest tests/ -q    # unit suite (94 incl. goodlaw guards)

# Live check -- requires ISAACUS_CONFIG.txt
python -c "from isaacus_config import get_client; c = get_client(); print('Client OK:', type(c).__name__)"
```

The live check is the only step that touches the API key.  It constructs
a client but does not make any billable calls.

---

## 6. Cost ceiling (from plan v3, section 7)

- Cite-check pipeline baseline: **$25-$90 per month** for the author's
  expected usage (several cite-checks per week, occasional long briefs
  that trigger AI chunking).
- AI chunking (`isaacus_chunker.chunk_if_needed` with the AI branch
  taken) consumes one enrichment call per ~1M-character block.  The
  32,000-character gate is sized so this only triggers for very long
  documents.
- The `inextractability_score` QA signal costs nothing extra beyond the
  normal QA call -- it is returned alongside the passage score.

---

## 7. Sequence for a clean install

1. Install the cite-check skill (the scripts ship inside it).
2. Provide the Isaacus key: run the environment-setup skill (writes
   `api_keys.isaacus` to `~/.legal-skills/config.json`), or set
   `ISAACUS_API_KEY`, or place `ISAACUS_CONFIG.txt` next to the scripts
   (one line, bare API key, no prefix).
3. Run the install command from section 2.
4. Run the verification sequence from section 5.
5. On success, the cite-check pipeline is ready.  Validate any code change
   against a brief you know well before a live run (the author's regression
   gates and gold sets are not distributed).
   (`cite_check_quality_check.py` is retired to `_Archive/` as of 2026.07.04.)

---

## 8. Upgrade policy

Pin bumps happen only when:

- Isaacus announces a new model family that requires a new SDK major
  (update `MODEL_*` constants in `isaacus_helpers.py` in the same commit).
- A CVE or correctness bug is fixed in one of the four dependencies.
- The C6 quality check still agrees with the gold cite-check after the
  bump.  Do not upgrade without re-running C6.

Pin bumps must update this file, the plan, and the imports in one
atomic commit.
