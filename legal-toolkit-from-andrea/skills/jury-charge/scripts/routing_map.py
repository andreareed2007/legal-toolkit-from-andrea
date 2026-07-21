#!/usr/bin/env python3
"""routing_map.py - Conditional routing engine for the jury-charge skill.

The routing map is data; routing prose is generated; generated prose is
machine-checked against the map. See JURY_CHARGE_CONDITIONAL_ROUTING.md for the
schema and semantics.

Subcommands:
  validate  <map.json>                      Run all structural checks.
  render    <map.json> -o <skeleton.md> [--numbering <numbering_map.json>]
  audit     <map.json> -o <audit.html> [--charge <charge.txt>]
  crosscheck <map.json> --numbering <numbering_map.json> --charge <charge.txt>

Exit codes: 0 clean, 1 warnings only (validate), 2 errors / mismatches.
"""
import argparse, html, json, re, sys
from pathlib import Path

NODE_TYPES = {"liability", "defense", "damages", "exemplary_predicate",
              "exemplary", "fees", "instruction_only"}
ANSWERABLE = NODE_TYPES - {"instruction_only"}
YESNO = {"liability", "defense", "exemplary_predicate"}  # damages/fees/exemplary are amount answers

def load_map(path):
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    return m

def node_index(m):
    return {n["key"]: n for n in m.get("nodes", [])}

# ---------------------------------------------------------------- validation
def validate(m):
    errors, warnings = [], []
    nodes = m.get("nodes", [])
    idx = node_index(m)
    if len(idx) != len(nodes):
        seen, dups = set(), set()
        for n in nodes:
            (dups if n["key"] in seen else seen).add(n["key"])
        errors.append(f"duplicate node keys: {sorted(dups)}")
    part_keys = {p["key"] for p in m.get("parts", [])}

    for n in nodes:
        k = n["key"]
        if n.get("type") not in NODE_TYPES:
            errors.append(f"{k}: unknown type '{n.get('type')}'")
            continue
        if n.get("part") not in part_keys:
            errors.append(f"{k}: part '{n.get('part')}' not declared in parts[]")
        if not src_sections(n) and not n.get("statute") and n["type"] != "instruction_only":
            warnings.append(f"{k}: no pattern section and no statute recorded - every question needs a source")
        # references
        for p in n.get("predicated_on", []):
            src = idx.get(p.get("source"))
            if src is None:
                errors.append(f"{k}: predicated_on unknown node '{p.get('source')}'")  # check 1
                continue
            if src["type"] not in YESNO:
                errors.append(f"{k}: predicated on '{p['source']}' ({src['type']}) which has no Yes/No answer")  # check 3
            if p.get("scope") in ("any_party", "same_party") and not src.get("per_party_answers"):
                errors.append(f"{k}: scope '{p['scope']}' but source '{p['source']}' is not per-party")  # check 9
            if p.get("answer") not in ("Yes", "No"):
                errors.append(f"{k}: predicate answer must be 'Yes' or 'No', got {p.get('answer')!r}")
        for alt in n.get("alternative_to", []):
            t = idx.get(alt)
            if t is None:
                errors.append(f"{k}: alternative_to unknown node '{alt}'")
            elif t["type"] != "liability":
                errors.append(f"{k}: alternative_to '{alt}' is {t['type']}, must be liability")  # check 8
        for b in n.get("blocks", []):
            t = idx.get(b)
            if t is None:
                errors.append(f"{k}: blocks unknown node '{b}'")
            elif t["type"] != "damages":
                errors.append(f"{k}: blocks '{b}' is {t['type']}, must be damages")  # check 7
            if n["type"] != "defense":
                errors.append(f"{k}: only defense nodes may carry blocks edges")

    # check 2: cycles over predication + alternative edges
    graph = {n["key"]: [p["source"] for p in n.get("predicated_on", [])] + list(n.get("alternative_to", []))
             for n in nodes}
    state = {}
    def dfs(k, stack):
        state[k] = 1
        for s in graph.get(k, []):
            if s not in graph:
                continue
            if state.get(s) == 1:
                errors.append(f"cycle detected: {' -> '.join(stack + [k, s])}")
                continue
            if state.get(s, 0) == 0:
                dfs(s, stack + [k])
        state[k] = 2
    for k in graph:
        if state.get(k, 0) == 0:
            dfs(k, [])

    # chain-based checks
    def chain_types(k, seen=None):
        seen = seen or set()
        if k in seen:
            return set()
        seen.add(k)
        n = idx.get(k)
        if n is None:
            return set()
        out = {n["type"]}
        for p in n.get("predicated_on", []):
            out |= chain_types(p["source"], seen)
        for a in n.get("alternative_to", []):
            out |= chain_types(a, seen)
        return out

    blocked_targets = {b for n in nodes for b in n.get("blocks", [])}
    damages_sources = set()
    for n in nodes:
        if n["type"] == "damages":
            for p in n.get("predicated_on", []):
                damages_sources.add(p["source"])
            ct = chain_types(n["key"]) - {n["type"]}
            if "liability" not in ct:
                errors.append(f"{n['key']}: damages node with no liability in its predicate chain")  # check 4
        if n["type"] == "exemplary":
            preds = n.get("predicated_on", [])
            ok = any(idx.get(p["source"], {}).get("type") == "exemplary_predicate" and p.get("unanimous")
                     for p in preds)
            if not ok:
                errors.append(f"{n['key']}: exemplary must be predicated (unanimous) on an exemplary_predicate node")  # check 6
        if n["type"] == "exemplary_predicate":
            if not any(idx.get(p["source"], {}).get("type") == "liability" for p in n.get("predicated_on", [])):
                errors.append(f"{n['key']}: exemplary_predicate must be predicated on a liability node")  # check 6
        if n["type"] == "defense":
            predicates_something = any(n["key"] in [p["source"] for p in x.get("predicated_on", [])]
                                       for x in nodes)
            if not n.get("blocks") and not predicates_something and \
                    "justified:" not in n.get("notes", "").lower():
                warnings.append(f"{n['key']}: defense finding blocks nothing and predicates nothing (check 10)")
    for n in nodes:
        if n["type"] == "liability":
            reached = n["key"] in damages_sources or any(
                n["key"] in x.get("alternative_to", []) for x in nodes)
            feeds_dmg = any(n["key"] == p["source"]
                            for x in nodes if x["type"] in ("damages", "exemplary_predicate", "fees")
                            for p in x.get("predicated_on", []))
            if not (reached or feeds_dmg) and "justified:" not in n.get("notes", "").lower():
                warnings.append(f"{n['key']}: liability node no damages/fees node depends on (check 5) - justify in notes")

    # check 11: unreachable - predicated on "No" of a node that is itself predicated (skip-chains)
    for n in nodes:
        for p in n.get("predicated_on", []):
            src = idx.get(p.get("source"))
            if src and p.get("answer") == "No" and src.get("predicated_on") and not n.get("alternative_to"):
                warnings.append(f"{n['key']}: predicated on 'No' of conditionally-asked '{p['source']}' - "
                                "if the source is skipped it is never 'No'; confirm intent (check 11)")
    return errors, warnings

# ---------------------------------------------------------------- numbering
def assign_numbers(m):
    """Stable topological order within declared node order; returns {key: qnum}."""
    nodes = [n for n in m["nodes"] if n["type"] != "instruction_only"]
    order = {n["key"]: i for i, n in enumerate(m["nodes"])}
    idx = node_index(m)
    deps = {n["key"]: {p["source"] for p in n.get("predicated_on", [])} |
                      set(n.get("alternative_to", [])) |
                      ({b for x in m["nodes"] if n["key"] in x.get("blocks", []) for b in [x["key"]]})
            for n in nodes}
    # a blocked damages node must come after its blocking defense:
    for d in m["nodes"]:
        for b in d.get("blocks", []):
            if b in deps:
                deps[b].add(d["key"])
    numbered, num, remaining = {}, 1, {n["key"] for n in nodes}
    while remaining:
        ready = sorted([k for k in remaining
                        if all(s not in remaining for s in deps[k] if s in idx)],
                       key=lambda k: order[k])
        if not ready:
            raise SystemExit("FATAL: could not order nodes (cycle?) - run validate first")
        k = ready[0]
        numbered[k] = num
        num += 1
        remaining.discard(k)
    return numbered

# ---------------------------------------------------------------- rendering
def q(s):  # smart double quotes, house style
    return f"“{s}”"

def src_sections(n):
    """Pattern-source sections for a node. 'pjc' is the historical field name;
    'sources' is the jurisdiction-neutral alias (v3.0). Either works."""
    return n.get("pjc") or n.get("sources") or []

def src_label(m):
    return m.get("source_label", "PJC")

ROUTING_STYLES = ("tx", "ca-vf")

def routing_style(m):
    st = m.get("routing_style", "tx")
    if st not in ROUTING_STYLES:
        sys.exit(f"FATAL: unknown routing_style '{st}'. Supported: {ROUTING_STYLES}")
    return st

def routing_sentences(n, numbered, idx, style="tx"):
    """Generate the conditional routing instruction lines preceding node n.
    style 'tx' is the proven Texas phrasing. style 'ca-vf' is a FIRST-CUT
    California verdict-form phrasing (draft; verify against the matter's actual
    CACI verdict forms before filing - flagged in the audit)."""
    lines = []
    me = numbered[n["key"]]
    conds = []
    def pred(num, answer, u="", scope_tail=""):
        if style == "ca-vf":
            un = "unanimous " if u else ""
            return f'your {un}answer to question {num} is {answer.lower()}{scope_tail}'
        return f'you {u}answered {q(answer)} to Question No. {num}{scope_tail}'
    for p in n.get("predicated_on", []):
        src_num = numbered.get(p["source"])
        u = "unanimously " if p.get("unanimous") else ""
        scope = p.get("scope", "all")
        if scope == "any_party":
            conds.append(pred(src_num, p["answer"], u, " regarding any party"))
        elif scope == "same_party":
            conds.append(pred(src_num, p["answer"], u, " regarding a party"))
        else:
            conds.append(pred(src_num, p["answer"], u))
    for d in (x for x in idx.values() if n["key"] in x.get("blocks", [])):
        conds.append(pred(numbered[d["key"]], "No"))
    joiner = " and " if (n.get("logic", "OR") == "AND" or len([1 for x in idx.values() if n["key"] in x.get("blocks", [])])) else " or "
    for alt in n.get("alternative_to", []):
        conds.append(pred(numbered[alt], "No") + (" or you did not answer it" if style == "ca-vf" else " or did not answer it"))
    if not conds:
        return lines
    per_party_tail = ""
    if any(p.get("scope") == "same_party" for p in n.get("predicated_on", [])):
        per_party_tail = " regarding that party"
    if style == "ca-vf":
        lines.append(f"If {joiner.join(conds)}, then answer question {me}{per_party_tail}. "
                     f"If not, do not answer question {me}.")
    else:
        lines.append(f"If {joiner.join(conds)}, then answer Question No. {me}{per_party_tail}. "
                     f"Otherwise, do not answer Question No. {me}.")
    return lines

def render(m, out_path, numbering_path=None):
    idx = node_index(m)
    numbered = assign_numbers(m)
    parts = {p["key"]: p["title"] for p in m.get("parts", [])}
    label = src_label(m)
    style = routing_style(m)
    lines = [f"# Charge Skeleton - {m.get('matter','')}",
             f"Posture: {m.get('posture','requested')} | Court type: {m.get('court_type','')} | "
             f"Source: {label} | Routing style: {style}",
             "", "NOTE: This is the routing skeleton. Question and instruction text MUST be",
             f"populated from verbatim {label} pattern text read via pjc_lookup.py - never from memory.", ""]
    cur_part = None
    ordered = sorted((n for n in m["nodes"] if n["type"] != "instruction_only"),
                     key=lambda n: numbered[n["key"]])
    for n in ordered:
        if n["part"] != cur_part:
            cur_part = n["part"]
            lines += ["", f"## PART {cur_part}: {parts.get(cur_part,'')}", ""]
        for r in routing_sentences(n, numbered, idx, style):
            lines += [f"> ROUTING: {r}", ""]
        src = ", ".join(f"{label} {s}" for s in src_sections(n)) or \
              (f"{n.get('statute')} (no {label} pattern exists; instruction tracks statutory elements)"
               if n.get("statute") else "!! NO SOURCE RECORDED")
        lines.append(f"### QUESTION NO. {numbered[n['key']]} - {n['label']} [{n['type']}]")
        lines.append(f"Parties: {n.get('asserting','?')} -> {', '.join(n.get('against', []))}"
                     + (" (answer per party)" if n.get("per_party_answers") else ""))
        lines.append(f"Source: {src}")
        if n.get("notes"):
            lines.append(f"Notes: {n['notes']}")
        lines += [f"[POPULATE FROM VERBATIM {label} TEXT]",
                  "GRANTED: ________ REFUSED: ________" if m.get("posture", "requested") == "requested" else "",
                  ""]
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    if numbering_path:
        Path(numbering_path).write_text(json.dumps(
            {"matter": m.get("matter"), "numbers": numbered}, indent=1), encoding="utf-8")
    print(f"rendered {len(ordered)} questions -> {out_path}")
    if numbering_path:
        print(f"numbering map -> {numbering_path}")

# ---------------------------------------------------------------- crosscheck
# body is tempered: it may not cross a question heading or a GRANTED line, and
# is length-bounded, so one instruction cannot glom onto text from another.
# ALL-CAPS 'QUESTION NO.' marks a heading; title-case 'Question No.' is an
# inline reference. The (?-i:...) scopes keep that distinction under IGNORECASE.
_SEG = r'(?:(?!(?-i:QUESTION\s+NO\.)|(?-i:GRANTED:))[\s\S])'
ROUTE_RE = re.compile(
    r'If\s+(?:your?\s+)?(?P<body>' + _SEG + r'{0,260}?(?:answered|answer\s+to\s+question)' + _SEG + r'{0,240}?)'
    r',\s*then\s+answer\s+'
    r'(?:Question\s+No\.?\s*(?P<target>\d+)|question\s+(?P<target2>\d+)|the\s+following\s+(?P<following>questions?))',
    re.IGNORECASE)
PRED_RE = re.compile(r'(?:(?P<u>unanimously)\s+)?answered\s+[“"‘\']?(?P<ans>Yes|No)[”"’\']?\s+'
                     r'to\s+(?:both\s+)?Question\s+No\.?\s*(?P<src>\d+)'
                     r'|(?:(?P<u2>unanimous)\s+)?answer\s+to\s+question\s+(?:No\.?\s*)?(?P<src2>\d+)\s+'
                     r'is\s+[“"‘\']?(?P<ans2>Yes|No)[”"’\']?', re.IGNORECASE)
HEAD_RE = re.compile(r'QUESTION\s+NO\.\s*\d+')  # case-sensitive: headings only

def crosscheck(m, numbering_path, charge_path):
    numbered = json.loads(Path(numbering_path).read_text(encoding="utf-8"))["numbers"]
    idx = node_index(m)
    by_num = {v: k for k, v in numbered.items()}
    text = Path(charge_path).read_text(encoding="utf-8", errors="replace")
    text = re.sub(r'\s+', ' ', text)  # heals predicate clauses split by PDF line breaks
    # question heading positions; sequential index = true question number
    # (PDF extraction concatenates superscript footnote digits onto heading
    # numbers, so the printed number is untrustworthy - the sequence is not)
    heads = [(m.start(), i + 1) for i, m in enumerate(HEAD_RE.finditer(text))]

    def next_question_after(pos):
        for hpos, num in heads:
            if hpos > pos:
                return num
        return None

    # expected predicate sets per target number
    expected = {}
    for n in m["nodes"]:
        if n["type"] == "instruction_only" or n["key"] not in numbered:
            continue
        preds = set()
        for p in n.get("predicated_on", []):
            preds.add((numbered.get(p["source"]), p["answer"], bool(p.get("unanimous"))))
        for d in (x for x in idx.values() if n["key"] in x.get("blocks", [])):
            preds.add((numbered[d["key"]], "No", False))
        for alt in n.get("alternative_to", []):
            preds.add((numbered[alt], "No", False))
        if preds:
            expected[numbered[n["key"]]] = preds

    checked = matched = mismatched = 0
    problems = []
    seen_targets = set()
    for match in ROUTE_RE.finditer(text):
        checked += 1
        tgt = match.group("target") or match.group("target2")
        found = {(int(p.group("src") or p.group("src2")),
                  (p.group("ans") or p.group("ans2")).title(),
                  bool(p.group("u") or p.group("u2")))
                 for p in PRED_RE.finditer(match.group("body"))}
        if tgt is None:
            if (match.group("following") or "").lower().endswith("s"):
                mismatched += 1
                problems.append("GROUP routing (unverifiable per-question): "
                                f"'{re.sub(chr(10), ' ', match.group(0))[:140]}' - "
                                "cannot be machine-checked; flag for attorney review")
                continue
            # "the following question" - resolve positionally to the next heading
            tgt = next_question_after(match.end())
            if tgt is None:
                problems.append(f"could not resolve 'the following question' after predicates {sorted(found)}")
                mismatched += 1
                continue
        tgt = int(tgt)
        seen_targets.add(tgt)
        exp = expected.get(tgt)
        if exp is None:
            mismatched += 1
            problems.append(f"charge routes into Question No. {tgt} "
                            f"({by_num.get(tgt, '?')}) but the map has no predicates for it")
        elif not found:
            mismatched += 1
            problems.append(f"routing to Q{tgt}: no parsable predicate clause")
        elif found <= exp:
            matched += 1
        else:
            mismatched += 1
            problems.append(f"routing to Q{tgt} ({by_num.get(tgt,'?')}): charge says {sorted(found)}, "
                            f"map expects a subset of {sorted(exp)}")
    missing_routes = sorted(set(expected) - seen_targets)
    for t in missing_routes:
        problems.append(f"map requires a routing instruction into Q{t} ({by_num.get(t,'?')}) - none found in charge")
    for p in problems:
        print(f"MISMATCH: {p}")
    print(f"TOTALS: routing instructions checked={checked} matched={matched} "
          f"mismatched={mismatched} missing={len(missing_routes)}")
    return (2 if (mismatched or missing_routes) else 0), problems

# ---------------------------------------------------------------- audit html
def audit(m, out_path, charge_path=None):
    errors, warnings = validate(m)
    idx = node_index(m)
    try:
        numbered = assign_numbers(m)
    except SystemExit:
        numbered = {}
    parts = {p["key"]: p["title"] for p in m.get("parts", [])}
    e = html.escape
    rows = []
    for n in sorted((x for x in m["nodes"] if x["type"] != "instruction_only"),
                    key=lambda x: numbered.get(x["key"], 10**6)):
        preds = "<br>".join(
            f'Q{numbered.get(p["source"],"?")} = {e(p["answer"])}'
            f'{" (unanimous)" if p.get("unanimous") else ""} [{e(p.get("scope","all"))}]'
            for p in n.get("predicated_on", [])) or "&mdash; entry point"
        alts = ", ".join(f'Q{numbered.get(a,"?")}' for a in n.get("alternative_to", []))
        blockers = ", ".join(f'Q{numbered.get(d["key"],"?")}' for d in idx.values()
                             if n["key"] in d.get("blocks", []))
        src = ", ".join(f"{src_label(m)} {s}" for s in src_sections(n)) or e(n.get("statute") or "!! none")
        rows.append(f"<tr><td>Q{numbered.get(n['key'],'?')}</td><td>{e(n['key'])}</td>"
                    f"<td class='t-{e(n['type'])}'>{e(n['type'])}</td><td>{e(n['label'])}</td>"
                    f"<td>{e(n.get('asserting','?'))} v. {e(', '.join(n.get('against',[])) or '?')}"
                    f"{' (per party)' if n.get('per_party_answers') else ''}</td>"
                    f"<td>{src}</td><td>{preds}</td>"
                    f"<td>{('alt to ' + alts) if alts else ''} {('blocked by ' + blockers) if blockers else ''}</td>"
                    f"<td>{e(n.get('notes',''))}</td></tr>")
    issues = "".join(f"<li class='err'>ERROR: {e(x)}</li>" for x in errors) + \
             "".join(f"<li class='warn'>WARNING: {e(x)}</li>" for x in warnings) or \
             "<li class='ok'>No validation issues.</li>"
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Routing Audit - {e(m.get('matter',''))}</title><style>
body{{font-family:Georgia,serif;margin:2em;color:#1a1a1a}}
h1{{border-bottom:3px solid #7c2529}} table{{border-collapse:collapse;width:100%;font-size:.9em}}
td,th{{border:1px solid #bbb;padding:.4em .6em;vertical-align:top;text-align:left}}
th{{background:#7c2529;color:#fff}} tr:nth-child(even){{background:#f6f2f2}}
.err{{color:#a40000;font-weight:bold}} .warn{{color:#9a6700}} .ok{{color:#116329}}
.t-liability{{background:#dbe9f6}}.t-damages{{background:#e2f0d9}}.t-defense{{background:#fde9d9}}
.t-exemplary,.t-exemplary_predicate{{background:#ead1dc}}.t-fees{{background:#fff2cc}}
</style></head><body>
<h1>Conditional Routing Audit</h1>
<p><b>Matter:</b> {e(m.get('matter',''))} &nbsp; <b>Posture:</b> {e(m.get('posture',''))} &nbsp;
<b>Court type:</b> {e(m.get('court_type',''))} &nbsp; <b>Source:</b> {e(src_label(m))} &nbsp;
<b>Routing style:</b> {e(routing_style(m))}{' <b style="color:#a40000">(ca-vf is first-cut - verify phrasing against official verdict forms)</b>' if routing_style(m) == 'ca-vf' else ''} &nbsp; <b>Questions:</b> {len(numbered)}</p>
<h2>Validation ({len(errors)} errors, {len(warnings)} warnings)</h2><ul>{issues}</ul>
<h2>Question-by-question routing table</h2>
<table><tr><th>Q#</th><th>Key</th><th>Type</th><th>Label</th><th>Claim direction</th>
<th>Source</th><th>Answered only if</th><th>Alt / blocked</th><th>Notes</th></tr>
{''.join(rows)}</table>
<h2>Parts</h2><ul>{''.join(f'<li><b>{e(k)}</b>: {e(v)}</li>' for k, v in parts.items())}</ul>
<p>Generated by routing_map.py (jury-charge skill). The map is data; prose is generated;
prose is machine-checked. Do not hand-edit routing instructions in the charge.</p>
</body></html>"""
    Path(out_path).write_text(doc, encoding="utf-8")
    print(f"audit -> {out_path} ({len(errors)} errors, {len(warnings)} warnings)")
    return 2 if errors else (1 if warnings else 0)

# ------------------------------------------------------------- findings
def emit_findings(path, records):
    """Append audit results to the Phase 6 findings ledger (deduped there)."""
    import subprocess, tempfile, os
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(records, tmp); tmp.close()
    script = Path(__file__).parent / "findings.py"
    r = subprocess.run([sys.executable, str(script), "import", path, tmp.name],
                       capture_output=True, text=True)
    os.unlink(tmp.name)
    print(r.stdout.strip() or r.stderr.strip())

def validate_findings(errors, warnings):
    recs = []
    for x in errors:
        recs.append({"source": "routing-validate", "question": x.split(":")[0],
                     "category": "ROUTING", "severity": "critical", "finding": x})
    for x in warnings:
        recs.append({"source": "routing-validate", "question": x.split(":")[0],
                     "category": "ROUTING", "severity": "review", "finding": x})
    return recs

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("validate", "render", "audit", "crosscheck"):
        p = sub.add_parser(c)
        p.add_argument("map")
        if c in ("render", "audit"):
            p.add_argument("-o", "--out", required=True)
        if c == "render":
            p.add_argument("--numbering")
        if c == "audit":
            p.add_argument("--charge")
        if c == "crosscheck":
            p.add_argument("--numbering", required=True)
            p.add_argument("--charge", required=True)
        if c in ("validate", "crosscheck"):
            p.add_argument("--findings", help="Phase 6 findings ledger to append results to")
    a = ap.parse_args()
    m = load_map(a.map)
    if a.cmd == "validate":
        errors, warnings = validate(m)
        for x in errors: print(f"ERROR: {x}")
        for x in warnings: print(f"WARNING: {x}")
        print(f"TOTALS: errors={len(errors)} warnings={len(warnings)}")
        if getattr(a, "findings", None):
            emit_findings(a.findings, validate_findings(errors, warnings))
        sys.exit(2 if errors else (1 if warnings else 0))
    if a.cmd == "render":
        errors, _ = validate(m)
        if errors:
            for x in errors: print(f"ERROR: {x}")
            sys.exit("FATAL: fix validation errors before rendering")
        render(m, a.out, a.numbering)
    if a.cmd == "audit":
        sys.exit(audit(m, a.out, a.charge))
    if a.cmd == "crosscheck":
        code, problems = crosscheck(m, a.numbering, a.charge)
        if getattr(a, "findings", None) and problems:
            emit_findings(a.findings, [
                {"source": "routing-crosscheck", "question": "", "category": "ROUTING",
                 "severity": ("review" if pr.startswith("GROUP") else "critical"), "finding": pr}
                for pr in problems])
        sys.exit(code)

if __name__ == "__main__":
    main()
