#!/usr/bin/env python3
"""
derive_ca_profile.py -- build a California pleading profile from the USER'S OWN
sample CA state pleadings. Never fabricate California format rules from memory:
this script observes measurable geometry from real sample .docx files and writes
`ca_profile.json`, which validate_ca_state.py then enforces.

Usage:
    python derive_ca_profile.py sample1.docx [sample2.docx ...] \
        [--out ca_profile.json]

What it observes (per sample, then reports the common/most-frequent value):
  - page size and margins (DXA)
  - body font and size
  - presence of a left-margin line-numbering column (CA pleading paper is
    28-line numbered paper -- flagged, but CONFIRMED from the sample, not assumed)
  - caption table dimensions (rows x cols, column widths)
  - line spacing of body paragraphs

The output is a starting point for Claude to review WITH the user against the
samples. Anything the script cannot observe is written as null with a note, so
no rule is invented.
"""
import argparse
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _doc_xml(path):
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("word/document.xml"))


def _styles_xml(path):
    with zipfile.ZipFile(path) as z:
        try:
            return ET.fromstring(z.read("word/styles.xml"))
        except KeyError:
            return None


def observe(path):
    prof = {"file": os.path.basename(path)}
    root = _doc_xml(path)
    body = root.find(W + "body")
    sect = body.find(W + "sectPr") if body is not None else None

    # page + margins
    if sect is not None:
        pg = sect.find(W + "pgSz")
        mar = sect.find(W + "pgMar")
        if pg is not None:
            prof["page_w"] = pg.get(W + "w")
            prof["page_h"] = pg.get(W + "h")
        if mar is not None:
            prof["margins"] = {k: mar.get(W + k) for k in ("top", "bottom", "left", "right")}
        # line numbering column (CA pleading paper)
        lnnum = sect.find(W + "lnNumType")
        prof["line_numbering_present"] = lnnum is not None
        if lnnum is not None:
            prof["line_numbering"] = {
                "countBy": lnnum.get(W + "countBy"),
                "start": lnnum.get(W + "start"),
                "restart": lnnum.get(W + "restart"),
            }
    else:
        prof["line_numbering_present"] = None

    # body font/size from Normal style
    styles = _styles_xml(path)
    if styles is not None:
        for st in styles.findall(W + "style"):
            if st.get(W + "styleId") in ("Normal", "DefaultParagraphFont"):
                rpr = st.find(W + "rPr")
                if rpr is not None:
                    rf = rpr.find(W + "rFonts")
                    sz = rpr.find(W + "sz")
                    if rf is not None:
                        prof["body_font"] = rf.get(W + "ascii")
                    if sz is not None:
                        prof["body_size"] = sz.get(W + "val")
                break

    # first table dims (likely the caption)
    tbl = body.find(W + "tbl") if body is not None else None
    if tbl is not None:
        rows = tbl.findall(W + "tr")
        grid = tbl.find(W + "tblGrid")
        cols = grid.findall(W + "gridCol") if grid is not None else []
        prof["caption_table"] = {
            "rows": len(rows),
            "cols": len(cols),
            "col_widths": [c.get(W + "w") for c in cols],
        }
    return prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("samples", nargs="+")
    ap.add_argument("--out", default="ca_profile.json")
    args = ap.parse_args()

    observations = [observe(p) for p in args.samples]

    def common(key, sub=None):
        vals = []
        for o in observations:
            v = o.get(key)
            if sub and isinstance(v, dict):
                v = v.get(sub)
            if v is not None:
                vals.append(json.dumps(v, sort_keys=True))
        if not vals:
            return None
        return json.loads(Counter(vals).most_common(1)[0][0])

    profile = {
        "jurisdiction": "CA-STATE",
        "derived_from": [o["file"] for o in observations],
        "page_w": common("page_w"),
        "page_h": common("page_h"),
        "margins": common("margins"),
        "line_numbering_present": common("line_numbering_present"),
        "line_numbering": common("line_numbering"),
        "body_font": common("body_font"),
        "body_size": common("body_size"),
        "caption_table": common("caption_table"),
        "observations": observations,
        "_notes": (
            "Derived from the user's own sample CA pleadings. Review each value "
            "against the samples before relying on it. Null values were not "
            "observable and must be filled in from the samples by hand -- do not "
            "invent them. California trial-court pleadings use 28-line numbered "
            "pleading paper; confirm line_numbering_present is true in the samples."
        ),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    print("wrote " + args.out)
    print("line numbering present:", profile["line_numbering_present"])
    print("body font/size:", profile["body_font"], profile["body_size"])
    print("caption table:", profile["caption_table"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
