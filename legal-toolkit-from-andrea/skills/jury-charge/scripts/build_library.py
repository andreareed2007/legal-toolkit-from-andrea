#!/usr/bin/env python3
"""build_library.py - Build a pattern-instruction library for a new jurisdiction.

The jury-charge skill drafts ONLY from verbatim pattern text printed in-session
by pjc_lookup.py. This tool builds the library that lookup reads: per-section
verbatim text files + an index + a manifest (library.json). A library is
UNUSABLE (validated=false) until the spot-check gate passes and the attorney
approves it. The parser never invents text: it segments text extracted from an
official source file staged by the user. No source file, no library.

Subcommands:
  init <lib_dir> --jurisdiction ca --label CACI --name "..." --edition 2026
                 [--source-url URL] [--routing-style ca-vf]
  inventory <lib_dir>            scan _staging/ and report what is there
  parse <lib_dir> --format caci-pdf [--source FILE]
  index <lib_dir>                rebuild index from parsed section files
  spot-check <lib_dir> [--n 10]  print random parsed sections for human
                                 comparison against the official source
  matrix-draft <lib_dir>         scaffold claim_matrix.json (ALL entries flagged
                                 ATTORNEY REVIEW - the machine maps series
                                 topics, never legal judgment)
  approve <lib_dir> --confirm ATTORNEY-APPROVED
                                 set validated=true after spot-check sign-off
"""
import argparse, bisect, hashlib, json, random, re, subprocess, sys, datetime
from pathlib import Path

MANIFEST = "library.json"

def now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def load_manifest(lib: Path) -> dict:
    p = lib / MANIFEST
    if not p.exists():
        sys.exit(f"FATAL: {p} not found. Run init first.")
    return json.loads(p.read_text(encoding="utf-8"))

def save_manifest(lib: Path, m: dict, event: str):
    m["updated"] = now()
    m.setdefault("history", []).append({"at": now(), "event": event})
    (lib / MANIFEST).write_text(json.dumps(m, indent=1), encoding="utf-8")

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

# ------------------------------------------------------------------ init
def cmd_init(a):
    lib = Path(a.lib_dir); (lib / "_staging").mkdir(parents=True, exist_ok=True)
    (lib / "sections").mkdir(exist_ok=True)
    if (lib / MANIFEST).exists() and not a.force:
        sys.exit(f"FATAL: {lib / MANIFEST} exists. Use --force to reinitialize.")
    m = {"jurisdiction": a.jurisdiction, "label": a.label, "name": a.name,
         "edition": a.edition, "source_url": a.source_url or "",
         "routing_style": a.routing_style, "index_file": "index.json",
         "matrix_file": "claim_matrix.json", "sections_dir": "sections",
         "validated": False,
         "coverage_note": f"Library '{a.label} {a.edition}' is not yet parsed/validated.",
         "source_files": [], "created": now(), "history": []}
    save_manifest(lib, m, "init")
    (lib / "_staging" / "README.txt").write_text(
        f"Stage the OFFICIAL {a.label} source file(s) in this folder.\n"
        f"Official source: {a.source_url or '(record the URL in library.json)'}\n"
        f"Then run: build_library.py parse ...\n", encoding="utf-8")
    print(f"initialized {lib} (validated=false). Stage official source in {lib/'_staging'}.")

# ------------------------------------------------------------------ inventory
def cmd_inventory(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    st = lib / "_staging"
    files = [p for p in sorted(st.glob("*")) if p.is_file() and p.name != "README.txt"]
    if not files:
        print(f"_staging is EMPTY. Download the official source ({m.get('source_url','?')}) "
              f"and place it in {st}. STOP - the library cannot be built without it.")
        sys.exit(2)
    for p in files:
        print(f"{p.name}  {p.stat().st_size:,} bytes  sha256={sha256(p)[:16]}...")
    print(f"TOTALS: staged files={len(files)}")

# ------------------------------------------------------------------ parse
# Heading lines: TOC entries are flush-left; instruction-body headings are
# CENTERED (indented). Title must start with a capital/bracket to reject text
# wraps like "1946.2 provides..." inside Sources and Authority.
HEAD_CACI = re.compile(r"^\s{0,60}(?P<id>VF-\d{3,4}[A-Z]?|\d{3,4}[A-Z]?)\.\s+(?P<title>[A-Z\[“\"].{0,150})$")

def cmd_parse(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    if a.format != "caci-pdf":
        sys.exit(f"FATAL: unknown --format '{a.format}'. Supported: caci-pdf. "
                 "Adding a jurisdiction means adding a parser derived from the ACTUAL "
                 "official source structure - never from an assumed format.")
    st = lib / "_staging"
    src = Path(a.source) if a.source else next(iter(sorted(st.glob("*.pdf"))), None)
    if src is None or not src.exists():
        sys.exit(f"FATAL: no source PDF staged in {st}. STOP - stage the official file first.")
    txt = lib / "_staging" / (src.stem + ".pdftotext.txt")
    if not txt.exists():
        print(f"extracting text: pdftotext -layout {src.name} ...")
        subprocess.run(["pdftotext", "-layout", str(src), str(txt)], check=True)
    raw = txt.read_text(encoding="utf-8", errors="replace")
    pages = raw.split("\f")
    # locate instruction headings with their page numbers
    hits = []  # (page_no, line_no, id, title)
    for pno, page in enumerate(pages, 1):
        for lno, line in enumerate(page.splitlines()):
            h = HEAD_CACI.match(line.rstrip())
            if h:
                hits.append((pno, lno, h.group("id"), h.group("title").strip()))
    sec_dir = lib / m.get("sections_dir", "sections"); sec_dir.mkdir(exist_ok=True)
    flat = []
    for pno, page in enumerate(pages, 1):
        for lno, line in enumerate(page.splitlines()):
            flat.append((pno, lno, line))
    # map (page,line) -> flat position
    pos_of = {(p, l): i for i, (p, l, _) in enumerate(flat)}
    # ---- front/back-matter barriers: never treat their contents as instructions,
    # and never let an instruction body run into them
    BACK_RE = re.compile(r"^(TABLE OF CASES|TABLE OF STATUTES|INDEX)\b")
    PAGEBAR_RE = re.compile(r"^(USER GUIDE|Life Expectancy Table)")
    page_start, first_line, i = {}, {}, 0
    for pno, page in enumerate(pages, 1):
        page_start[pno] = i
        first_line[pno] = next((l.strip() for l in page.splitlines() if l.strip()), "")
        i += len(page.splitlines())
    back_start = min((page_start[pn] for pn in page_start if BACK_RE.match(first_line[pn])),
                     default=len(flat))
    bar_pages = {pn for pn in page_start
                 if PAGEBAR_RE.match(first_line[pn]) or BACK_RE.match(first_line[pn])}
    barrier_pos = sorted({page_start[pn] for pn in bar_pages} | {back_start})
    n_raw = len(hits)
    hits = [h for h in hits
            if pos_of[(h[0], h[1])] < back_start and h[0] not in bar_pages]
    hit_pos = sorted(pos_of[(h[0], h[1])] for h in hits)
    boundaries = sorted(set(hit_pos) | set(barrier_pos))

    def span(i):
        """Lines until the next heading hit of ANY id. TOC mentions span 1-2
        lines (the next TOC entry follows immediately); a real instruction BODY
        spans dozens. Per id, the max-span occurrence is the body."""
        pos = pos_of[(hits[i][0], hits[i][1])]
        k = bisect.bisect_right(boundaries, pos)
        return (boundaries[k] if k < len(boundaries) else len(flat)) - pos

    best = {}
    for i, (pno, lno, sid, title) in enumerate(hits):
        if sid not in best or span(i) > span(best[sid]):
            best[sid] = i
    keep = sorted(best.values(), key=lambda i: pos_of[(hits[i][0], hits[i][1])])
    written, anomalies, written_files = 0, [], []
    for j, ki in enumerate(keep):
        pno, lno, sid, title = hits[ki]
        start = pos_of[(pno, lno)]
        # body ends at the next heading hit of ANY occurrence (kept or not) OR at
        # the next front/back-matter barrier, whichever comes first
        k = bisect.bisect_right(boundaries, start)
        end = boundaries[k] if k < len(boundaries) else len(flat)
        body = "\n".join(x[2] for x in flat[start:end]).rstrip()
        if len(body) < 200:
            anomalies.append(f"{sid}: suspiciously short ({len(body)} chars)")
        safe = sid.replace("/", "_")
        written_files.append(f"{m['label']}_{safe}.md")
        (sec_dir / f"{m['label']}_{safe}.md").write_text(
            f"# {m['label']} {sid} - {title}\n"
            f"<!-- source: {src.name} page {pno}; edition {m.get('edition')}; "
            f"extracted {now()}; VERBATIM pdftotext output below - do not edit -->\n\n"
            + body + "\n", encoding="utf-8")
        written += 1
    m["source_files"] = [{"name": src.name, "sha256": sha256(src)}]
    m["validated"] = False
    m["coverage_note"] = (f"{m['label']} {m.get('edition')} parsed from {src.name} "
                          f"({written} sections) - NOT YET VALIDATED. Run spot-check, "
                          "then approve. Lookup refuses unvalidated libraries.")
    save_manifest(lib, m, f"parse: {written} sections from {src.name}")
    report = {"at": now(), "source": src.name, "sections_written": written,
              "heading_hits_raw": n_raw, "heading_hits_kept": len(hits),
              "back_matter_page": next((pn for pn in sorted(page_start) if page_start[pn] == back_start), None),
              "files_written": sorted(f.name for f in sec_dir.glob("*.md")) and None,
              "written_files": written_files, "anomalies": anomalies}
    (lib / "parse_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"TOTALS: sections written={written} anomalies={len(anomalies)}")
    for x in anomalies[:20]:
        print(f"  ANOMALY: {x}")
    print("Library remains validated=false. Next: build_library.py index, then spot-check.")

# ------------------------------------------------------------------ index
def cmd_index(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    sec_dir = lib / m.get("sections_dir", "sections")
    entries = []
    for p in sorted(sec_dir.glob("*.md")):
        first = p.read_text(encoding="utf-8").splitlines()
        head = first[0] if first else ""
        hm = re.match(rf"#\s+{re.escape(m['label'])}\s+(\S+)\s+-\s+(.*)", head)
        if not hm:
            print(f"SKIP (unparsable heading): {p.name}"); continue
        sid, title = hm.group(1), hm.group(2)
        entries.append({"section_id": sid, "title": title,
                        "series": (re.match(r"(?:VF-)?(\d+)", sid).group(1)[: -2] + "00") if re.match(r"(?:VF-)?\d{3,4}", sid) else None,
                        "type": "verdict_form" if sid.startswith("VF-") else "instruction",
                        "edition": m.get("edition"), "text_file": f"{m.get('sections_dir','sections')}/{p.name}",
                        "text_lines": len(first)})
    (lib / m.get("index_file", "index.json")).write_text(json.dumps(entries, indent=1), encoding="utf-8")
    save_manifest(lib, m, f"index: {len(entries)} entries")
    print(f"TOTALS: indexed={len(entries)} -> {lib / m.get('index_file','index.json')}")

# ------------------------------------------------------------------ spot-check
def cmd_spot_check(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    idx = json.loads((lib / m.get("index_file", "index.json")).read_text(encoding="utf-8"))
    picks = random.sample(idx, min(a.n, len(idx)))
    print("SPOT-CHECK: compare each section below, word for word, against the official "
          "source document (the page number is in the file's source comment). The library "
          "may not be approved until every sampled section matches verbatim.\n")
    for e in picks:
        print("#" * 78)
        print((lib / e["text_file"]).read_text(encoding="utf-8")[:2500])
    print(f"\nTOTALS: sampled={len(picks)} of {len(idx)}")

# ------------------------------------------------------------------ matrix-draft
def cmd_matrix_draft(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    idx = json.loads((lib / m.get("index_file", "index.json")).read_text(encoding="utf-8"))
    by_series = {}
    for e in idx:
        if e["type"] == "instruction" and e.get("series"):
            by_series.setdefault(e["series"], []).append(e)
    matrix = []
    for series, items in sorted(by_series.items(), key=lambda kv: int(kv[0])):
        head = items[0]
        matrix.append({
            "claim_id": f"SERIES_{series}_DRAFT",
            "label": f"[DRAFT - series {series}] {head['title'][:60]}",
            "elements": {"instructions": [{"section_id": e["section_id"], "title": e["title"]}
                                          for e in items[:40]]},
            "notes": "ATTORNEY JUDGMENT: machine first-pass scaffold from series grouping only. "
                     "An attorney must select the correct instructions, variants, defenses, and "
                     "damages routing before this claim may be used in a charge."})
    (lib / m.get("matrix_file", "claim_matrix.json")).write_text(json.dumps(matrix, indent=1), encoding="utf-8")
    save_manifest(lib, m, f"matrix-draft: {len(matrix)} draft claims")
    print(f"TOTALS: draft claims={len(matrix)} - EVERY entry is flagged ATTORNEY JUDGMENT.")

# ------------------------------------------------------------------ approve
def cmd_approve(a):
    lib = Path(a.lib_dir); m = load_manifest(lib)
    if a.confirm != "ATTORNEY-APPROVED":
        sys.exit("FATAL: approval requires --confirm ATTORNEY-APPROVED, given only after "
                 "the attorney has personally reviewed the spot-check output.")
    m["validated"] = True
    m["coverage_note"] = (f"{m['label']} {m.get('edition')} - validated "
                          f"{now()[:10]} after attorney spot-check.")
    save_manifest(lib, m, "approved by attorney after spot-check")
    print(f"library validated=true: {lib}")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("lib_dir")
    p.add_argument("--jurisdiction", required=True); p.add_argument("--label", required=True)
    p.add_argument("--name", required=True); p.add_argument("--edition", required=True)
    p.add_argument("--source-url", default=""); p.add_argument("--routing-style", default="tx")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("inventory"); p.add_argument("lib_dir"); p.set_defaults(fn=cmd_inventory)
    p = sub.add_parser("parse"); p.add_argument("lib_dir")
    p.add_argument("--format", required=True); p.add_argument("--source")
    p.set_defaults(fn=cmd_parse)
    p = sub.add_parser("index"); p.add_argument("lib_dir"); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("spot-check"); p.add_argument("lib_dir")
    p.add_argument("--n", type=int, default=10); p.set_defaults(fn=cmd_spot_check)
    p = sub.add_parser("matrix-draft"); p.add_argument("lib_dir"); p.set_defaults(fn=cmd_matrix_draft)
    p = sub.add_parser("approve"); p.add_argument("lib_dir")
    p.add_argument("--confirm", default=""); p.set_defaults(fn=cmd_approve)
    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
