#!/usr/bin/env python3
"""
write_config.py -- persist the toolkit user profile.

Usage:
    python write_config.py <profile.json>      # merge given JSON into config
    python write_config.py -                   # read JSON from stdin

Writes to $LEGAL_SKILLS_CONFIG or ~/.legal-skills/config.json (created if
absent). Merges over any existing config so a re-run can update one field.
Prints the final path and a short summary. Never writes into the plugin
directory -- installed skills are read-only.
"""
import json
import os
import sys

from config_helper import config_path, load_config, _deep_merge


def main():
    if len(sys.argv) != 2:
        print("usage: python write_config.py <profile.json | ->", file=sys.stderr)
        return 2
    src = sys.argv[1]
    raw = sys.stdin.read() if src == "-" else open(src, "r", encoding="utf-8").read()
    incoming = json.loads(raw)
    if not isinstance(incoming, dict):
        print("profile must be a JSON object", file=sys.stderr)
        return 2

    existing = load_config()
    existing.pop("_config_present", None)
    path = existing.pop("_config_path", None) or config_path()
    merged = _deep_merge(existing, incoming)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print("wrote config to: " + path)
    print("os: " + str(merged.get("os", "")))
    print("matter_root: " + str(merged.get("matter_root", "")))
    print("attorneys: " + str([a.get("name") for a in merged.get("attorneys", [])]))
    print("filing_font: " + str(merged.get("filing_font", "")))
    print("jurisdictions: " + str(merged.get("jurisdictions", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
