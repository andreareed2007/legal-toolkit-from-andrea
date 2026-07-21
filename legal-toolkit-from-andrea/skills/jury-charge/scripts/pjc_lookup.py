#!/usr/bin/env python3
"""pjc_lookup.py - Mandatory verbatim pattern-instruction lookup for the jury-charge skill.

(The filename is historical - since v3.0 this script serves ANY jurisdiction
library that carries a library.json manifest; without one it defaults to the
Texas PJC layout.)

The gate this script enforces: no jury charge question, instruction, or definition
may be drafted unless the verbatim pattern text was printed by this script in
the current session. Sections absent from the index cause a loud failure
(exit 2) - never draft such a section from memory. Libraries whose manifest says
validated=false are refused (exit 3) until the attorney approves them via
build_library.py approve.

Usage:
  python3 pjc_lookup.py --library "<path to 2024 Texas PJCs>" 101.2A 115.3
  python3 pjc_lookup.py --library "<path>" --claim BREACH_OF_CONTRACT_ONE_SIDE
  python3 pjc_lookup.py --library "<path>" --list-claims
  Optional: --ledger <path.json> appends a record of every section read.
"""
import argparse, json, sys, datetime
from pathlib import Path

def load_json(p: Path, what: str):
    if not p.exists():
        sys.exit(f"FATAL: {what} not found at {p}. Check --library path.")
    return json.loads(p.read_text(encoding="utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sections", nargs="*", help="PJC section ids, e.g. 101.2A")
    ap.add_argument("--library", required=True, help="Path to '2024 Texas PJCs' folder")
    ap.add_argument("--claim", help="claim_id from claim_matrix.json - resolves all mapped sections")
    ap.add_argument("--list-claims", action="store_true")
    ap.add_argument("--ledger", help="Path to lookup ledger JSON (appended)")
    args = ap.parse_args()

    lib = Path(args.library)
    mf = lib / "library.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    label = manifest.get("label", "PJC")
    if manifest and not manifest.get("validated", False):
        print("=" * 78)
        print(f"UNVALIDATED LIBRARY: {label} {manifest.get('edition','')} - "
              f"{manifest.get('coverage_note','no coverage note')}")
        print("This library has not passed the spot-check gate. Drafting from it is")
        print("prohibited. Run build_library.py spot-check, get the attorney's approval,")
        print("then build_library.py approve --confirm ATTORNEY-APPROVED.")
        print("=" * 78)
        sys.exit(3)
    index_name = manifest.get("index_file", "pjc_index.json")
    matrix_name = manifest.get("matrix_file", "claim_matrix.json")
    index = load_json(lib / index_name, index_name)
    by_id = {}
    for e in index:
        by_id.setdefault(str(e.get("section_id")), []).append(e)

    matrix = load_json(lib / matrix_name, matrix_name)

    if args.list_claims:
        for c in matrix:
            _n = str(c.get("notes", ""))
            flag = " [ATTORNEY JUDGMENT]" if ("ATTORNEY JUDGMENT" in _n) else ""
            print(f"{c['claim_id']}: {c.get('label','')}{flag}")
        return

    wanted = list(args.sections)
    claim = None
    if args.claim:
        claim = next((c for c in matrix if c["claim_id"] == args.claim), None)
        if claim is None:
            sys.exit(f"FATAL: claim_id '{args.claim}' not in claim_matrix.json. "
                     f"Run --list-claims to see valid ids.")
        for role, items in (claim.get("elements") or {}).items():
            for it in items:
                sid = it.get("section_id")
                if sid and sid not in wanted:
                    wanted.append(sid)
        notes = str(claim.get("notes", ""))
        if "ATTORNEY JUDGMENT" in notes:
            print("=" * 78)
            print("ATTORNEY JUDGMENT FLAG - this claim requires the attorney's call, not the skill's:")
            print(notes)
            print("=" * 78)

    if not wanted:
        sys.exit("Nothing to look up. Pass section ids or --claim.")

    missing, read_log = [], []
    for sid in wanted:
        entries = by_id.get(sid)
        if not entries:
            missing.append(sid)
            continue
        for e in entries:
            tf = lib / e["text_file"]
            print("\n" + "#" * 78)
            print(f"# {label} {sid} - {e.get('title','')[:70]}")
            print(f"# volume={e.get('volume')} chapter={e.get('chapter')} edition={e.get('edition')}")
            print(f"# source file: {e['text_file']}")
            print("#" * 78)
            if tf.exists():
                print(tf.read_text(encoding="utf-8"))
            else:
                missing.append(sid)
                print(f"!! text file missing on disk: {tf}")
                continue
            if e.get("comment_labels"):
                print(f"[COMMENT topics: {'; '.join(e['comment_labels'])}]")
            xrefs = e.get("cross_references") or []
            if xrefs:
                print("[CROSS-REFERENCES - read these too before drafting:]")
                for x in xrefs:
                    print(f"  -> {label} {x.get('target')} ({x.get('relationship')}, {x.get('status')})")
            read_log.append({"section_id": sid, "file": e["text_file"]})

    if args.ledger and read_log:
        lp = Path(args.ledger)
        ledger = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else []
        ledger.append({"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                       "claim": args.claim, "sections_read": read_log})
        lp.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
        print(f"\n[ledger updated: {lp}]")

    print(f"\nTOTALS: requested={len(wanted)} read={len(read_log)} missing={len(missing)}")
    if missing:
        print("=" * 78)
        print(f"STOP - sections NOT in {index_name} or missing on disk: {', '.join(sorted(set(missing)))}")
        print(manifest.get("coverage_note",
              "This section is not in the library index, or its text file is missing on\n"
              "disk. A parsed-but-unindexed volume requires the attorney's explicit\n"
              "go-ahead and an UNVALIDATED flag in the audit; a volume not yet in the\n"
              "library must be staged and parsed through build_library.py first."))
        print("DO NOT draft these sections from memory. No workarounds. If the jurisdiction")
        print("has no library yet, build one with scripts/build_library.py (see")
        print("references/JURY_CHARGE_LIBRARY_BUILDER.md).")
        print("=" * 78)
        sys.exit(2)

if __name__ == "__main__":
    main()
