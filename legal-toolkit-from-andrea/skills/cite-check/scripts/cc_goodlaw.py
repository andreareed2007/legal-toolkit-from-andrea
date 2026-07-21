"""cc_goodlaw.py -- treatment-signal engine (good-law evidence pass).

Additive companion to the cite-check pipeline (2026.07.06 design doc, signed
off by author: summary section + per-card flags, N=10, uniform depth). Runs
AFTER phase2, renders as a separate Treatment axis -- verify() semantics and
the 11-verdict taxonomy are untouched.

Architecture (Phase 0 spike, 2026.07.06): probe-first. CourtListener search
supports `cites:(<opinion ids>) AND ("overruled" OR ...)` at our tier, so one
full-text probe covers ALL citing opinions; text is fetched only for hits,
to confirm PROXIMITY (the verb must treat OUR case, not a neighbor). If the
probe ever stops working, `_fallback_candidates_by_depth` reverts to the
handoff design (opinions-cited, top N by depth).

Language discipline (locked): output classes are evidence classes, never
verdicts. The ceiling of any claim is the coverage sentence. This module
never emits the words "good law".
"""

from __future__ import annotations

import os
import pickle
import re
import time
from urllib.parse import quote as _urlquote

import cl_resolver as _clr
from cl_resolver import BASE_URL, CLResolver, make_opinion_url

LEXICON_VERSION = 1
N_DEFAULT = 10          # citing opinions scanned per authority (author, 2026.07.06)
WINDOW = 250            # chars each side of a mention that a verb may sit in
CAUTION_CONFIRM_CAP = 3  # max text fetches spent confirming caution-only hits
NEGATIVE_WEIGHT = 80    # court weight at/above which a strong verb is NEGATIVE

GOODLAW_PKL = os.path.join(os.environ.get("CC_STATE_DIR", "/tmp"), "cc_goodlaw.pkl")

# --- Treatment lexicon (v1). Tune ONLY through cc_goodlaw_gate fixtures. ----

STRONG_PATTERNS = [
    ("overruled", re.compile(r"\boverrul(?:ed|es|ing)\b", re.I)),
    ("abrogated", re.compile(r"\babrogat(?:ed|es|ion|ing)\b", re.I)),
    ("disapproved", re.compile(r"\bdisapprov(?:ed|es|ing|al)\b", re.I)),
    ("superseded by statute", re.compile(r"\bsuperseded\s+(?:in\s+part\s+)?by\s+statute\b", re.I)),
    ("no longer good law", re.compile(r"\bno\s+longer\s+(?:good|controlling)\s+law\b", re.I)),
    ("receded from", re.compile(r"\breced(?:ed|es|ing)\s+from\b", re.I)),
    ("expressly rejected", re.compile(r"\bexpressly\s+reject(?:ed|s|ing)\b", re.I)),
]
CAUTION_PATTERNS = [
    ("distinguished", re.compile(r"\bdistinguish(?:ed|es|ing|able)\b", re.I)),
    ("declined to follow", re.compile(r"\bdeclin(?:ed|es|ing)\s+to\s+follow\b", re.I)),
    ("questioned", re.compile(r"\bquestioned\b", re.I)),
    ("criticized", re.compile(r"\bcritici[sz](?:ed|es|ing)\b", re.I)),
    ("limited to its facts", re.compile(r"\blimited\s+to\s+(?:its|their)\s+facts\b", re.I)),
    ("called into doubt", re.compile(r"\b(?:called|cast)\s+(?:into\s+)?doubt\b", re.I)),
]
# A strong verb NEGATED just before the match is not a signal ("declined to
# overrule", "does not abrogate", "we do not disapprove").
_NEGATION_RE = re.compile(
    r"(?:\bnot\b|\bnever\b|\bno\b|declin\w+\s+to|refus\w+\s+to|"
    r"do(?:es)?\s+not|did\s+not|without)\s+(?:\w+\s+){0,2}$", re.I)
# Courts overrule OBJECTIONS and motions; that is a procedural ruling, not
# case treatment (the Anderson control false positive, 2026.07.06).
_PROCEDURAL_RE = re.compile(
    r"\b(?:objections?|motions?|demurrers?|pleas?|exceptions?)\b", re.I)

_STRONG_QUERY = ('("overruled" OR "abrogated" OR "disapproved" OR '
                 '"superseded by statute" OR "no longer good law" OR '
                 '"receded from" OR "expressly rejected")')
_CAUTION_QUERY = ('("distinguished" OR "declined to follow" OR '
                  '"limited to its facts" OR "called into doubt")')

# Generic full-cite token, used ONLY for the nearest-cite guard (never for
# detection -- eyecite owns detection; prime directive unaffected).
_ANY_CITE_RE = re.compile(
    r"\b\d{1,4}\s+(?:WL|LEXIS|[A-Z][A-Za-z0-9.']*(?:\s?[A-Z0-9][A-Za-z0-9.']*){0,3})"
    r"\s+\d{1,7}\b")

_NAME_STOPWORDS = {
    "state", "states", "united", "commonwealth", "people", "city", "county",
    "town", "village", "matter", "estate", "ex", "parte", "re", "in", "of",
    "the", "and", "et", "al", "inc", "llc", "llp", "lp", "co", "corp",
    "company", "assn", "ass'n", "bd", "board", "dept", "department", "comm",
    "commission", "america", "american", "national", "trustee", "trust",
}


# --- Pure helpers (offline-testable; the gate exercises these) -------------

def name_tokens(case_name: str) -> list:
    """Distinctive capitalized party tokens from a case name (max 4)."""
    toks = []
    for raw in re.split(r"[\s,]+", case_name or ""):
        t = raw.strip(".,;:()[]'\"").replace("'s", "")
        if len(t) < 4 or not t[0].isupper():
            continue
        if t.lower() in _NAME_STOPWORDS:
            continue
        if t not in toks:
            toks.append(t)
        if len(toks) >= 4:
            break
    return toks


def cite_regexes(reporter_cite: str) -> list:
    """Punctuation/space-tolerant regexes for a reporter cite string.

    '478 U.S. 186' -> full-cite pattern plus the short-form 'at' pattern
    ('478 U.S. at 190'). Returns [] when the string does not parse.
    """
    m = re.match(r"^\s*(\d{1,4})\s+(.+?)\s+(\d{1,5})\s*$", reporter_cite or "")
    if not m:
        return []
    vol, rep, page = m.groups()
    rep_pat = re.escape(rep).replace(r"\.", r"\.?").replace(r"\ ", r"\s*")
    return [
        re.compile(rf"\b{vol}\s+{rep_pat}\s+{page}\b"),
        re.compile(rf"\b{vol}\s+{rep_pat}\s+at\s+\d{{1,5}}\b"),
    ]


def _mention_spans(text: str, toks: list, cite_res: list) -> list:
    spans = []
    lowtoks = {t.lower() for t in toks}
    for t in toks:
        for m in re.finditer(rf"\b{re.escape(t)}\b", text):
            tail = text[m.end():m.end() + 40]
            vm = re.match(r"\s+v\.?\s+([A-Z][\w.']*)", tail)
            if vm and vm.group(1).strip(".,'").lower() not in lowtoks:
                continue  # same surname, different case
            spans.append(m.span())
    for cr in cite_res:
        for m in cr.finditer(text):
            spans.append(m.span())
    return sorted(set(spans))


def _sentence_around(text: str, pos: int, cap: int = 500) -> str:
    """Quoting window around a verb hit. Sentence bounds are unreliable in
    cite-dense passages (reporter abbreviations end in '. '), so enforce a
    minimum context window and trim to word boundaries."""
    start = max(text.rfind(". ", 0, pos), text.rfind("\n", 0, pos)) + 1
    end_dot = text.find(". ", pos)
    end_nl = text.find("\n", pos)
    ends = [e for e in (end_dot, end_nl) if e != -1]
    end = (min(ends) + 1) if ends else min(len(text), pos + cap)
    if pos - start < 100:
        start = max(0, pos - 220)
    if end - pos < 100:
        end = min(len(text), pos + 280)
    frag = text[max(start, pos - cap // 2):min(end, pos + cap)].strip()
    frag = re.sub(r"\s+", " ", frag)[:cap]
    return ("\u2026" + frag) if start > 0 else frag


def scan_text(text: str, toks: list, cite_res: list) -> list:
    """Scan ONE citing opinion's text for treatment verbs proximate to OUR case.

    Returns raw signals: {verb, cls, passage, distance}. A verb counts only
    when (a) a mention of our case sits within WINDOW chars, (b) no OTHER
    full cite token sits strictly between verb and mention (nearest-cite
    guard), and (c) the verb is not negated.
    """
    if not text:
        return []
    mentions = _mention_spans(text, toks, cite_res)
    if not mentions:
        return []
    signals = []
    for cls, patterns in (("strong", STRONG_PATTERNS), ("caution", CAUTION_PATTERNS)):
        for verb, rx in patterns:
            for m in rx.finditer(text):
                vpos = m.start()
                if cls == "strong" and _NEGATION_RE.search(text[max(0, vpos - 60):vpos]):
                    continue
                if _PROCEDURAL_RE.search(text[max(0, vpos - 45):m.end() + 45]):
                    continue  # "objections overruled" -- ruling, not treatment
                best = None
                for ms, me in mentions:
                    d = 0 if ms <= vpos <= me else min(abs(vpos - me), abs(ms - m.end()))
                    if d <= WINDOW and (best is None or d < best[0]):
                        best = (d, ms, me)
                if best is None:
                    continue
                lo, hi = min(best[1], vpos), max(best[2], m.end())
                between = text[min(lo, hi):max(lo, hi)]
                stripped = between
                for cr in cite_res:
                    stripped = cr.sub(" ", stripped)
                if _ANY_CITE_RE.search(stripped):
                    continue  # a different case sits between the verb and ours
                signals.append({
                    "verb": verb, "cls": cls, "distance": best[0],
                    "passage": _sentence_around(text, vpos),
                })
    # Deduplicate per verb, keep the closest occurrence.
    best_by_verb = {}
    for s in signals:
        k = s["verb"]
        if k not in best_by_verb or s["distance"] < best_by_verb[k]["distance"]:
            best_by_verb[k] = s
    return sorted(best_by_verb.values(), key=lambda s: s["distance"])


def court_weight(court_name: str, court_id: str = "") -> int:
    """Approximate rank of a citing court. Imperfect by design (documented):
    Maryland/New York-style high courts named 'Court of Appeals' rank 70,
    which can downgrade a true NEGATIVE to CAUTION -- evidence still renders."""
    n = (court_name or "").lower()
    cid = (court_id or "").lower()
    if cid == "scotus" or "supreme court of the united states" in n:
        return 100
    if "supreme court" in n:
        return 40 if ("county" in n or "appellate term" in n) else 90
    if "court of criminal appeals" in n:
        return 90 if ("texas" in n or "oklahoma" in n) else 70
    if "court of appeals for the" in n or "circuit" in n:
        return 80
    if "court of appeals" in n or "appellate" in n or "appeals" in n:
        return 70
    if "district" in n or "bankruptcy" in n:
        return 50
    return 60


def is_later_history(cited_name: str, citing_name: str) -> bool:
    a = set(t.lower() for t in name_tokens(cited_name))
    b = set(t.lower() for t in name_tokens(citing_name))
    return len(a & b) >= max(1, min(len(a), 2) if len(a) <= 2 else 2)


def classify(signals: list) -> str:
    """Evidence class for one authority from its confirmed signals."""
    strong = [s for s in signals if s["cls"] == "strong"]
    if any(s.get("court_weight", 0) >= NEGATIVE_WEIGHT or s.get("later_history")
           for s in strong):
        return "negative"
    if strong:
        return "caution"
    caution_ops = {s.get("op_id") for s in signals if s["cls"] == "caution"}
    if len(caution_ops) >= 2:
        return "caution"
    return "none"


# --- API layer (rate-paced through CLResolver plumbing) ---------------------

class GoodlawClient:
    def __init__(self, resolver: CLResolver | None = None):
        self.resolver = resolver or CLResolver()

    @property
    def calls(self) -> int:
        return getattr(self.resolver, "_request_count", 0)

    def cluster_meta(self, cluster_id: str) -> dict:
        data = self.resolver._get(
            f"{BASE_URL}/clusters/{cluster_id}/?format=json"
            "&fields=sub_opinions,case_name,citation_count,absolute_url") or {}
        subs = []
        for s in data.get("sub_opinions", []):
            sid = _clr.CLResolver._sub_opinion_id(s)
            if sid:
                subs.append(str(sid))
        return {"sub_ids": subs,
                "case_name": data.get("case_name", ""),
                "cited_by_total": data.get("citation_count", 0),
                "url": make_opinion_url(data.get("absolute_url", "")
                                        or f"/opinion/{cluster_id}/")}

    def probe(self, sub_ids: list, terms_query: str) -> list | None:
        """cites:() full-text probe. None signals probe failure (fallback)."""
        if not sub_ids:
            return None
        q = f"cites:({' OR '.join(sub_ids)}) AND {terms_query}"
        data = self.resolver._get(
            f"{BASE_URL}/search/?type=o&q={_urlquote(q)}"
            "&fields=caseName,cluster_id,court,court_id,dateFiled,absolute_url,opinions")
        if not isinstance(data, dict) or "results" not in data:
            return None
        return data["results"]

    def fallback_candidates_by_depth(self, sub_ids: list, cap: int) -> list:
        out = []
        for sid in sub_ids[:2]:
            data = self.resolver._get(
                f"{BASE_URL}/opinions-cited/?cited_opinion={sid}"
                "&fields=citing_opinion,depth") or {}
            for r in data.get("results", []) if isinstance(data, dict) else []:
                op = str(r.get("citing_opinion", "")).rstrip("/").split("/")[-1]
                if op:
                    out.append({"op_id": op, "depth": r.get("depth", 0),
                                "name": "", "court": "", "date": "", "url": "",
                                "weight": 60, "src": "depth"})
        out.sort(key=lambda c: -c.get("depth", 0))
        return out[:cap]

    def opinion_text(self, op_id: str) -> str:
        data = self.resolver._get(f"{BASE_URL}/opinions/{op_id}/?format=json")
        if not data or "error" in (data or {}):
            return ""
        return _clr.extract_opinion_text(data) or ""


def _hit_to_candidate(hit: dict) -> dict:
    ops = hit.get("opinions") or []
    op_id = ""
    for o in ops:
        if isinstance(o, dict) and o.get("id"):
            op_id = str(o["id"])
            break
    return {
        "op_id": op_id,
        "cluster_id": str(hit.get("cluster_id", "")),
        "name": hit.get("caseName", ""),
        "court": hit.get("court", ""),
        "court_id": hit.get("court_id", ""),
        "date": hit.get("dateFiled", "") or "",
        "url": make_opinion_url(hit.get("absolute_url", "")),
        "weight": court_weight(hit.get("court", ""), hit.get("court_id", "")),
        "src": "probe",
    }


# --- Authority discovery from pipeline state --------------------------------

def build_authorities(ckpt: dict, lookup_map: dict, client: GoodlawClient | None,
                      n: int = N_DEFAULT) -> dict:
    """Unique CL authorities from the checkpoint. Treatment is per AUTHORITY;
    instances map back via instance_indexes. Missing cluster IDs are
    bootstrapped with ONE batched citation-lookup (paced) when a client is
    given. No-ID entries land NOT CHECKED with the reason stated."""
    cits = ckpt["built"]["citations"]
    ot = ckpt.get("opinion_texts", {})
    rs = ckpt.get("recap_src", {})
    auth, need_lookup = {}, []
    cluster_of = {}
    for i, c in enumerate(cits):
        if not ot.get(i):
            cluster_of[i] = ("", "unresolved")
            continue
        if rs.get(i):
            cluster_of[i] = ("", "RECAP/PACER document -- no citation graph")
            continue
        k = _clr._normalize_cite(_clr.reporter_cite_str(c) or "")
        e = (lookup_map or {}).get(k) if k else None
        if e and e.get("status") == 200 and e.get("clusters"):
            cluster_of[i] = (str(e["clusters"][0]), "")
        else:
            need_lookup.append(i)
    if need_lookup and client is not None:
        st = client.resolver.batch_lookup_step([cits[i] for i in need_lookup])
        m = st.get("map", {})
        for i in need_lookup:
            k = _clr._normalize_cite(_clr.reporter_cite_str(cits[i]) or "")
            e = m.get(k) if k else None
            if e and e.get("status") == 200 and e.get("clusters"):
                cluster_of[i] = (str(e["clusters"][0]), "")
            else:
                cluster_of[i] = ("", "no CourtListener cluster ID "
                                     "(free-source copy or lookup miss)")
    elif need_lookup:
        for i in need_lookup:
            cluster_of[i] = ("", "no CourtListener cluster ID")
    for i, c in enumerate(cits):
        cid, reason = cluster_of[i]
        key = cid or f"__nc_{_clr._normalize_cite(_clr.reporter_cite_str(c) or '') or i}"
        a = auth.setdefault(key, {
            "cluster_id": cid, "name": c.name,
            "reporter_cite": _clr.reporter_cite_str(c) or "",
            "instance_indexes": [], "status": "pending" if cid else "not_checked",
            "not_checked_reason": reason, "candidates": [], "scanned": [],
            "signals": [], "classification": "not_checked" if not cid else "",
            "cited_by_total": 0, "probe_ok": None, "url": "",
        })
        a["instance_indexes"].append(i)
    return auth


# --- Resumable driver --------------------------------------------------------

def load_state() -> dict | None:
    if os.path.exists(GOODLAW_PKL):
        return pickle.load(open(GOODLAW_PKL, "rb"))
    return None


def save_state(state: dict) -> None:
    pickle.dump(state, open(GOODLAW_PKL, "wb"))


def _advance_authority(a: dict, client: GoodlawClient, n: int, deadline: float) -> bool:
    """Advance one authority's state machine. Returns False on deadline."""
    toks = name_tokens(a["name"])
    cite_res = cite_regexes(a["reporter_cite"])
    while a["status"] not in ("done", "not_checked"):
        if time.time() > deadline:
            return False
        if a["status"] == "pending":
            meta = client.cluster_meta(a["cluster_id"])
            if not meta.get("sub_ids"):
                # Transient CL failure or empty cluster: NEVER classify a
                # hollow scan as "no signal". Retry next window, then give up
                # honestly (2026.07.06 Anderson bug).
                a["_meta_attempts"] = a.get("_meta_attempts", 0) + 1
                if a["_meta_attempts"] >= 2:
                    a["status"] = "not_checked"
                    a["classification"] = "not_checked"
                    a["not_checked_reason"] = ("cluster metadata unavailable "
                                               "after 2 attempts")
                return True
            a["cited_by_total"] = meta.get("cited_by_total", 0)
            a["url"] = meta.get("url", "")
            a["_sub_ids"] = meta.get("sub_ids", [])
            a["status"] = "meta"
        elif a["status"] == "meta":
            strong = client.probe(a["_sub_ids"], _STRONG_QUERY)
            if time.time() > deadline and strong is None:
                return False
            caution = client.probe(a["_sub_ids"], _CAUTION_QUERY) if strong is not None else None
            if strong is None:
                a["probe_ok"] = False
                a["candidates"] = client.fallback_candidates_by_depth(a["_sub_ids"], n)
                if not a["candidates"]:
                    a["_probe_attempts"] = a.get("_probe_attempts", 0) + 1
                    if a["_probe_attempts"] >= 2:
                        a["status"] = "not_checked"
                        a["classification"] = "not_checked"
                        a["not_checked_reason"] = ("citation graph unavailable "
                                                   "(probe and depth fallback "
                                                   "both returned nothing)")
                    return True
            else:
                a["probe_ok"] = True
                cands, seen = [], set()
                for hit in strong:
                    c = _hit_to_candidate(hit)
                    if c["op_id"] and c["op_id"] not in seen:
                        c["from"] = "strong"
                        seen.add(c["op_id"])
                        cands.append(c)
                n_caution = 0
                for hit in sorted((caution or []), key=lambda h: (
                        -court_weight(h.get("court", ""), h.get("court_id", "")),
                        h.get("dateFiled") or "")):
                    if n_caution >= CAUTION_CONFIRM_CAP:
                        break
                    c = _hit_to_candidate(hit)
                    if c["op_id"] and c["op_id"] not in seen:
                        c["from"] = "caution"
                        seen.add(c["op_id"])
                        cands.append(c)
                        n_caution += 1
                cands.sort(key=lambda c: c.get("date") or "", reverse=True)
                cands.sort(key=lambda c: -c["weight"])  # stable: weight, then recency
                a["candidates"] = cands[:n]
            a["status"] = "listed"
        elif a["status"] == "listed":
            todo = [c for c in a["candidates"] if c["op_id"] not in a["scanned"]]
            if not todo:
                a["classification"] = classify(a["signals"])
                a["status"] = "done"
                continue
            cand = todo[0]
            text = client.opinion_text(cand["op_id"])
            a["scanned"].append(cand["op_id"])
            for s in scan_text(text, toks, cite_res):
                s.update(op_id=cand["op_id"], citing_name=cand["name"],
                         court=cand["court"], date=cand["date"], url=cand["url"],
                         court_weight=cand["weight"],
                         later_history=is_later_history(a["name"], cand["name"]))
                a["signals"].append(s)
            # Early stop: confirmed strong verb at NEGATIVE weight.
            if any(s["cls"] == "strong" and (s["court_weight"] >= NEGATIVE_WEIGHT
                                             or s["later_history"])
                   for s in a["signals"]):
                a["classification"] = "negative"
                a["status"] = "done"
    return True


def run(ckpt: dict, lookup_map: dict, n: int = N_DEFAULT,
        deadline: float | None = None) -> dict:
    """Resumable entry point (runner verb). Loads/creates state, advances
    until done or deadline, saves after every authority step."""
    deadline = deadline or (time.time() + 38)
    client = GoodlawClient()
    state = load_state()
    doc_name = ckpt.get("doc_name", "")
    if not state or state.get("doc_name") != doc_name:
        state = {"doc_name": doc_name,
                 "params": {"n": n, "lexicon_version": LEXICON_VERSION,
                            "tiered": False},
                 "authorities": build_authorities(ckpt, lookup_map, client, n),
                 "api_calls_total": 0}
        save_state(state)
    n = state["params"]["n"]
    # Fresh process each window: the client's call counter starts at 0, so
    # the window base must too (a killed window leaves a stale _win_base).
    state["_win_base"] = 0
    for key in sorted(state["authorities"]):
        a = state["authorities"][key]
        if a["status"] in ("done", "not_checked"):
            continue
        finished = _advance_authority(a, client, n, deadline)
        state["api_calls_total"] += client.calls - state.get("_win_base", 0)
        state["_win_base"] = client.calls
        save_state(state)
        if not finished:
            break
    state.pop("_win_base", None)
    save_state(state)
    return state


def is_done(state: dict) -> bool:
    return all(a["status"] in ("done", "not_checked")
               for a in state["authorities"].values())


# --- Report payload ----------------------------------------------------------

def summary(state: dict) -> dict:
    """Renderer payload: rows for flagged authorities, coverage for the rest,
    and a per-instance-index class map for card flags. Language discipline:
    evidence classes only; the coverage sentence is the ceiling of the claim."""
    rows, clean, not_checked, by_index = [], [], [], {}
    for key in sorted(state["authorities"]):
        a = state["authorities"][key]
        cls = a["classification"] or "none"
        for i in a["instance_indexes"]:
            by_index[i] = cls
        cov = ""
        if a["status"] == "done":
            if a.get("probe_ok"):
                cov = (f"Full-text treatment-term probe of the "
                       f"{a['cited_by_total']} opinions citing this case; "
                       f"{len(a['scanned'])} hit(s) scanned for proximity.")
            else:
                cov = (f"Probe unavailable; the {len(a['scanned'])} most "
                       f"prominent citing opinions (by depth) were scanned.")
        entry = {"name": a["name"], "reporter_cite": a["reporter_cite"],
                 "classification": cls, "coverage": cov, "url": a.get("url", ""),
                 "instance_indexes": a["instance_indexes"],
                 "signals": [
                     {k: s.get(k, "") for k in
                      ("verb", "cls", "passage", "citing_name", "court",
                       "date", "url")}
                     for s in sorted(a["signals"],
                                     key=lambda s: (s["cls"] != "strong",
                                                    -s.get("court_weight", 0)))
                 ][:4]}
        if cls in ("negative", "caution"):
            rows.append(entry)
        elif a["status"] == "not_checked":
            entry["reason"] = a.get("not_checked_reason", "")
            not_checked.append(entry)
        else:
            clean.append(entry)
    return {"rows": rows, "clean": clean, "not_checked": not_checked,
            "by_index": by_index, "params": state["params"],
            "api_calls_total": state.get("api_calls_total", 0)}
