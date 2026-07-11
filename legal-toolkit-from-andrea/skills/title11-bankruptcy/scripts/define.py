#!/usr/bin/env python3
"""Resolve a defined term in Title 11: definition text plus the section/subsection that defines it.

Usage: python define.py claim
       python define.py "domestic support obligation"
"""
import argparse, sys
from _corpus import load_terms, canvas_hint

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("term", help="Defined term, e.g. claim")
    args = ap.parse_args()

    terms = load_terms()
    q = args.term.strip().strip('"“”').lower()
    entry = terms.get(q)

    if not entry:
        matches = sorted(k for k in terms if q in k)
        if not matches:
            print(f"\"{q}\" is not a defined term in the bundled Title 11 corpus.", file=sys.stderr)
            sys.exit(1)
        print(f"No exact match for \"{q}\". Similar defined terms: " + ", ".join(matches), file=sys.stderr)
        sys.exit(1)

    cands = entry.get("candidates", [])
    print(f"Defined term: \"{q}\"  ({len(cands)} definition(s) in Title 11)")
    print("=" * 72)
    for i, c in enumerate(cands, 1):
        loc = f"§ {c.get('section','?')}{c.get('subsection','')}"
        scope = c.get("scope", "")
        print(f"[{i}] {loc}  (scope: {scope})")
        print(f"    “{c.get('definition','').strip()}”")
        print(canvas_hint(c.get("section")))
        print()

if __name__ == "__main__":
    main()
