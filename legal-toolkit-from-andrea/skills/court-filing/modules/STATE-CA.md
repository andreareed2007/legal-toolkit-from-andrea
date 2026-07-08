# California State Court — Module (SCAFFOLD)

> **This module is a scaffold, not a finished spec.** Unlike the TX/NY/federal
> modules, California caption geometry, signature-block format, proof-of-service
> conventions, font, and line numbering are **not** pre-encoded here. They are
> derived from YOUR OWN sample California pleadings during a one-time setup, so
> the rules match the courts you actually file in — and so nothing is invented
> from memory. Do not draft a CA filing off this module until setup is done.

`--court-type ca-state`

## Why derive instead of hardcode

California trial-court pleadings differ structurally from Texas and New York in
ways that must be observed, not guessed — most notably **28-line numbered
pleading paper** with a ruled line-number column, and local-rule variation in
caption and proof-of-service format. Approximating these from training data is
exactly the failure this toolkit avoids. The setup routine reads real samples.

## One-time setup (first CA filing)

1. **Ask the user for 1–3 sample California state pleadings** they trust —
   their own prior filings or public filings from the same court/county. Word
   (`.docx`) is ideal because geometry is measurable; if only PDFs exist, ask
   for at least one `.docx`, or reconstruct geometry with the user's help.
2. **Derive the profile** from the samples:

   ```bash
   python3 scripts/derive_ca_profile.py sample1.docx sample2.docx \
       --out scripts/ca_profile.json
   ```

   This observes page size, margins, body font/size, line-numbering column,
   caption-table dimensions, and body spacing, and writes `ca_profile.json`.
   Values it cannot observe are left null — fill those from the samples WITH the
   user; never invent them.
3. **Review the derived values with the user** against the samples, especially:
   caption geometry (column widths, party/court arrangement), the **28-line
   pleading-paper line numbering**, proof-of-service wording, and the signature/
   verification block. Confirm the SBN label (California uses "SBN" / "State
   Bar No.") and filing font.
4. **Validate** CA filings through the normal pipeline with `--court-type
   ca-state`. `validate_ca_state.py` enforces whatever the derived profile
   specifies and reads signer identity from the toolkit profile
   (`environment-setup`). Until `ca_profile.json` exists, CA-specific checks are
   WARN-only — the validator will not guess California format.

## What comes from where

- **Identity** (signer name, SBN, firm block, filing font): the toolkit profile
  at `~/.legal-skills/config.json` (`environment-setup`). Set `bar_label` to
  "SBN" or "State Bar No." for California.
- **CA format geometry**: `scripts/ca_profile.json`, derived from the user's
  samples (this module's setup).
- **Shared formatting** (numbered paragraphs, footer, certificate spacing,
  signature technique): the core `SKILL.md` — jurisdiction-neutral, unchanged.

## Known California structural flags to confirm from samples

These are prompts for what to verify in the samples — not assertions to encode
blind:

- **28-line pleading paper** with a left-margin line-number column (`lnNumType`,
  `countBy=1`). Confirm present and how it restarts per page.
- **Line spacing**: many CA courts require double-spaced numbered lines aligned
  to the 28-line ruling. Confirm from the sample rather than assuming.
- **Caption**: confirm the court/party arrangement and case-number placement in
  the sample; CA differs from the TX §-column caption.
- **Proof of service**: California uses its own POS format (often a POS-010
  Judicial Council form or a rule-compliant declaration). Capture the exact
  wording the user's samples use.

## Self-heal scope

Setup adapts to the user's samples, OS, and paths. It must never rewrite the
core validators' shared formatting rules. If the derived CA rules conflict with
a core check for a genuine California reason, surface it for the user's decision
— do not silently change either one.
