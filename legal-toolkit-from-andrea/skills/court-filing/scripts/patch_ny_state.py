#!/usr/bin/env python3
"""
New York Supreme Court-specific patch operations.

Injects NY caption styles into styles.xml if missing:
- Parties, PartyType, versus, CaseNo, PleadingTitle, PLDCaption2, CaseCaptionBottom, border

These styles are defined in modules/STATE-NY.md and are required for proper
NY caption table formatting.
"""
import os
import re


def inject_ny_caption_styles(styles_xml):
    """Inject NY caption styles into styles.xml if they're missing.

    Returns (styles_xml, count_injected) where count_injected is the number
    of styles that were added.
    """
    # Define the complete NY caption styles
    ny_styles = [
        # Parties -- party names in caption
        '''  <w:style w:type="paragraph" w:styleId="Parties">
    <w:name w:val="Parties"/>
    <w:pPr>
      <w:spacing w:after="240" w:line="240" w:lineRule="auto"/>
      <w:jc w:val="left"/>
      <w:suppressAutoHyphens w:val="1"/>
    </w:pPr>
  </w:style>''',

        # PartyType -- "Plaintiffs," / "Defendants."
        '''  <w:style w:type="paragraph" w:styleId="PartyType">
    <w:name w:val="Party Type"/>
    <w:pPr>
      <w:spacing w:line="240" w:lineRule="auto"/>
      <w:ind w:left="1440"/>
      <w:jc w:val="left"/>
      <w:suppressAutoHyphens w:val="1"/>
    </w:pPr>
  </w:style>''',

        # versus -- "v."
        '''  <w:style w:type="paragraph" w:styleId="versus">
    <w:name w:val="versus"/>
    <w:pPr>
      <w:spacing w:after="240" w:line="240" w:lineRule="auto"/>
      <w:ind w:left="720"/>
      <w:jc w:val="left"/>
    </w:pPr>
  </w:style>''',

        # CaseNo -- "INDEX NO.: ________"
        '''  <w:style w:type="paragraph" w:styleId="CaseNo">
    <w:name w:val="CaseNo"/>
    <w:pPr>
      <w:spacing w:after="240" w:line="240" w:lineRule="auto"/>
      <w:ind w:left="144"/>
      <w:jc w:val="left"/>
      <w:suppressAutoHyphens w:val="1"/>
    </w:pPr>
    <w:rPr>
      <w:caps/>
    </w:rPr>
  </w:style>''',

        # PleadingTitle -- document title in right column
        '''  <w:style w:type="paragraph" w:styleId="PleadingTitle">
    <w:name w:val="Pleading Title"/>
    <w:pPr>
      <w:spacing w:line="240" w:lineRule="auto"/>
      <w:ind w:left="144"/>
      <w:jc w:val="left"/>
      <w:suppressAutoHyphens w:val="1"/>
    </w:pPr>
    <w:rPr>
      <w:b/>
      <w:caps/>
    </w:rPr>
  </w:style>''',

        # PLDCaption2 -- spacer paragraphs in right column
        '''  <w:style w:type="paragraph" w:styleId="PLDCaption2">
    <w:name w:val="PLD Caption 2"/>
    <w:pPr>
      <w:spacing w:line="240" w:lineRule="auto"/>
      <w:ind w:left="144"/>
      <w:jc w:val="left"/>
      <w:contextualSpacing w:val="1"/>
    </w:pPr>
  </w:style>''',

        # CaseCaptionBottom -- closing paragraph in left column
        '''  <w:style w:type="paragraph" w:styleId="CaseCaptionBottom">
    <w:name w:val="Case Caption Bottom"/>
    <w:pPr>
      <w:spacing w:line="240" w:lineRule="auto"/>
      <w:ind w:left="-86"/>
      <w:jc w:val="left"/>
    </w:pPr>
  </w:style>''',

        # border -- empty paragraph in center divider cell
        '''  <w:style w:type="paragraph" w:styleId="border">
    <w:name w:val="border"/>
    <w:pPr>
      <w:spacing w:line="240" w:lineRule="auto"/>
      <w:jc w:val="left"/>
      <w:suppressAutoHyphens w:val="1"/>
    </w:pPr>
  </w:style>''',
    ]

    style_ids = ["Parties", "PartyType", "versus", "CaseNo", "PleadingTitle", "PLDCaption2", "CaseCaptionBottom", "border"]
    count_injected = 0

    for style_id, style_def in zip(style_ids, ny_styles):
        if f'w:styleId="{style_id}"' not in styles_xml:
            if '</w:styles>' in styles_xml:
                styles_xml = styles_xml.replace('</w:styles>', style_def + '\n</w:styles>')
                count_injected += 1
                print(f"  Injected NY caption style: {style_id}")

    return styles_xml, count_injected


def run_ny_patches(unpacked_dir):
    """Run New York-specific patch operations.

    Args:
        unpacked_dir: Path to unpacked docx directory

    Returns:
        dict with keys: styles_patched, styles_count
    """
    results = {
        'styles_patched': False,
        'styles_count': 0,
    }

    styles_path = os.path.join(unpacked_dir, "word", "styles.xml")
    if os.path.exists(styles_path):
        print("Patching styles.xml (NY caption styles)...")
        with open(styles_path, "r", encoding="utf-8") as f:
            content = f.read()
        content, count = inject_ny_caption_styles(content)
        with open(styles_path, "w", encoding="utf-8") as f:
            f.write(content)
        if count > 0:
            print(f"  Done. Injected {count} NY caption style(s).")
            results['styles_patched'] = True
            results['styles_count'] = count
        else:
            print("  Done. All NY caption styles already present.")

    return results
