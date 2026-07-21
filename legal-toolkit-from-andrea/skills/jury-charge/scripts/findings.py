#!/usr/bin/env python3
"""findings.py - Phase 6 findings ledger for the jury-charge skill.

Audit results become a work queue that ends in an amended document. Every
finding gets: evidence (charge text vs verbatim pattern-source text), a proposed fix
drawn only from verbatim source, and an attorney disposition. Nothing is
applied to a charge until its finding is dispositioned 'approved' (or
'modified') by the attorney. Rejected findings keep their fingerprint so re-running
audits never resurrects them as new.

Commands:
  import <ledger> <records.json>       Append an array of finding records (deduped).
  add    <ledger> --json '<record>'    Append one record.
  list   <ledger> [--status pending] [--severity critical]
  disposition <ledger> <id> <approved|rejected|modified|deferred> [--note TEXT]
  propose <ledger> <id> --fix TEXT [--basis "PJC 105.9" / "CACI 303"]
  report <ledger> -o <fix_proposal.html> [--matter NAME]
  status <ledger>                      TOTALS line.

Record shape (see SKILL.md Phase 6):
  {question, node_key, category, severity, finding, evidence:{charge,pjc,section},
   proposed_fix, fix_basis, disposition, disposition_note, source, history[]}
"""
import argparse, datetime, hashlib, html, json, sys
from pathlib import Path

CATEGORIES = {"MISSING_ELEMENT","WRONG_CITE","CONFLICTING_DEFINITION","MISSING_INSTRUCTION",
              "ROUTING","ARCHITECTURE","NO_SOURCE","ATTORNEY_DECISION","OTHER"}
SEVERITIES = {"critical","review","note"}
DISPOSITIONS = {"pending","approved","rejected","modified","deferred"}

def now(): return datetime.datetime.now().isoformat(timespec="seconds")

def load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

def save(p, ledger):
    Path(p).write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")

def fingerprint(r):
    basis = "|".join(str(r.get(k, "")) for k in ("question","category","finding"))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]

def normalize(r):
    r.setdefault("category","OTHER"); r.setdefault("severity","review")
    r.setdefault("disposition","pending"); r.setdefault("proposed_fix","")
    r.setdefault("fix_basis",""); r.setdefault("disposition_note","")
    r.setdefault("evidence",{}); r.setdefault("source","manual")
    if r["category"] not in CATEGORIES: sys.exit(f"bad category {r['category']}")
    if r["severity"] not in SEVERITIES: sys.exit(f"bad severity {r['severity']}")
    r["fingerprint"] = fingerprint(r)
    r.setdefault("history",[{"ts":now(),"event":"created"}])
    return r

def do_import(ledger, records):
    known = {r["fingerprint"] for r in ledger}
    added = skipped = 0
    for rec in records:
        rec = normalize(dict(rec))
        if rec["fingerprint"] in known:
            skipped += 1; continue
        rec["id"] = f"F-{len(ledger)+added+1:03d}"
        ledger.append(rec); known.add(rec["fingerprint"]); added += 1
    return added, skipped

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("import","add","list","disposition","propose","report","status"):
        p = sub.add_parser(c); p.add_argument("ledger")
        if c=="import": p.add_argument("records")
        if c=="add": p.add_argument("--json", required=True)
        if c=="list":
            p.add_argument("--status"); p.add_argument("--severity")
        if c=="disposition":
            p.add_argument("id"); p.add_argument("value", choices=sorted(DISPOSITIONS-{"pending"}))
            p.add_argument("--note", default="")
        if c=="propose":
            p.add_argument("id"); p.add_argument("--fix", required=True); p.add_argument("--basis", default="")
        if c=="report":
            p.add_argument("-o","--out", required=True); p.add_argument("--matter", default="")
    a = ap.parse_args()
    ledger = load(a.ledger)

    if a.cmd == "import":
        added, skipped = do_import(ledger, json.loads(Path(a.records).read_text(encoding="utf-8")))
        save(a.ledger, ledger); print(f"imported={added} deduped={skipped} total={len(ledger)}")
    elif a.cmd == "add":
        added, skipped = do_import(ledger, [json.loads(a.json)])
        save(a.ledger, ledger); print(f"added={added} deduped={skipped}")
    elif a.cmd == "list":
        for r in ledger:
            if a.status and r["disposition"] != a.status: continue
            if a.severity and r["severity"] != a.severity: continue
            print(f"{r['id']} [{r['severity']}/{r['disposition']}] {r.get('question','')} "
                  f"{r['category']}: {r['finding'][:100]}")
    elif a.cmd == "disposition":
        r = next((x for x in ledger if x["id"] == a.id), None) or sys.exit(f"no finding {a.id}")
        r["disposition"] = a.value; r["disposition_note"] = a.note
        r["history"].append({"ts": now(), "event": f"dispositioned:{a.value}", "note": a.note})
        save(a.ledger, ledger); print(f"{a.id} -> {a.value}")
    elif a.cmd == "propose":
        r = next((x for x in ledger if x["id"] == a.id), None) or sys.exit(f"no finding {a.id}")
        r["proposed_fix"] = a.fix; r["fix_basis"] = a.basis
        r["history"].append({"ts": now(), "event": "proposed"})
        save(a.ledger, ledger); print(f"{a.id} proposal recorded")
    elif a.cmd == "status":
        from collections import Counter
        c = Counter(r["disposition"] for r in ledger); s = Counter(r["severity"] for r in ledger)
        print(f"TOTALS: findings={len(ledger)} pending={c['pending']} approved={c['approved']} "
              f"rejected={c['rejected']} modified={c['modified']} deferred={c['deferred']} | "
              f"critical={s['critical']} review={s['review']} note={s['note']}")
        sys.exit(2 if any(r["severity"]=="critical" and r["disposition"]=="pending" for r in ledger) else 0)
    elif a.cmd == "report":
        E = html.escape
        order = {"critical":0,"review":1,"note":2}
        rows = ""
        for r in sorted(ledger, key=lambda x:(order[x["severity"]], x.get("question",""))):
            ev = r.get("evidence",{})
            rows += (f"<tr class='d-{E(r['disposition'])}'><td>{E(r['id'])}</td>"
                     f"<td>{E(r.get('question',''))}</td><td>{E(r['category'])}</td>"
                     f"<td class='s-{E(r['severity'])}'>{E(r['severity'])}</td>"
                     f"<td>{E(r['finding'])}</td>"
                     f"<td>{E(ev.get('charge',''))}</td>"
                     f"<td>{E(ev.get('pjc',''))}{(' <i>['+E(ev.get('section',''))+']</i>') if ev.get('section') else ''}</td>"
                     f"<td>{E(r['proposed_fix'])}{(' <i>Basis: '+E(r['fix_basis'])+'</i>') if r['fix_basis'] else ''}</td>"
                     f"<td><b>{E(r['disposition'].upper())}</b> {E(r['disposition_note'])}</td></tr>")
        doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Fix Proposal Report</title><style>
body{{font-family:Georgia,serif;margin:2em;color:#1a1a1a}}
h1{{border-bottom:3px solid #7c2529}} table{{border-collapse:collapse;width:100%;font-size:.85em}}
td,th{{border:1px solid #bbb;padding:.35em .5em;vertical-align:top;text-align:left}}
th{{background:#7c2529;color:#fff}} .s-critical{{color:#a40000;font-weight:bold}}
.s-review{{color:#9a6700}} .d-approved{{background:#e8f3e8}} .d-rejected{{background:#f0f0f0;color:#666}}
.d-pending{{background:#fff8e6}}</style></head><body>
<h1>Fix Proposal Report{(' — '+E(a.matter)) if a.matter else ''}</h1>
<p>Generated {now()} by the jury-charge skill (Phase 6). No fix is applied until its finding is dispositioned APPROVED or MODIFIED by the attorney. Proposed language is drawn only from verbatim pattern source or disclosed authority.</p>
<table><tr><th>ID</th><th>Q</th><th>Category</th><th>Severity</th><th>Finding</th>
<th>Charge language (current)</th><th>Pattern source / authority (verbatim)</th><th>Proposed fix</th><th>Disposition</th></tr>
{rows}</table></body></html>"""
        Path(a.out).write_text(doc, encoding="utf-8")
        print(f"report -> {a.out} ({len(ledger)} findings)")

if __name__ == "__main__":
    main()
