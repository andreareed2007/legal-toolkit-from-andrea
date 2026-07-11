# Legal Toolkit (from Andrea)

A public Claude plugin **marketplace** hosting a growing library of litigation
skills. Add the marketplace once and Claude keeps the plugin up to date — no
file re-sends.

The toolkit is identity-, document-management-system-, and
jurisdiction-agnostic. A one-time setup step tailors every skill to your machine,
your name and signature block, and the courts you practice in. Nothing about any
particular firm, client, or matter is baked in.

## What's inside

The marketplace currently lists one plugin, `legal-toolkit-from-andrea`, with
seven skills:

- **environment-setup** — the first-run setup. It writes a small local profile
  (your name, bar number, signature block, default jurisdictions, and where your
  files live) that the other skills read. Run this first.
- **court-filing** — builds and validates filing-ready Word documents with proper
  captions, numbering, and signature blocks. Supports Texas state, New York
  Supreme Court, federal, Texas Business Court, and AAA arbitration.
- **pdf-to-cowork** — converts PDFs into clean Markdown or plain text that Claude
  can fully read and search.
- **court-filing-normalizer** — batch-renames court filing PDFs (NYSCEF,
  ECF/PACER, eFileTexas) to a consistent dated, docketed naming convention.
- **date-checker** — verifies every date in a draft against the real calendar
  before work goes out the door.
- **ny-tanbook** — formats New York citations to the Official Reports style
  manual (the "Tanbook").
- **title11-bankruptcy** — offline U.S. Bankruptcy Code section lookup, defined
  terms, and cross-reference tracing.

## Add the marketplace in Cowork

1. Open Cowork and go to **Customize → Plugins**.
2. Choose **Add marketplace → Add from a repository**.
3. Paste this repository's URL.
4. Install **Legal Toolkit (from Andrea)** from the list.

## First run

Run the **environment-setup** skill before anything else. Just say something like
"run the legal toolkit setup" and answer the questions. It stores your profile at
`~/.legal-skills/config.json` on your own machine; the other skills read from it
automatically. You only do this once per computer.

## License

MIT © 2026 Andrea Reed. See `LICENSE`.

## Publishing safety

This is a public repository, so everything in it is world-readable and
permanent. The maintainer screens every change for client-, firm-, and
matter-confidential information before pushing. The author's own name appears
intentionally in the license and manifests and is not treated as confidential.
