---
name: environment-setup
description: >
  First-run setup for the legal-filing-toolkit. USE THIS SKILL the first time
  any toolkit skill runs, or when the user says "set up the toolkit", "run
  setup", "reconfigure my filing profile", "change my signature block", "change
  my filing font", "point the toolkit at my files", or when another toolkit
  skill reports that no configuration was found. Runs a short interview and
  writes a single user profile the other skills read. Also handles updating one
  setting later without redoing the whole interview.
---

> **Version:** v1.0.0 · First-run bootstrap for the legal-filing-toolkit.

# Environment Setup — Toolkit Bootstrap

This skill tailors the toolkit to the current user's machine, files, identity,
and jurisdictions. It writes ONE profile that every other skill reads, so the
installed skill files never need editing.

A packaged skill cannot literally ask questions at install time. This skill is
the substitute: it runs on first use, checks for the profile, and if none
exists runs the interview and writes it. Later runs read the profile silently.

## Where the profile lives

`~/.legal-skills/config.json` (override with the `LEGAL_SKILLS_CONFIG`
environment variable). This is a writable, cross-platform location — `~`
resolves on Windows, macOS, and Linux. Never write the profile inside the
plugin directory; installed skills are read-only.

## Step 1 — Probe first, never assume

Run the self-test before anything else and read its output:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/environment-setup/scripts/self_test.py"
```

(If `$CLAUDE_PLUGIN_ROOT` is not set in the shell, locate the script with
`find /sessions -name self_test.py -path '*environment-setup*' 2>/dev/null | head -1`
and run that path. The scripts import `config_helper.py` from the same folder,
so run them from that folder or copy the pair to a writable dir first.)

If the self-test shows the profile is already present and the user did not ask
to reconfigure, stop — setup is done. Report what was detected and hand back to
the skill the user actually wanted.

## Step 2 — Interview (only when no profile, or user asked to change one)

Use the AskUserQuestion tool. Ask only what is still unknown; a user changing a
single setting gets only that question. Group related questions. Cover:

1. **Operating system** — Windows / macOS / Linux. Drives the self-heal: on
   macOS/Linux the skills must avoid any Windows-only tool (e.g. PowerShell)
   and use the POSIX/Python equivalent.
2. **File-system layout** — the root folder where matter/case files live, and a
   free-text note on how they organize (by client, by matter number, etc.).
   Do NOT assume any particular structure.
3. **Document storage / DMS** — NetDocuments, Dropbox, OneDrive, Google
   Drive, a local folder, or another document store. Capability only — the
   skills must not hard-depend on any one of these.
4. **Connectors** — ask which assistant connectors they have (document Q&A,
   case-law search, chat, e-filing, etc.). Record by capability, never by a
   specific server ID. Assume nothing — no particular assistant integration
   or MCP is required.
5. **Signer identity for filings** — for each attorney who signs: full name,
   bar-number label (e.g. "State Bar No.", "Texas Bar No.", "SBN"), bar number,
   email. Then firm name line(s), address line(s), phone, fax, and the client
   description line.
6. **Filing font** — the body font their court filings use (offer Century
   Schoolbook and Times New Roman as common options; let them type their own).
7. **Home jurisdiction(s)** — which courts they file in most (e.g. CA-STATE,
   FEDERAL, NY-STATE, TX-STATE). This drives which court-filing modules matter.

## Step 3 — Write the profile

Assemble answers into a JSON object matching this shape and write it:

```json
{
  "os": "macos",
  "matter_root": "~/Documents/Cases",
  "matter_layout_notes": "one folder per client, matter subfolders inside",
  "dms": "dropbox",
  "connectors": ["document Q&A", "case-law search"],
  "attorneys": [
    {"name": "Jane Q. Public", "bar_label": "State Bar No.", "bar": "300111", "email": "jpublic@example.com"}
  ],
  "firm": {
    "name_lines": ["Example Law Group,", "  L.L.P."],
    "tokens": ["EXAMPLE"],
    "address_lines": ["100 Main Street, Suite 100", "City, State 00000"],
    "phone": "(000) 000-0000 (Telephone)",
    "fax": "(000) 000-0000 (Facsimile)"
  },
  "filing_font": "Century Schoolbook",
  "default_signer": "Jane Q. Public",
  "jurisdictions": ["CA-STATE"]
}
```

`firm.tokens` are the uppercase words the court-filing validators look for in a
signature block (usually the first distinctive word of the firm name). Write
the profile with:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/environment-setup/scripts/write_config.py" /path/to/profile.json
```

(Or pipe JSON: `... write_config.py -`.) `write_config.py` merges over any
existing profile, so a one-field change preserves everything else. Re-run the
self-test to confirm.

## Step 4 — Hand back

Tell the user what was saved and that they can re-run this skill any time to
change a setting. Then proceed with whatever they originally asked for.

## Self-heal scope — hard guardrail

Self-heal in this toolkit is limited to **configuration, file paths, OS
differences, and connector availability**. When a required tool or connector is
absent, degrade gracefully: substitute a cross-platform equivalent, fall back to
a local path, or prompt for manual input. Self-heal must **never** rewrite the
substantive legal logic of any skill — e.g. the court-filing validators'
formatting rules. Those rules are the guarantee the toolkit exists to provide;
altering them to make a check pass would silently defeat the tool. If a legal-
logic check fails, report it — do not "fix" it by changing the rule.
