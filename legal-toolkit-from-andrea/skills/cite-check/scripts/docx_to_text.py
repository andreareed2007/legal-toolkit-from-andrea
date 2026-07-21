#!/usr/bin/env python3
"""docx_to_text.py -- extract a .docx/.dotx brief to plain text with each
footnote spliced INLINE at its reference marker.

Why this exists (2026.07.13, QA-Brief Opposition to Interpleader miss):
The cite-check pipeline is citation-anchored and its proposition logic keys
off adjacency -- a citation's proposition is the sentence it sits next to.
python-docx's ``paragraph.text`` silently drops BOTH the footnote reference
marks and the footnote text, so a naive extraction loses every footnoted
citation.  Appending the footnotes as a block at the END of the document is
worse: it (a) detaches every citation from the sentence it supports and
(b) creates a trailing wall of citations that the pipeline's back-matter /
TOA strip discards -- so footnoted cites are never verified at all.  That is
exactly how the brief's "...is not a magic wand..." quote (cited in
footnote 15 as ``Id. at 704 (Hudson, J., dissenting)`` -> Madeksho v.
Watkins, 112 S.W.3d 679) slipped through unchecked.

The fix: walk the document body in reading order and, at each footnote
reference, splice that footnote's text inline right after the marker.  A
footnoted citation then behaves like an ordinary trailing inline citation --
the proposition falls out of the preceding sentence, and an ``Id.`` resolves
to the full cite spliced just before it (its true antecedent).

Usage:
    python3 docx_to_text.py <in.docx> [out.txt]
If out.txt is omitted, text is written to stdout.

'All changes accepted' semantics: inserted runs use <w:t> (kept); deleted
runs use <w:delText> (dropped) -- so tracked-change briefs extract as the
current/accepted text, matching what a reviewer sees.
"""
import sys

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _load_footnotes(doc):
    """Return {footnote_id: text} from the document's footnotes part."""
    from lxml import etree
    fn_part = None
    for rel in doc.part.rels.values():
        if "footnotes" in rel.reltype:
            fn_part = rel.target_part
            break
    notes = {}
    if fn_part is None:
        return notes
    root = etree.fromstring(fn_part.blob)
    for f in root.findall(W + "footnote"):
        fid = f.get(W + "id")
        # <w:t> only -> excludes <w:delText> (deleted tracked-change text).
        txt = "".join(t.text or "" for t in f.iter(W + "t")).strip()
        notes[fid] = txt
    return notes


def _para_text(p, notes):
    """Reconstruct one paragraph's text, splicing footnote text inline at
    each reference marker in document order."""
    out = []
    for node in p._p.iter():
        tag = node.tag
        if tag == W + "t":
            out.append(node.text or "")
        elif tag == W + "footnoteReference":
            fid = node.get(W + "id")
            ftxt = (notes.get(fid) or "").strip()
            if ftxt:
                # Splice as its own trailing clause: a leading space keeps it
                # from gluing onto the prior word, and a trailing period (if
                # the footnote does not already end in sentence punctuation)
                # closes the clause so the NEXT body sentence stays separate.
                if ftxt[-1] not in ".;:!?":
                    ftxt += "."
                out.append(" " + ftxt + " ")
    return "".join(out)


def extract(path):
    import docx
    doc = docx.Document(path)
    notes = _load_footnotes(doc)
    lines = [_para_text(p, notes) for p in doc.paragraphs]
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 docx_to_text.py <in.docx> [out.txt]")
    text = extract(sys.argv[1])
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[docx_to_text] {len(text)} chars -> {sys.argv[2]}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
