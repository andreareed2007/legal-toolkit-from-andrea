#!/usr/bin/env python3
"""
config_helper.py -- shared configuration loader for the legal-filing-toolkit.

The toolkit stores one user profile at a writable, cross-platform location so
that installed (read-only) skill files never need editing. The environment-setup
skill writes this file on first run; every other script reads it.

Resolution order for the config path:
  1. $LEGAL_SKILLS_CONFIG (explicit override)
  2. ~/.legal-skills/config.json   (default; ~ works on Windows, macOS, Linux)

Every consumer must degrade gracefully when the file is missing: return safe,
generic defaults and set _config_present=False so the caller can emit a WARN
telling the user to run environment-setup. Never crash on a missing config.
"""
import json
import os

DEFAULTS = {
    "version": "1.0",
    "os": "",
    "matter_root": "",
    "matter_layout_notes": "",
    "dms": "",
    "connectors": [],
    "attorneys": [],          # [{"name","bar_label","bar","email"}]
    "firm": {
        "name_lines": [],     # e.g. ["Example Law Group,", "  L.L.P."]
        "tokens": [],         # uppercase substrings the BC sig-block check matches
        "address_lines": [],
        "phone": "",
        "fax": "",
    },
    "filing_font": "Century Schoolbook",
    "bar_label": "State Bar No.",
    "default_signer": "",
    "jurisdictions": [],
}


def config_path():
    override = os.environ.get("LEGAL_SKILLS_CONFIG")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".legal-skills", "config.json")


def _deep_merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    """Return the merged config dict. Always includes _config_present (bool)."""
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        cfg = _deep_merge(DEFAULTS, {})
        cfg["_config_present"] = False
        cfg["_config_path"] = path
        return cfg
    cfg = _deep_merge(DEFAULTS, data if isinstance(data, dict) else {})
    cfg["_config_present"] = True
    cfg["_config_path"] = path
    return cfg


def attorney_names(cfg=None):
    cfg = cfg or load_config()
    return [a.get("name", "") for a in cfg.get("attorneys", []) if a.get("name")]


def firm_tokens(cfg=None):
    cfg = cfg or load_config()
    return [t.upper() for t in cfg.get("firm", {}).get("tokens", []) if t]


if __name__ == "__main__":
    c = load_config()
    print("config path:", c["_config_path"])
    print("present:", c["_config_present"])
    print("attorneys:", attorney_names(c))
    print("firm tokens:", firm_tokens(c))
