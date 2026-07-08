#!/usr/bin/env python3
"""
self_test.py -- environment probe for the legal-filing-toolkit.

Reports: detected OS, whether the shared shell (bash sandbox) tools the skills
rely on are present, whether the user profile exists, and what is still
missing. Run this first after install so the user sees exactly what the toolkit
detected on THEIR machine before relying on it. Purely diagnostic -- changes
nothing.
"""
import os
import platform
import shutil
import sys

from config_helper import load_config

OK = "[OK]"
MISS = "[MISSING]"
WARN = "[WARN]"


def main():
    print("=" * 60)
    print("legal-filing-toolkit self-test")
    print("=" * 60)

    # Host OS (informational -- skill scripts run in the Linux sandbox, but
    # paths and DMS behavior depend on the user's actual machine).
    print("\nHost / runtime")
    print("  runtime python : " + platform.python_version())
    print("  runtime system : " + platform.system())

    # Tool availability (these run in the shell sandbox, OS-independent).
    print("\nShell tools")
    for tool in ("pdftotext", "python3", "node", "zip"):
        mark = OK if shutil.which(tool) else MISS
        print("  %-10s %s" % (tool, mark))

    print("\nPython packages (Markdown/PDF features)")
    for mod in ("pdfplumber", "pypdf", "fitz"):
        try:
            __import__(mod)
            print("  %-12s %s" % (mod, OK))
        except Exception:
            print("  %-12s %s (install with pip --break-system-packages)" % (mod, WARN))

    # User profile.
    print("\nUser profile")
    cfg = load_config()
    if cfg.get("_config_present"):
        print("  config          " + OK + "  " + cfg["_config_path"])
        if not cfg.get("attorneys"):
            print("  signer identity " + WARN + "  no attorneys set -- court-filing sig blocks will use placeholders")
        else:
            print("  signer identity " + OK + "  " + ", ".join(a.get("name", "?") for a in cfg["attorneys"]))
        print("  filing font     " + OK + "  " + cfg.get("filing_font", ""))
        print("  matter root     " + (OK if cfg.get("matter_root") else WARN) + "  " + (cfg.get("matter_root") or "(not set)"))
        print("  jurisdictions   " + (OK if cfg.get("jurisdictions") else WARN) + "  " + ", ".join(cfg.get("jurisdictions", []) or ["(none)"]))
    else:
        print("  config          " + MISS + "  run environment-setup to create " + cfg["_config_path"])

    print("\nDone. Anything marked [MISSING] or [WARN] should be resolved before relying on that feature.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
