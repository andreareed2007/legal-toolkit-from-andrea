"""
S1 -- Isaacus credential loader (Project: Isaacus Integration, Plan v3).

Loads the Isaacus API key into the
ISAACUS_API_KEY environment variable and returns a ready Isaacus() client.

HARD RULES:
    * The config file is read with Path.read_text() only.
    * The value is never echoed, printed, logged, written to any file, or
      included in any error message or traceback.
    * No shell command (cat, head, tail, grep, awk, etc.) is ever run on the
      config file, in this module or anywhere else.
    * If the key is ever exposed, the first sentence of the next response must
      be "rotate isaacus now."  That is a human discipline, not code.

This module intentionally exposes no "debug" or "show" helper.  There is no
safe way to print the credential.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_ENV_VAR = "ISAACUS_API_KEY"


class IsaacusConfigError(RuntimeError):
    """Raised when the credential file cannot be located or parsed.

    The exception message never contains any characters read from the
    config file.  Callers can catch this safely.
    """


def _profile_key() -> str:
    """Read api_keys.isaacus from the shared toolkit profile, or ""."""
    import json
    path = os.environ.get("LEGAL_SKILLS_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".legal-skills", "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return str((data.get("api_keys") or {}).get("isaacus") or "").strip()
    except (OSError, ValueError):
        return ""


def _default_config_path() -> Path:
    """Fallback key file: ISAACUS_CONFIG.txt next to this module.

    Discovery order for the key overall (see load_api_key):
      1. ISAACUS_API_KEY environment variable
      2. api_keys.isaacus in ~/.legal-skills/config.json
         (path overridable via LEGAL_SKILLS_CONFIG)
      3. an ISAACUS_CONFIG.txt file in this scripts directory
    """
    return Path(__file__).resolve().parent / "ISAACUS_CONFIG.txt"


def load_api_key(config_path: Optional[Path] = None) -> None:
    """Read the API key and assign it to ``os.environ[ISAACUS_API_KEY]``.

    The value is read into a local variable and immediately assigned; the
    local is allowed to fall out of scope.  Nothing else touches the key.

    Idempotent: if the environment variable is already set and non-empty,
    this is a no-op.  That lets downstream code call ``load_api_key()`` at
    module load without re-reading the file on every import.
    """
    if os.environ.get(_ENV_VAR):
        return

    profile_key = _profile_key()
    if profile_key:
        os.environ[_ENV_VAR] = profile_key
        return

    path = config_path if config_path is not None else _default_config_path()
    if not path.exists():
        # Filename and path are safe to mention; key content is not.
        raise IsaacusConfigError(
            "Isaacus API key not found. Set the ISAACUS_API_KEY environment "
            "variable, add api_keys.isaacus to ~/.legal-skills/config.json "
            "(run the environment-setup skill), or place ISAACUS_CONFIG.txt "
            f"next to the scripts (looked at: {path})."
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        # Re-raise without the raw content.  OSError from read_text does
        # not contain file contents, but wrap defensively anyway.
        raise IsaacusConfigError(
            f"Could not read ISAACUS config file at {path}: {type(exc).__name__}"
        ) from None

    # Take the first non-empty, non-comment line.  Do not log the value.
    first_line = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        first_line = stripped
        break

    if not first_line:
        raise IsaacusConfigError(
            f"ISAACUS config file at {path} contains no usable key line."
        )

    os.environ[_ENV_VAR] = first_line
    # `first_line` and `raw` go out of scope on return.


def get_client(config_path: Optional[Path] = None):
    """Return an ``isaacus.Isaacus`` client, loading the key first.

    The import is deferred so that importing this module does not require
    the SDK to be installed.  That keeps the module cheap to import in
    tests and in skills that may run before ``pip install isaacus``.
    """
    load_api_key(config_path=config_path)
    from isaacus import Isaacus  # deferred import
    return Isaacus()


def get_async_client(config_path: Optional[Path] = None):
    """Return an ``isaacus.AsyncIsaacus`` client.  Same contract as ``get_client``.

    Deferred import, same credential discipline.  Useful for batch paths
    once async helpers land (deferred, plan v3).
    """
    load_api_key(config_path=config_path)
    from isaacus import AsyncIsaacus  # deferred import
    return AsyncIsaacus()


__all__ = ["IsaacusConfigError", "load_api_key", "get_client", "get_async_client"]
