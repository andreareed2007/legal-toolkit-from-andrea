#!/usr/bin/env python3
"""Look up a Title 11 section: heading, operative text, cross-references, defined terms.

Usage: python lookup.py 547
       python lookup.py 362 --refs-only
"""
import argparse, sys
from _corpus import load_sections, canvas_hint

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section", help="Section number, e.g. 547")
    ap.add_argument("--refs-only", action="store_true", help="Print only cross-references and defined terms")
    args = ap.parse_args()

    secs = load_sections()
    key = args.section.strip().lstrip("§ ").strip()
    s = secs.get(key)
    if not s:
        print(f"No section {key} in Title 11 corpus.", file=sys.stderr)
        near = [k for k in secs if k.startswith(key[:2])][:8]
        if near:
            print("Nearby sections: " + ", ".join(sorted(near, key=lambda x: int(x))), file=sys.stderr)
        sys.exit(1)

    print(f"11 U.S.C. § {s['sectionNumber']} — {s['heading']}  (Chapter {s['chapter']})")
    print("=" * 72)
    if not args.refs_only:
        print(s["text"])
        print()
    print("Cross-references (other Title 11 sections cited): " +
          (", ".join(s["crossRefs"]) if s["crossRefs"] else "none"))
    print("Defined terms used: " +
          (", ".join(s["definedTerms"]) if s["definedTerms"] else "none"))
    print(canvas_hint(s["sectionNumber"]))

if __name__ == "__main__":
    main()
