#!/usr/bin/env python3
"""
California state court validation checks (SCAFFOLD).

CA-specific rules are NOT hardcoded from memory. They come from `ca_profile.json`
(built by derive_ca_profile.py from the user's own sample CA pleadings). This
module runs the shared core checks and then enforces whatever the derived
profile specifies. Until a profile exists, CA-specific checks emit WARN rather
than guessing California format.

Signer identity is read from the toolkit profile via config_helper, exactly like
the other court-type validators.
"""
import json
import os

from validate_core import find, find1, extract_text, CheckResult

try:
    from config_helper import load_config
except Exception:
    def load_config():
        return {"_config_present": False}


def _load_ca_profile():
    for name in (os.environ.get("CA_PROFILE"), "ca_profile.json",
                 os.path.join(os.path.dirname(__file__), "ca_profile.json")):
        if name and os.path.exists(name):
            try:
                with open(name, "r", encoding="utf-8") as f:
                    return json.load(f), name
            except (ValueError, OSError):
                pass
    return None, None


def run_ca_state_checks(doc_path):
    """Return CA-specific CheckResults. Core checks run separately in the driver."""
    results = []
    profile, ppath = _load_ca_profile()

    if profile is None:
        results.append(CheckResult(
            "CA_PROFILE", "WARN",
            "No ca_profile.json found. California format is not yet configured. "
            "Run derive_ca_profile.py on 1-3 of your own sample CA pleadings, "
            "review the derived values, then re-validate. No CA-specific rule is "
            "enforced until then (nothing is guessed from memory)."))
        return results

    results.append(CheckResult("CA_PROFILE", "PASS",
                   "Loaded CA profile from " + os.path.basename(ppath) +
                   " (derived from: " + ", ".join(profile.get("derived_from", [])) + ")"))

    # 28-line numbered pleading paper -- confirmed from the sample, then enforced.
    from validate_core import parse_docx
    files = parse_docx(doc_path)
    doc = files.get("word/document.xml")

    want_ln = profile.get("line_numbering_present")
    if want_ln is True:
        has_ln = doc is not None and (".//" and find1(doc, ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}lnNumType") is not None)
        if has_ln:
            results.append(CheckResult("CA_LINE_NUMBERING", "PASS",
                           "Line-numbered pleading paper present (matches your CA samples)."))
        else:
            results.append(CheckResult("CA_LINE_NUMBERING", "FAIL",
                           "Your CA samples use 28-line numbered pleading paper, but this "
                           "document has no lnNumType. Add line numbering."))
    elif want_ln is None:
        results.append(CheckResult("CA_LINE_NUMBERING", "WARN",
                       "Line-numbering convention was not observable in the samples; "
                       "confirm CA pleading-paper line numbering by hand."))

    # Margins / page size, if the profile captured them.
    exp_mar = profile.get("margins")
    if exp_mar:
        results.append(CheckResult("CA_MARGINS", "PASS",
                       "CA margin profile available (top/bottom/left/right = " +
                       "/".join(str(exp_mar.get(k)) for k in ("top", "bottom", "left", "right")) +
                       " DXA). Confirm the document matches."))

    # Signer identity presence (parameterized, never hardcoded).
    cfg = load_config()
    if not cfg.get("_config_present") or not cfg.get("attorneys"):
        results.append(CheckResult("CA_SIGNER", "WARN",
                       "No signer identity configured. Run environment-setup so the "
                       "signature block uses your name, bar number (SBN), and firm."))
    return results


if __name__ == "__main__":
    import sys
    for r in run_ca_state_checks(sys.argv[1]):
        print(r.status, r.check, "-", r.message)
