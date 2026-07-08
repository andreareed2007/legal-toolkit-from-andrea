# Legal Toolkit (from Andrea)

A growing library of litigation skills, built to be shared: nothing is hardwired
to one person, firm, jurisdiction, or document-management system. Everything
specific to you lives in one profile the toolkit writes on first run — so new
skills added later reuse the same setup instead of asking you again.

**Author:** Andrea Reed · **License:** MIT (see `LICENSE`). Free to use, copy,
modify, and redistribute. No warranty. Attribution appreciated but not required.

## Skills

- **environment-setup** — Run this first. A short interview captures your
  operating system, where your case files live, your document storage, your
  signer identity (name, bar number, email, firm block), your filing font, and
  your home jurisdictions. It writes one shared profile to
  `~/.legal-skills/config.json` that every skill in this library reads. Re-run
  it any time to change a single setting.
- **court-filing** — Creates and validates Word court filings (caption,
  document title, body, signature block, certificates, footer) with a
  build → patch → validate pipeline. Supports Texas state, New York Supreme
  Court, federal, Texas Business Court, and AAA arbitration out of the box, plus
  a **California** scaffold you populate from your own sample pleadings.
- **pdf-to-cowork** — Converts large PDFs (transcripts, briefs, exhibit binders)
  into Markdown/plain text the assistant can read and search natively, with a
  content-gap report. Runs entirely in the shell sandbox — no host-OS
  dependency.

## First run

1. Run **environment-setup** and answer the questions.
2. Run the self-test to confirm what was detected:
   `python3 skills/environment-setup/scripts/self_test.py`
3. For California filings, follow the one-time setup in
   `skills/court-filing/modules/STATE-CA.md` to derive CA format from your own
   sample pleadings.

## Design principles

- **Identity, firm, font, paths, and jurisdictions are configuration**, not code
  — they come from your profile, never from hardcoded values.
- **Self-heal is limited to configuration, paths, OS, and connector
  availability.** It never rewrites the substantive legal-formatting rules the
  validators enforce — those are the guarantee the toolkit exists to provide.
- **No assumed tools.** No dependency on any specific document-management
  system, case-law connector, or assistant integration. Absent capabilities
  degrade gracefully (local-path fallback, manual input, POSIX equivalents).
- **California format is derived from your samples, not approximated from
  memory** — consistent with the toolkit's rule against fabricating
  authoritative formats.

## Growing the library

This plugin is designed to accumulate more skills over time. To add one, drop a
new `skills/<name>/SKILL.md` into the plugin and have it read shared settings
via `skills/court-filing/scripts/config_helper.py` (or a copy of it). It will
reuse the same `~/.legal-skills/config.json` profile and the same
`environment-setup` bootstrap — no new setup step for the user. If a new skill
needs a setting the interview doesn't collect yet, add a question to
`environment-setup` and a default key to `config_helper.py`; `write_config.py`
merges new fields without disturbing existing ones.

## What is NOT included

Client- and matter-specific content and copyrighted pattern material are
intentionally excluded. The California module ships as a scaffold; you supply
your own sample pleadings. Any pattern-instruction or jurisdiction content you
need beyond the bundled caption specs, you provide.
