#!/usr/bin/env python3
"""
Core patch operations shared across all court types.

Universal operations:
1. Patch ListParagraph style in styles.xml
2. Ensure ListParagraph style exists (inject if missing)
3. Ensure numbering.xml has ListParagraph numbering config
4. Patch document.xml ListParagraph paragraphs (inline pPr)
5. Convert manually-typed numbers to ListParagraph
6. Patch underscore signatures → underline+tab technique
"""
import os
import re


def patch_listparagraph_style(styles_xml):
    """Replace bare ListParagraph style with correct definition."""
    bare = '''  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
  </w:style>'''

    full = '''  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr>
      <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>
      <w:ind w:left="0" w:firstLine="720"/>
      <w:jc w:val="both"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Century Schoolbook" w:eastAsia="Century Schoolbook" w:hAnsi="Century Schoolbook" w:cs="Century Schoolbook"/>
      <w:sz w:val="24"/>
    </w:rPr>
  </w:style>'''

    if bare in styles_xml:
        return styles_xml.replace(bare, full)
    print("  WARN: Bare ListParagraph style not found, trying alternate match")
    pattern = re.compile(
        r'<w:style w:type="paragraph" w:styleId="ListParagraph">.*?</w:style>',
        re.DOTALL
    )
    if pattern.search(styles_xml):
        return pattern.sub(full.strip(), styles_xml)
    print("  ERROR: Could not find ListParagraph style to patch")
    return styles_xml


def patch_listparagraph_paragraphs(doc_xml):
    """Replace inline indent on ListParagraph paragraphs with left=0 firstLine=720."""
    pattern = re.compile(
        r'(<w:pPr>\s*'
        r'<w:pStyle w:val="ListParagraph"/>\s*'
        r'<w:numPr>\s*'
        r'<w:ilvl w:val="\d+"/>\s*'
        r'<w:numId w:val="\d+"/>\s*'
        r'</w:numPr>)'
        r'(.*?)'
        r'(</w:pPr>)',
        re.DOTALL
    )

    def replace_inline(match):
        ppr_start = match.group(1)
        close = match.group(3)
        inline = """
        <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>
        <w:ind w:left="0" w:firstLine="720"/>
        <w:contextualSpacing w:val="0"/>
        <w:jc w:val="both"/>
      """
        return ppr_start + inline + close

    count = len(pattern.findall(doc_xml))
    patched = pattern.sub(replace_inline, doc_xml)
    print(f"  Patched {count} ListParagraph paragraphs with left=0 firstLine=720")

    # Strip leading <w:tab/> from the first run of every ListParagraph paragraph.
    # numPr's suffix="tab" already inserts a tab between number and body.
    para_pat = re.compile(r'<w:p[ >].*?</w:p>', re.DOTALL)
    stripped = 0
    out_parts = []
    last_end = 0
    for pm in para_pat.finditer(patched):
        pxml = pm.group(0)
        if 'w:val="ListParagraph"' in pxml:
            tabm = re.search(
                r'</w:pPr>\s*(<w:r[^>]*>)\s*(?:<w:rPr>.*?</w:rPr>\s*)?<w:tab/>',
                pxml, re.DOTALL
            )
            if tabm:
                pxml = pxml[:tabm.start()] + pxml[tabm.start():tabm.end()].replace('<w:tab/>', '', 1) + pxml[tabm.end():]
                stripped += 1
        out_parts.append(patched[last_end:pm.start()])
        out_parts.append(pxml)
        last_end = pm.end()
    out_parts.append(patched[last_end:])
    patched = ''.join(out_parts)
    if stripped:
        print(f"  Stripped leading <w:tab/> from {stripped} ListParagraph run(s) (number suffix=tab handles spacing)")
    return patched


def ensure_listparagraph_style(styles_xml):
    """Ensure styles.xml contains a ListParagraph style definition.

    If no ListParagraph style exists at all (common in manually-drafted docs),
    inject one before the closing </w:styles> tag.
    Returns (styles_xml, was_added).
    """
    if 'w:styleId="ListParagraph"' in styles_xml:
        return styles_xml, False

    lp_style = '''  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr>
      <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>
      <w:ind w:left="0" w:firstLine="720"/>
      <w:jc w:val="both"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Century Schoolbook" w:eastAsia="Century Schoolbook" w:hAnsi="Century Schoolbook" w:cs="Century Schoolbook"/>
      <w:sz w:val="24"/>
    </w:rPr>
  </w:style>
'''
    if '</w:styles>' in styles_xml:
        styles_xml = styles_xml.replace('</w:styles>', lp_style + '</w:styles>')
        print("  Injected ListParagraph style definition (was missing)")
        return styles_xml, True

    print("  ERROR: Could not find </w:styles> to inject ListParagraph style")
    return styles_xml, False



def _rewrite_abstractnum_lvl0_ind(numbering_xml, abstract_id):
    """Rewrite the lvl 0 <w:pPr> of a specific abstractNum to spec ind.

    Spec: tab pos=720, left=0, firstLine=720. Wrapped lines flush to left margin.
    """
    pattern = re.compile(
        r'(<w:abstractNum\s+w:abstractNumId="' + re.escape(str(abstract_id))
        + r'"[^>]*>.*?<w:lvl\s+w:ilvl="0">.*?)<w:pPr>.*?</w:pPr>',
        re.DOTALL
    )
    new_pPr = (
        '<w:pPr>'
        '<w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs>'
        '<w:ind w:left="0" w:firstLine="720"/>'
        '</w:pPr>'
    )
    new_xml, n = pattern.subn(lambda m: m.group(1) + new_pPr, numbering_xml, count=1)
    if n:
        print(f"  Rewrote abstractNum {abstract_id} lvl 0 ind -> left=0/firstLine=720")
    return new_xml


def ensure_listparagraph_numbering(numbering_xml):
    """Ensure numbering.xml has a ListParagraph-compatible decimal numbering config.

    Checks for an existing decimal level-0 config. If none found, adds one.
    Returns (numbering_xml, numId) where numId is the w:numId to use in document.xml.
    """
    if re.search(r'<w:lvl\s+w:ilvl="0"[^>]*>.*?<w:numFmt\s+w:val="decimal"/>', numbering_xml, re.DOTALL):
        abstract_match = re.search(
            r'<w:abstractNum\s+w:abstractNumId="(\d+)"[^>]*>'
            r'.*?<w:lvl\s+w:ilvl="0"[^>]*>.*?<w:numFmt\s+w:val="decimal"/>',
            numbering_xml, re.DOTALL
        )
        if abstract_match:
            abstract_id = abstract_match.group(1)
            num_match = re.search(
                r'<w:num\s+w:numId="(\d+)"[^>]*>\s*'
                r'<w:abstractNumId\s+w:val="' + abstract_id + r'"/>',
                numbering_xml
            )
            if num_match:
                num_id = num_match.group(1)
                print(f"  Found existing decimal numbering: abstractNum={abstract_id}, numId={num_id}")
                numbering_xml = _rewrite_abstractnum_lvl0_ind(numbering_xml, abstract_id)
                return numbering_xml, num_id

    abstract_ids = [int(m) for m in re.findall(r'w:abstractNumId="(\d+)"', numbering_xml)]
    num_ids = [int(m) for m in re.findall(r'w:numId="(\d+)"', numbering_xml)]
    new_abstract_id = max(abstract_ids, default=-1) + 1
    new_num_id = max(num_ids, default=0) + 1

    abstract_num = f'''  <w:abstractNum w:abstractNumId="{new_abstract_id}">
    <w:multiLevelType w:val="hybridMultilevel"/>
    <w:lvl w:ilvl="0">
      <w:start w:val="1"/>
      <w:numFmt w:val="decimal"/>
      <w:lvlText w:val="%1."/>
      <w:lvlJc w:val="left"/>
      <w:pPr>
        <w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs>
        <w:ind w:left="0" w:firstLine="720"/>
      </w:pPr>
      <w:rPr>
        <w:b w:val="0"/>
        <w:i w:val="0"/>
      </w:rPr>
    </w:lvl>
  </w:abstractNum>
'''

    num_ref = f'''  <w:num w:numId="{new_num_id}">
    <w:abstractNumId w:val="{new_abstract_id}"/>
  </w:num>
'''

    first_num = re.search(r'\s*<w:num\s', numbering_xml)
    if first_num:
        pos = first_num.start()
        numbering_xml = numbering_xml[:pos] + '\n' + abstract_num + numbering_xml[pos:]
        numbering_xml = numbering_xml.replace('</w:numbering>', num_ref + '</w:numbering>')
    elif '</w:numbering>' in numbering_xml:
        numbering_xml = numbering_xml.replace(
            '</w:numbering>',
            abstract_num + num_ref + '</w:numbering>'
        )
    else:
        print("  ERROR: Could not find insertion point in numbering.xml")
        return numbering_xml, None

    print(f"  Added ListParagraph numbering: abstractNum={new_abstract_id}, numId={new_num_id}")
    return numbering_xml, str(new_num_id)


def convert_manual_numbers_to_listparagraph(doc_xml, num_id):
    """Convert manually-typed numbered paragraphs to ListParagraph + numPr.

    Handles:
      A) Single merged run: <w:r><w:tab/><w:t>1.</w:t><w:tab/><w:t>Body...</w:t></w:r>
      B) Flat typed:        <w:r><w:t xml:space="preserve">4. </w:t></w:r>
    """
    if num_id is None:
        print("  ERROR: No numId available -- cannot convert manual numbers")
        return doc_xml

    para_pattern = re.compile(r'(<w:p[ >].*?</w:p>)', re.DOTALL)
    paragraphs = list(para_pattern.finditer(doc_xml))

    tbl_ranges = [(m.start(), m.end())
                  for m in re.finditer(r'<w:tbl[ >].*?</w:tbl>', doc_xml, re.DOTALL)]

    def in_table(pos):
        return any(s <= pos <= e for s, e in tbl_ranges)

    lp_ppr = (
        '<w:pPr>\n'
        '      <w:pStyle w:val="ListParagraph"/>\n'
        '      <w:numPr>\n'
        f'        <w:ilvl w:val="0"/>\n'
        f'        <w:numId w:val="{num_id}"/>\n'
        '      </w:numPr>\n'
        '      <w:spacing w:line="480" w:lineRule="auto" w:before="0" w:after="0"/>\n'
        '      <w:ind w:left="0" w:firstLine="720"/>\n'
        '      <w:contextualSpacing w:val="0"/>\n'
        '      <w:jc w:val="both"/>\n'
        '    </w:pPr>'
    )

    replacements = []
    count = 0

    for pm in paragraphs:
        pxml = pm.group()
        pstart = pm.start()

        if in_table(pstart):
            continue
        if re.search(r'<w:pStyle\s+w:val="Heading\d"', pxml):
            continue
        if '<w:pStyle w:val="DocumentTitle"' in pxml:
            continue
        if '<w:numPr>' in pxml:
            continue

        text_parts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', pxml)
        full_text = ''.join(text_parts).strip()
        if not re.match(r'^\d{1,3}\.', full_text):
            continue
        if len(full_text) < 10:
            continue

        new_pxml = pxml

        prefix_match = re.search(
            r'(<w:lastRenderedPageBreak/>\s*)?'
            r'<w:tab/>'
            r'((?:\s*<w:t[^>]*>[^<]*</w:t>)*?)'
            r'\s*<w:tab/>',
            new_pxml
        )

        if prefix_match:
            between = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', prefix_match.group(2) or '')
            joined = ''.join(between).strip()
            if re.match(r'^\d{1,3}\.$', joined):
                pagebreak = prefix_match.group(1) or ''
                new_pxml = new_pxml[:prefix_match.start()] + pagebreak + new_pxml[prefix_match.end():]
            else:
                continue
        else:
            flat_match = re.search(r'<w:t[^>]*>(\d{1,3}\.\s*)</w:t>(\s*<w:tab/>)?', new_pxml)
            if flat_match:
                new_pxml = new_pxml[:flat_match.start()] + new_pxml[flat_match.end():]
            else:
                continue

        existing_ppr = re.search(r'<w:pPr>.*?</w:pPr>', new_pxml, re.DOTALL)
        if existing_ppr:
            new_pxml = new_pxml[:existing_ppr.start()] + lp_ppr + new_pxml[existing_ppr.end():]
        else:
            p_open = re.match(r'<w:p[^>]*>', new_pxml)
            if p_open:
                ins_pos = p_open.end()
                new_pxml = new_pxml[:ins_pos] + '\n    ' + lp_ppr + new_pxml[ins_pos:]

        replacements.append((pstart, pstart + len(pxml), new_pxml))
        count += 1

    for start, end, new_xml in reversed(replacements):
        doc_xml = doc_xml[:start] + new_xml + doc_xml[end:]

    print(f"  Converted {count} manually-typed numbered paragraph(s) to ListParagraph + numPr")
    return doc_xml


def patch_underscore_signatures(doc_xml):
    """Replace underscore-based certificate signatures with underline+tab technique.

    Detects the pattern:
        <w:p>..._____...</w:p>     (underscore line)
        <w:p>.../s/ Name...</w:p>  (signature)
        <w:p>...Name...</w:p>      (printed name)

    Replaces with a single paragraph using underline+tab+break technique.
    """
    if "Respectfully submitted" not in doc_xml:
        print("  No 'Respectfully submitted' found -- skipping signature patch")
        return doc_xml

    para_pattern = re.compile(r'<w:p[ >].*?</w:p>', re.DOTALL)
    paragraphs = list(para_pattern.finditer(doc_xml))

    end_matter_start = None
    for idx, m in enumerate(paragraphs):
        if "Respectfully submitted" in m.group():
            end_matter_start = idx
            break

    if end_matter_start is None:
        print("  Could not locate Respectfully submitted paragraph -- skipping signature patch")
        return doc_xml

    replacements = []
    i = end_matter_start
    patched_count = 0

    while i < len(paragraphs) - 2:
        p_text = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', paragraphs[i].group())
        p_full = "".join(p_text)

        if re.search(r'_{5,}', p_full):
            next_text_parts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', paragraphs[i + 1].group())
            next_full = "".join(next_text_parts)

            if "/s/" in next_full:
                name_match = re.search(r'/s/\s+(.+)', next_full)
                if name_match:
                    name = name_match.group(1).strip().rstrip(".")

                    printed_text_parts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', paragraphs[i + 2].group())
                    printed_full = "".join(printed_text_parts).strip()

                    replace_count = 2
                    if printed_full and name.lower().startswith(printed_full.lower()[:5]):
                        replace_count = 3

                    replacement = _build_cert_sig_xml(name)

                    start_pos = paragraphs[i].start()
                    end_pos = paragraphs[i + replace_count - 1].end()
                    replacements.append((start_pos, end_pos, replacement))
                    patched_count += 1

                    i += replace_count
                    continue
        i += 1

    for start_pos, end_pos, replacement in reversed(replacements):
        doc_xml = doc_xml[:start_pos] + replacement + doc_xml[end_pos:]

    print(f"  Patched {patched_count} underscore-based signature(s) → underline+tab technique")
    return doc_xml


def _build_cert_sig_xml(name):
    """Build the XML for a correct certificate signature paragraph."""
    return f'''<w:p>
      <w:pPr>
        <w:tabs><w:tab w:val="left" w:pos="9360"/></w:tabs>
        <w:spacing w:line="240" w:before="0" w:after="0"/>
        <w:ind w:left="4680"/>
      </w:pPr>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Century Schoolbook" w:hAnsi="Century Schoolbook"/>
          <w:u w:val="single"/>
        </w:rPr>
        <w:t xml:space="preserve">/s/ </w:t>
      </w:r>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Century Schoolbook" w:hAnsi="Century Schoolbook"/>
          <w:i/>
          <w:iCs/>
          <w:u w:val="single"/>
        </w:rPr>
        <w:t>{name}</w:t>
      </w:r>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Century Schoolbook" w:hAnsi="Century Schoolbook"/>
          <w:u w:val="single"/>
        </w:rPr>
        <w:tab/>
      </w:r>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Century Schoolbook" w:hAnsi="Century Schoolbook"/>
          <w:u w:val="single"/>
        </w:rPr>
        <w:br/>
      </w:r>
      <w:r>
        <w:rPr>
          <w:rFonts w:ascii="Century Schoolbook" w:hAnsi="Century Schoolbook"/>
        </w:rPr>
        <w:t>{name}</w:t>
      </w:r>
    </w:p>'''


def run_core_patches(unpacked_dir, court_type="tx-state"):
    """Run all universal patch operations.

    Args:
        unpacked_dir: Path to unpacked docx directory
        court_type: Court type string (tx-state, ny-state, federal, business) -- passed through for reference

    Returns:
        dict with keys: styles_patched, numbering_patched, document_patched, num_id
    """
    results = {
        'styles_patched': False,
        'numbering_patched': False,
        'document_patched': False,
        'num_id': None,
    }

    styles_path = os.path.join(unpacked_dir, "word", "styles.xml")
    if os.path.exists(styles_path):
        print("Patching styles.xml...")
        with open(styles_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = patch_listparagraph_style(content)
        content, _ = ensure_listparagraph_style(content)
        with open(styles_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("  Done.")
        results['styles_patched'] = True

    numbering_path = os.path.join(unpacked_dir, "word", "numbering.xml")
    num_id = None
    if os.path.exists(numbering_path):
        print("Checking numbering.xml...")
        with open(numbering_path, "r", encoding="utf-8") as f:
            content = f.read()
        content, num_id = ensure_listparagraph_numbering(content)
        with open(numbering_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("  Done.")
        results['numbering_patched'] = True
    else:
        print("  WARN: numbering.xml not found -- creating it")
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        numbering_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{ns}">
</w:numbering>'''
        numbering_xml, num_id = ensure_listparagraph_numbering(numbering_xml)
        os.makedirs(os.path.dirname(numbering_path), exist_ok=True)
        with open(numbering_path, "w", encoding="utf-8") as f:
            f.write(numbering_xml)
        print("  WARN: Created numbering.xml -- verify relationships and content types")
        results['numbering_patched'] = True

    results['num_id'] = num_id

    doc_path = os.path.join(unpacked_dir, "word", "document.xml")
    if os.path.exists(doc_path):
        print("Patching document.xml...")
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        if num_id:
            content = convert_manual_numbers_to_listparagraph(content, num_id)
        content = patch_listparagraph_paragraphs(content)
        content = patch_underscore_signatures(content)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("  Done.")
        results['document_patched'] = True

    return results
