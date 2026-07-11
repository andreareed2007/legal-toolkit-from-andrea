#!/usr/bin/env python3
"""
rename_filings.py — Rename court filing PDFs based on approved rename plan.

Usage:
    python rename_filings.py --renames '<JSON array>'
    python rename_filings.py --renames '<JSON array>' --dry-run

Input JSON format:
    [
        {"old_path": "/full/path/to/old.pdf", "new_name": "2026.04.22 [1] Summons.pdf"},
        ...
    ]

The script renames each file in place (same directory). It uses os.rename()
for reliable error handling and reports per-file results.
"""

import argparse
import json
import os
import sys


def validate_new_name(new_name):
    """Check that the new filename is valid for Windows."""
    invalid_chars = set('<>:"/\\|?*')
    for ch in new_name:
        if ch in invalid_chars:
            return False, f"Invalid character '{ch}' in filename"
    if len(new_name) > 240:
        return False, f"Filename too long ({len(new_name)} chars, max 240)"
    return True, None


def rename_file(old_path, new_name, dry_run=False):
    """
    Rename a single file. Returns a result dict.

    Args:
        old_path: Full path to the existing file
        new_name: New filename (just the name, not full path)
        dry_run: If True, validate but don't actually rename
    """
    result = {
        "old_path": old_path,
        "new_name": new_name,
        "success": False,
        "error": None,
        "dry_run": dry_run,
    }

    # Validate the old file exists
    if not os.path.exists(old_path):
        result["error"] = "Source file does not exist"
        return result

    # Validate the new name
    valid, err = validate_new_name(new_name)
    if not valid:
        result["error"] = err
        return result

    # Build new full path (same directory as old file)
    old_dir = os.path.dirname(old_path)
    new_path = os.path.join(old_dir, new_name)

    # Check full path length
    if len(new_path) > 250:
        result["error"] = f"Full path too long ({len(new_path)} chars, max 250)"
        return result

    # Check if destination already exists
    if os.path.exists(new_path) and os.path.normpath(old_path) != os.path.normpath(new_path):
        result["error"] = f"Destination already exists: {new_name}"
        return result

    # If the old and new are the same, skip
    if os.path.basename(old_path) == new_name:
        result["success"] = True
        result["error"] = "No change needed (same name)"
        return result

    if dry_run:
        result["success"] = True
        result["new_path"] = new_path
        return result

    # Attempt the rename
    try:
        os.rename(old_path, new_path)
        result["success"] = True
        result["new_path"] = new_path
    except PermissionError as e:
        result["error"] = f"Permission denied: {e}"
    except FileExistsError as e:
        result["error"] = f"File already exists: {e}"
    except OSError as e:
        result["error"] = f"OS error: {e}"
    except Exception as e:
        result["error"] = f"Unexpected error: {type(e).__name__}: {e}"

    return result


def main():
    parser = argparse.ArgumentParser(description="Rename court filing PDFs")
    parser.add_argument("--renames", required=True,
                        help="JSON array of {old_path, new_name} objects")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate without actually renaming")
    args = parser.parse_args()

    try:
        renames = json.loads(args.renames)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(renames, list):
        print("Error: --renames must be a JSON array", file=sys.stderr)
        sys.exit(1)

    results = []
    success_count = 0
    fail_count = 0
    skip_count = 0

    for item in renames:
        old_path = item.get("old_path")
        new_name = item.get("new_name")

        if not old_path or not new_name:
            results.append({
                "old_path": old_path,
                "new_name": new_name,
                "success": False,
                "error": "Missing old_path or new_name",
            })
            fail_count += 1
            continue

        result = rename_file(old_path, new_name, dry_run=args.dry_run)
        results.append(result)

        if result["success"]:
            if "No change needed" in (result.get("error") or ""):
                skip_count += 1
            else:
                success_count += 1
        else:
            fail_count += 1

    output = {
        "dry_run": args.dry_run,
        "total": len(renames),
        "success": success_count,
        "failed": fail_count,
        "skipped": skip_count,
        "results": results,
    }

    print(json.dumps(output, indent=2))

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
