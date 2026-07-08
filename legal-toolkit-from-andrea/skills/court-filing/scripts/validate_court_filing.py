#!/usr/bin/env python3
"""
Court Filing Formatting Validator (Modular)
===========================================
Unpacks a .docx file and checks the XML against the configured court-filing
formatting rules.

Modular architecture:
- validate_core.py -- Shared checks for all court types
- validate_tx_state.py -- Texas state court specifics
- validate_ny_state.py -- New York Supreme Court specifics
- validate_federal.py -- Federal (U.S. District Court) specifics
- validate_business.py -- Texas Business Court specifics

Usage:
    python validate_court_filing.py <path_to_docx> [--spacing single|double]
        [--court-type state|tx-state|federal|business|ny-state|aaa-arb|ca-state]
        [--apply-safety-net --case-input case.json]
"""

import sys
import os

from validate_core import run_core_checks


class ValidationReport:
    """Report aggregate validation results."""
    def __init__(self):
        self.results = []

    def add_result(self, result):
        self.results.append(result)

    def add_results(self, results):
        self.results.extend(results)

    def report(self):
        passed = sum(1 for r in self.results if r.status == "PASS")
        failed = sum(1 for r in self.results if r.status == "FAIL")
        warned = sum(1 for r in self.results if r.status == "WARN")

        print("\n" + "=" * 70)
        print("COURT FILING FORMATTING VALIDATION REPORT")
        print("=" * 70)

        categories = {}
        for result in self.results:
            if result.check not in categories:
                categories[result.check] = []
            categories[result.check].append(result)

        for check, items in sorted(categories.items()):
            print(f"\n--- {check} ---")
            for item in items:
                icon = {"PASS": "OK", "FAIL": "X", "WARN": "!"}[item.status]
                print(f"  [{icon}] {item.status}: {item.message}")

        print(f"\n{'=' * 70}")
        print(f"TOTALS: {passed} passed, {failed} failed, {warned} warnings")
        if failed == 0:
            print("ALL CHECKS PASSED")
        else:
            print(f"{failed} FAILURES DETECTED - DOCUMENT DOES NOT COMPLY")
        print("=" * 70)
        return failed


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_court_filing.py <path_to_docx> "
              "[--spacing single|double] "
              "[--court-type state|tx-state|federal|business|ny-state|aaa-arb|ca-state] "
              "[--apply-safety-net --case-input case.json]")
        sys.exit(1)

    docx_path = sys.argv[1]
    if not os.path.exists(docx_path):
        print(f"Error: File not found: {docx_path}")
        sys.exit(1)

    expected_spacing = "double"
    court_type = "state"
    apply_safety_net = False
    case_input_path = None
    for i, arg in enumerate(sys.argv):
        if arg == "--spacing" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1].lower()
            if val in ("single", "double"):
                expected_spacing = val
            else:
                print(f"Error: --spacing must be 'single' or 'double', got '{val}'")
                sys.exit(1)
        if arg == "--apply-safety-net":
            apply_safety_net = True
        if arg == "--case-input" and i + 1 < len(sys.argv):
            case_input_path = sys.argv[i + 1]
        if arg == "--court-type" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1].lower()
            if val in ("state", "tx-state", "federal", "business", "ny-state", "aaa-arb", "ca-state"):
                court_type = "tx-state" if val == "state" else val
            else:
                print(f"Error: --court-type must be 'state', 'tx-state', 'federal', "
                      f"'business', 'ny-state', or 'aaa-arb', got '{val}'")
                sys.exit(1)

    print(f"Validating: {os.path.basename(docx_path)}")
    print(f"Expected body spacing: {expected_spacing}")
    print(f"Court type: {court_type}")
    print("Unpacking and parsing XML...")

    font_size = 24 if court_type != "business" else 28
    require_heading_styles = True if court_type != "ny-state" else False

    report = ValidationReport()
    core_results = run_core_checks(docx_path, spacing=expected_spacing,
                                   font_size=font_size,
                                   require_heading_styles=require_heading_styles)
    report.add_results(core_results)

    if court_type == "tx-state":
        from validate_tx_state import run_tx_state_checks
        tx_results = run_tx_state_checks(docx_path)
        report.add_results(tx_results)

        spec_failures = [r for r in tx_results
                         if r.status == "FAIL" and r.check.startswith("TX_SPEC_")]
        if spec_failures and apply_safety_net:
            if not case_input_path:
                print("\n" + "=" * 70)
                print("SAFETY-NET WOULD APPLY but --case-input not provided.")
                print("Re-run with --apply-safety-net --case-input case.json")
                print("=" * 70)
            else:
                print("\n" + "=" * 70)
                print(f"TX_SPEC_* failures detected ({len(spec_failures)}). "
                      "Invoking python-docx safety-net.")
                print("=" * 70)
                import json
                from caption_safety_net import build_caption, splice_caption_into
                with open(case_input_path) as f:
                    case = json.load(f)
                fresh = build_caption(case, court_type="tx-state")
                splice_caption_into(docx_path, fresh, court_type="tx-state")
                report = ValidationReport()
                core_results = run_core_checks(docx_path, spacing=expected_spacing,
                                               font_size=font_size,
                                               require_heading_styles=require_heading_styles)
                report.add_results(core_results)
                tx_results_2 = run_tx_state_checks(docx_path)
                report.add_results(tx_results_2)
                print("Safety-net applied. Re-validation results below.")
    elif court_type == "federal":
        from validate_federal import run_federal_checks
        fed_results = run_federal_checks(docx_path)
        report.add_results(fed_results)

        spec_failures = [r for r in fed_results
                         if r.status == "FAIL" and r.check.startswith("FED_SPEC_")]
        if spec_failures and apply_safety_net:
            if not case_input_path:
                print("\n" + "=" * 70)
                print("SAFETY-NET WOULD APPLY but --case-input not provided.")
                print("Re-run with --apply-safety-net --case-input case.json")
                print("=" * 70)
            else:
                print("\n" + "=" * 70)
                print(f"FED_SPEC_* failures detected ({len(spec_failures)}). "
                      "Invoking python-docx safety-net.")
                print("=" * 70)
                import json
                from caption_safety_net import build_caption, splice_caption_into
                with open(case_input_path) as f:
                    case = json.load(f)
                fresh = build_caption(case, court_type="federal")
                splice_caption_into(docx_path, fresh, court_type="federal")
                report = ValidationReport()
                core_results = run_core_checks(docx_path, spacing=expected_spacing,
                                               font_size=font_size,
                                               require_heading_styles=require_heading_styles)
                report.add_results(core_results)
                fed_results_2 = run_federal_checks(docx_path)
                report.add_results(fed_results_2)
                print("Safety-net applied. Re-validation results below.")
    elif court_type == "ny-state":
        from validate_ny_state import run_ny_state_checks
        ny_results = run_ny_state_checks(docx_path)
        report.add_results(ny_results)
    elif court_type == "business":
        from validate_business import run_business_checks
        bc_results = run_business_checks(docx_path)
        report.add_results(bc_results)
    elif court_type == "aaa-arb":
        from validate_aaa_arb import run_aaa_arb_checks
        aaa_results = run_aaa_arb_checks(docx_path)
        report.add_results(aaa_results)
    elif court_type == "ca-state":
        from validate_ca_state import run_ca_state_checks
        ca_results = run_ca_state_checks(docx_path)
        report.add_results(ca_results)

    failures = report.report()
    sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    main()
