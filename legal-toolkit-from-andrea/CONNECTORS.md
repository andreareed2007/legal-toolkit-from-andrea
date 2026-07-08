# Connectors

## How tool references work

This toolkit is connector-agnostic. It never assumes a specific document-
management system (NetDocuments, Dropbox, OneDrive, Google Drive, a
local disk), case-law provider, or assistant integration. Instead, the
`environment-setup` skill records the capabilities you have — by category, not
by product or server ID — in your profile at `~/.legal-skills/config.json`.

Each skill checks for the capability it needs at runtime and degrades
gracefully when it is absent:

| Capability             | If present                         | If absent (self-heal)                          |
| ---------------------- | ---------------------------------- | ---------------------------------------------- |
| Document Q&A / vault    | Use it to pull matter documents    | Prompt for manual document input               |
| Case-law search         | Use it for authority lookups       | Skip; ask the user to supply citations         |
| Document storage / DMS  | Read/write in your configured store | Fall back to a local path under `matter_root`  |
| Shell sandbox           | Run converters and validators      | Report unavailable; never fake output          |

Set these during `environment-setup`. Nothing here is required for the
core court-filing and PDF-conversion pipelines, which run in the shell sandbox.
