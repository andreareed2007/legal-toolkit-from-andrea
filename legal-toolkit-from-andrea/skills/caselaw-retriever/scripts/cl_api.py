#!/usr/bin/env python3
"""
cl_api.py — CourtListener API v4 wrapper with token authentication.

Usage:
    python3 cl_api.py <endpoint> [params...]

Token discovery: COURTLISTENER_API_TOKEN env var, then api_keys.courtlistener
in ~/.legal-skills/config.json, then CL_CONFIG.txt next to this script.
--config <file> still works as an explicit override.

Endpoints:
    search              Search opinions, dockets, people, etc.
    cluster <id>        Get cluster metadata (sub-opinions, citation arrays)
    opinion <id>        Get full opinion text (html_with_citations)
    citation-lookup     POST text to resolve up to 250 citations (reads stdin or --text-file)
    opinions-cited      Get cases that cite a given opinion
    docket <id>         Get docket metadata
    docket-entries      Get entries for a docket (incremental with --since)
    recap-documents     Get RECAP documents for a docket entry
    people              Search people (judges) by name
    positions <id>      Get positions for a person
    educations <id>     Get education history for a person
    aba-ratings <id>    Get ABA ratings for a person
    political-affiliations <id>  Get political affiliations for a person
    parties             Get parties for a docket
    attorneys           Get attorneys for a docket
    bankruptcy-info     Get bankruptcy information for a docket
    originating-court   Get originating court info (removed/transferred cases)

All output is JSON to stdout. Use --paginate to follow cursor-based pagination
and merge all pages into a single result array.

Rate limit: 5,000 requests/hour (authenticated). The script tracks request
count and pauses if approaching the limit.

Examples:
    # Search for opinions
    python3 cl_api.py search --q "830 S.W.2d 911" --type o

    # Get a cluster
    python3 cl_api.py cluster 1766885

    # Citation lookup (POST, from file)
    python3 cl_api.py citation-lookup --text-file brief.txt

    # Docket entries since a date
    python3 cl_api.py docket-entries --docket 72343932 --since 2026-04-01

    # Judge lookup
    python3 cl_api.py people --name_last Rakoff

    # Full pagination (all pages merged)
    python3 cl_api.py --config CL_CONFIG.txt search --q arbitration --type o --court tex --paginate
"""

import argparse
import json
import sys
import time
import os

try:
    import requests
except ImportError:
    print(json.dumps({"error": "requests library not installed. Run: pip install requests --break-system-packages"}), file=sys.stderr)
    sys.exit(1)


BASE_URL = "https://www.courtlistener.com/api/rest/v4"

# Rate limit tracking (per-process; resets on restart)
_request_count = 0
_window_start = time.time()
RATE_LIMIT = 5000
RATE_WINDOW = 3600  # 1 hour in seconds
BACKOFF_THRESHOLD = 4800  # Start slowing down at 96% of limit


def _load_token(config_path=None) -> str:
    """Load the CourtListener API token.

    Discovery order:
      1. --config <file> (first non-empty, non-comment line), if given
      2. COURTLISTENER_API_TOKEN environment variable
      3. api_keys.courtlistener in ~/.legal-skills/config.json
         (path overridable via LEGAL_SKILLS_CONFIG)
      4. a CL_CONFIG.txt file next to this script
    """
    if config_path:
        path = os.path.expanduser(config_path)
        if not os.path.isfile(path):
            print(json.dumps({"error": f"Config file not found: {path}"}), file=sys.stderr)
            sys.exit(1)
        with open(path, "r") as f:
            for line in f.read().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped
        print(json.dumps({"error": "Config file is empty — no API token found."}), file=sys.stderr)
        sys.exit(1)
    env = os.environ.get("COURTLISTENER_API_TOKEN", "").strip()
    if env:
        return env
    cfg_path = os.environ.get("LEGAL_SKILLS_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".legal-skills", "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = str((data.get("api_keys") or {}).get("courtlistener") or "").strip()
        if key:
            return key
    except (OSError, ValueError):
        pass
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CL_CONFIG.txt")
    if os.path.isfile(local):
        with open(local, "r") as f:
            for line in f.read().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped
    print(json.dumps({"error": "CourtListener API token not found. Set "
                      "COURTLISTENER_API_TOKEN, add api_keys.courtlistener to "
                      "~/.legal-skills/config.json (run environment-setup), "
                      "pass --config <file>, or place CL_CONFIG.txt next to "
                      "cl_api.py."}), file=sys.stderr)
    sys.exit(1)


def _check_rate_limit():
    """Pause if approaching the hourly rate limit."""
    global _request_count, _window_start
    now = time.time()
    elapsed = now - _window_start
    if elapsed >= RATE_WINDOW:
        # Reset the window
        _request_count = 0
        _window_start = now
        return
    if _request_count >= RATE_LIMIT:
        wait = RATE_WINDOW - elapsed + 1
        print(json.dumps({"warning": f"Rate limit reached. Waiting {wait:.0f}s."}), file=sys.stderr)
        time.sleep(wait)
        _request_count = 0
        _window_start = time.time()
    elif _request_count >= BACKOFF_THRESHOLD:
        # Slow down near the limit
        time.sleep(0.5)


def _request(method: str, url: str, token: str, params: dict = None,
             json_body: dict = None, data: str = None,
             content_type: str = None) -> dict:
    """Make an authenticated request to CourtListener API."""
    global _request_count
    _check_rate_limit()

    headers = {
        "Authorization": f"Token {token}",
    }
    if content_type:
        headers["Content-Type"] = content_type

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        elif method == "POST":
            if json_body is not None:
                resp = requests.post(url, headers=headers, json=json_body, timeout=60)
            elif data is not None:
                resp = requests.post(url, headers=headers, data=data,
                                     timeout=60, params=params)
            else:
                resp = requests.post(url, headers=headers, params=params, timeout=60)
        else:
            return {"error": f"Unsupported HTTP method: {method}"}

        _request_count += 1

        if resp.status_code == 429:
            # Rate limited — back off and retry once
            retry_after = int(resp.headers.get("Retry-After", 60))
            print(json.dumps({"warning": f"429 Too Many Requests. Retrying after {retry_after}s."}), file=sys.stderr)
            time.sleep(retry_after)
            _request_count += 1
            resp = requests.request(method, url, headers=headers,
                                    params=params, json=json_body,
                                    data=data, timeout=60)

        if resp.status_code != 200:
            return {
                "error": f"HTTP {resp.status_code}",
                "detail": resp.text[:500],
                "url": resp.url
            }

        return resp.json()

    except requests.exceptions.Timeout:
        return {"error": "Request timed out", "url": url}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"Connection error: {str(e)[:200]}", "url": url}
    except json.JSONDecodeError:
        return {"error": "Response is not valid JSON", "url": url, "body_preview": resp.text[:500]}


def _paginate(method: str, url: str, token: str, params: dict = None,
              max_pages: int = 50) -> dict:
    """Follow cursor-based pagination, merging all results."""
    all_results = []
    page = 0
    current_url = url
    current_params = params

    while current_url and page < max_pages:
        data = _request(method, current_url, token, params=current_params)
        if "error" in data:
            if all_results:
                # Return what we have so far plus the error
                return {"count": len(all_results), "results": all_results,
                        "pagination_error": data["error"], "pages_fetched": page}
            return data

        results = data.get("results", [])
        all_results.extend(results)
        page += 1

        next_url = data.get("next")
        if next_url:
            # Next URL is absolute and includes cursor params
            current_url = next_url
            current_params = None  # params are embedded in the next URL
        else:
            current_url = None

    return {
        "count": data.get("count", len(all_results)),
        "results": all_results,
        "pages_fetched": page,
        "truncated": current_url is not None
    }


# ─── Endpoint functions ───────────────────────────────────────────────

def search(token: str, params: dict, paginate: bool = False) -> dict:
    """Search opinions, dockets, people, etc."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/search/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def cluster(token: str, cluster_id: str) -> dict:
    """Get cluster metadata — sub-opinions, full citation arrays."""
    url = f"{BASE_URL}/clusters/{cluster_id}/?format=json"
    return _request("GET", url, token)


def opinion(token: str, opinion_id: str) -> dict:
    """Get full opinion text including html_with_citations."""
    url = f"{BASE_URL}/opinions/{opinion_id}/?format=json"
    return _request("GET", url, token)


def citation_lookup(token: str, text: str) -> dict:
    """POST text to resolve citations. Up to 64,000 chars, 250 citations."""
    url = f"{BASE_URL}/citation-lookup/?format=json"
    return _request("POST", url, token, json_body={"text": text})


def opinions_cited(token: str, params: dict, paginate: bool = False) -> dict:
    """Get opinions that cite a given opinion."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/opinions-cited/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def docket(token: str, docket_id: str) -> dict:
    """Get docket metadata — parties, judge, dates."""
    url = f"{BASE_URL}/dockets/{docket_id}/?format=json"
    return _request("GET", url, token)


def docket_entries(token: str, params: dict, paginate: bool = False) -> dict:
    """Get docket entries. Filter by docket ID and date_created__gt for incremental."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/docket-entries/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def recap_documents(token: str, params: dict, paginate: bool = False) -> dict:
    """Get RECAP documents for a docket entry."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/recap-documents/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def people(token: str, params: dict, paginate: bool = False) -> dict:
    """Search people (judges) by name or other attributes."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/people/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def positions(token: str, person_id: str, paginate: bool = False) -> dict:
    """Get judicial and non-judicial positions for a person."""
    url = f"{BASE_URL}/positions/"
    params = {"person": person_id, "format": "json"}
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def educations(token: str, person_id: str, paginate: bool = False) -> dict:
    """Get education history for a person."""
    url = f"{BASE_URL}/educations/"
    params = {"person": person_id, "format": "json"}
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def aba_ratings(token: str, person_id: str, paginate: bool = False) -> dict:
    """Get ABA qualification ratings for a person."""
    url = f"{BASE_URL}/aba-ratings/"
    params = {"person": person_id, "format": "json"}
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def political_affiliations(token: str, person_id: str, paginate: bool = False) -> dict:
    """Get political affiliations for a person."""
    url = f"{BASE_URL}/political-affiliations/"
    params = {"person": person_id, "format": "json"}
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def parties(token: str, params: dict, paginate: bool = False) -> dict:
    """Get parties for a docket."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/parties/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def attorneys(token: str, params: dict, paginate: bool = False) -> dict:
    """Get attorneys for a docket."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/attorneys/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def bankruptcy_info(token: str, params: dict, paginate: bool = False) -> dict:
    """Get bankruptcy information for a docket."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/bankruptcy-information/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


def originating_court(token: str, params: dict, paginate: bool = False) -> dict:
    """Get originating court information (removed/transferred cases)."""
    params.setdefault("format", "json")
    url = f"{BASE_URL}/originating-court-information/"
    if paginate:
        return _paginate("GET", url, token, params)
    return _request("GET", url, token, params=params)


# ─── CLI dispatcher ───────────────────────────────────────────────────

def _parse_kv_params(args: list) -> dict:
    """Parse --key value pairs into a dict."""
    params = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                params[key] = args[i + 1]
                i += 2
            else:
                params[key] = "true"
                i += 1
        else:
            i += 1
    return params


def main():
    parser = argparse.ArgumentParser(
        description="CourtListener API v4 wrapper",
        usage="python3 cl_api.py --config <path> <endpoint> [params...]"
    )
    parser.add_argument("--config", required=False, default=None,
                        help="Path to CL_CONFIG.txt containing API token")
    parser.add_argument("--paginate", action="store_true",
                        help="Follow cursor-based pagination (merge all pages)")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max pages to fetch when paginating (default: 50)")
    parser.add_argument("--text-file",
                        help="Path to text file for citation-lookup POST body")
    parser.add_argument("--output", "-o",
                        help="Write JSON output to file instead of stdout")
    parser.add_argument("endpoint", help="API endpoint name")

    # Use parse_known_args so endpoint-specific --key value pairs pass through
    args, remaining = parser.parse_known_args()
    token = _load_token(args.config)
    endpoint = args.endpoint.lower().replace("-", "_").replace(" ", "_")
    paginate = args.paginate

    # Separate positional ID args from --key value params in remaining
    positional = []
    kv_start = 0
    for i, a in enumerate(remaining):
        if a.startswith("--"):
            kv_start = i
            break
        positional.append(a)
    else:
        kv_start = len(remaining)

    params = _parse_kv_params(remaining[kv_start:])

    # Dispatch
    if endpoint == "search":
        result = search(token, params, paginate=paginate)

    elif endpoint == "cluster":
        if not positional:
            result = {"error": "cluster requires a cluster_id argument"}
        else:
            result = cluster(token, positional[0])

    elif endpoint == "opinion":
        if not positional:
            result = {"error": "opinion requires an opinion_id argument"}
        else:
            result = opinion(token, positional[0])

    elif endpoint == "citation_lookup":
        if args.text_file:
            with open(args.text_file, "r") as f:
                text = f.read()
        elif not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            result = {"error": "citation-lookup requires --text-file or stdin input"}
            text = None
        if text is not None:
            if len(text) > 64000:
                print(json.dumps({"warning": f"Text is {len(text)} chars; truncating to 64,000 for API limit."}), file=sys.stderr)
                text = text[:64000]
            result = citation_lookup(token, text)

    elif endpoint == "opinions_cited":
        result = opinions_cited(token, params, paginate=paginate)

    elif endpoint == "docket":
        if not positional:
            result = {"error": "docket requires a docket_id argument"}
        else:
            result = docket(token, positional[0])

    elif endpoint == "docket_entries":
        # Convenience: --since maps to date_created__gt
        if "since" in params:
            params["date_created__gt"] = params.pop("since")
        result = docket_entries(token, params, paginate=paginate)

    elif endpoint == "recap_documents":
        result = recap_documents(token, params, paginate=paginate)

    elif endpoint == "people":
        result = people(token, params, paginate=paginate)

    elif endpoint == "positions":
        if not positional:
            result = {"error": "positions requires a person_id argument"}
        else:
            result = positions(token, positional[0], paginate=paginate)

    elif endpoint == "educations":
        if not positional:
            result = {"error": "educations requires a person_id argument"}
        else:
            result = educations(token, positional[0], paginate=paginate)

    elif endpoint == "aba_ratings":
        if not positional:
            result = {"error": "aba-ratings requires a person_id argument"}
        else:
            result = aba_ratings(token, positional[0], paginate=paginate)

    elif endpoint == "political_affiliations":
        if not positional:
            result = {"error": "political-affiliations requires a person_id argument"}
        else:
            result = political_affiliations(token, positional[0], paginate=paginate)

    elif endpoint == "parties":
        result = parties(token, params, paginate=paginate)

    elif endpoint == "attorneys":
        result = attorneys(token, params, paginate=paginate)

    elif endpoint in ("bankruptcy_info", "bankruptcy_information"):
        result = bankruptcy_info(token, params, paginate=paginate)

    elif endpoint in ("originating_court", "originating_court_information"):
        result = originating_court(token, params, paginate=paginate)

    else:
        result = {
            "error": f"Unknown endpoint: {args.endpoint}",
            "available": [
                "search", "cluster", "opinion", "citation-lookup",
                "opinions-cited", "docket", "docket-entries",
                "recap-documents", "people", "positions", "educations",
                "aba-ratings", "political-affiliations", "parties",
                "attorneys", "bankruptcy-info", "originating-court"
            ]
        }

    # Output
    output_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(json.dumps({"status": "ok", "output_file": args.output,
                          "size_bytes": len(output_json)}))
    else:
        print(output_json)


if __name__ == "__main__":
    main()
