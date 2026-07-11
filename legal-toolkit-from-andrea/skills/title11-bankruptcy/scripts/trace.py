#!/usr/bin/env python3
"""Trace cross-references from a Title 11 section, following the trail across sections.

This is the "chase the definition/reference across sections" workflow the canvas was built
for, rendered as text: it walks each section's outbound Title 11 cross-references, expands
them recursively up to --depth, and shows the heading of every section it reaches (cycles pruned).

Usage: python trace.py 547
       python trace.py 362 --depth 3
"""
import argparse, sys
from _corpus import load_sections, canvas_hint

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section", help="Starting section number, e.g. 547")
    ap.add_argument("--depth", type=int, default=2, help="How many hops to follow (default 2)")
    args = ap.parse_args()

    secs = load_sections()
    start = args.section.strip().lstrip("§ ").strip()
    if start not in secs:
        print(f"No section {start} in Title 11 corpus.", file=sys.stderr)
        sys.exit(1)

    seen = set()
    lines = []
    def walk(num, depth, prefix):
        s = secs.get(num)
        head = s["heading"] if s else "(not in Title 11 corpus)"
        marker = ""
        if num in seen:
            marker = "  ↓ (already shown above)"
            lines.append(f"{prefix}§ {num} — {head}{marker}")
            return
        seen.add(num)
        lines.append(f"{prefix}§ {num} — {head}")
        if not s or depth <= 0:
            return
        for r in s["crossRefs"]:
            walk(r, depth - 1, prefix + "    ")

    walk(start, args.depth, "")
    print(f"Cross-reference trail from 11 U.S.C. § {start} (depth {args.depth}):")
    print("=" * 72)
    print("\n".join(lines))
    print()
    print(canvas_hint(start))

if __name__ == "__main__":
    main()
