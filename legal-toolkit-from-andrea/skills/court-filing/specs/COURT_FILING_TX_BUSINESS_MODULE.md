# Texas Business Court Module — Non-Caption Content

**Status:** Authoritative for the non-caption portions of a Texas Business Court filing.
**Last updated:** 2026-04-28
**Companion locked spec:** `specs/COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md` (caption-only, governs the table that opens the filing).
**Sibling specs / modules:** `COURT_FILING_TX_STATE_CAPTION_SPEC.md`, `COURT_FILING_FEDERAL_CAPTION_SPEC.md`, core `court-filing/SKILL.md`.
**Retired predecessor:** `(retired supplement)` — the caption section of that file is retired by the locked caption spec; the non-caption content of that file is retired by this module.

This module covers everything in a Business Court filing that is **not** the caption: the global font override, heading style adjustments, signature block treatment, certificate of compliance, and certificate of service language. The base court-filing skill defaults still apply except where this module overrides them.

---

## Scope

Apply this module **in addition to** the locked caption spec and the core court-filing skill, whenever:
- The user identifies the court as the Texas Business Court or "TBC".
- The cause number follows the BC pattern `YY-BCDDP-NNNN`.
- The matter context file identifies the venue as the Texas Business Court.

Build inputs:

| Parameter | Required | Notes |
|---|---|---|
| `courtType` | Yes | `"business"` |
| `courtDivision` | Yes | e.g., `"First Division"`, `"Third Division"`, `"Eighth Division"`, `"Eleventh Division"`. Capitalized words; the validator converts to ALL CAPS for the caption row 3 anchor. |
| `causeNumber` | Yes | BC format: `YY-BCDDP-NNNN`, e.g., `"25-BC00X-0000"`. |

---

## 1. Font override

The Business Court default font size is **14 pt (sz=28) Century Schoolbook**, overriding the base skill's 12 pt (sz=24).

- Applies to the caption (covered by the locked caption spec), body text, headings, certificates, and signature block.
- Does **not** apply to the footer — the footer remains 10 pt per the base skill.
- The build-script constant `SZ` should be set to `28` for `courtType="business"`.
- All `TextRun` instances in the filing inherit `size: 28` unless explicitly overridden (footer, special exhibits).

---

## 2. Signature block — BC-specific overrides

The base court-filing skill (`court-filing/SKILL.md`) defines the signature block container-table layout, the `/s/` underline+tab technique, indent constants, and the attorney info block. BC filings inherit all of that and add only:

- **Firm name** (`[Firm Name from profile]`): bold + small caps (`bold: true, smallCaps: true`).
- **`ATTORNEYS FOR [CLIENT]` line**: bold (`bold: true`).

Attorney name styling, container table dimensions, and the `/s/` line technique all follow the base skill — no override.

> Heading style (Heading 1 centering, suffix "space", `spaceAfter=240`) and the certificate signature underline+tab technique are defined globally in `court-filing/SKILL.md` and apply to BC unchanged. Not restated here.

---

## 3. Certificate of Compliance — required

All motions, responses, and replies filed in the Texas Business Court must include a Certificate of Compliance with a word count, per TBC Local Rules. Discovery motions are capped at **3,000 words**. Other motions follow the applicable cap.

```
CERTIFICATE OF COMPLIANCE

    I certify that this [document type] complies with the word limits in the
Texas Business Court Local Rules. This [document type] contains [WORD COUNT]
words, excluding the parts exempted by the Texas Business Court Local Rules.

                              /s/ [Signing Attorney Name]____________
                              [Signing Attorney Name]
```

Build defaults:
- Set `includeCOC = true` for all BC motions, responses, and replies.
- The signature uses the underline+tab technique defined in `court-filing/SKILL.md`.
- The Certificate of Compliance appears **after** the Certificate of Conference (when applicable) and **before** the Certificate of Service.

---

## 4. Certificate of Service — eFileTexas

The Business Court uses electronic filing through eFileTexas. The COS language replaces the state court Rule 21a language and the federal CM/ECF language:

> "I hereby certify that on [date], I electronically filed the foregoing with the Clerk of the Business Court of Texas by using the eFileTexas system which will send a notice of electronic filing to all counsel of record."

Build defaults: `cosMethod = "eFileTexas"` for `courtType="business"`.

---

## 5. Certificate defaults summary

| Parameter | Default for `courtType="business"` |
|---|---|
| `cosMethod` | `eFileTexas` (see Section 4 language) |
| `includeCOC` | `true` for all motions, responses, and replies |
| `certSigTechnique` | Underline + tab single-paragraph (defined in `court-filing/SKILL.md`) |

All other certificate defaults (Certificate of Conference, signers) follow the base court-filing skill.

---

## 6. Validation

Run with:

```
python <court-filing-skill>/scripts/validate_court_filing.py output.docx --spacing double --court-type business
```

The `--court-type business` flag enables BC-specific checks:

- Caption checks (delegated to the locked caption spec — see `COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md` Section "Validator checks").
- Font size: 14 pt (sz=28) throughout body, headings, certificates, and signature block; 10 pt for footer.
- Signature block: firm name bold + smallCaps; "ATTORNEYS FOR" bold.
- Certificate of Compliance: present when `includeCOC=true` (default for motions); contains a word count.
- Certificate of Service: contains the eFileTexas language from Section 4 verbatim (modulo bracket substitutions).

Cross-court checks (heading spacing, Heading 1 centering, certificate signature underline+tab technique) run for all court types and are not BC-specific — see `court-filing/SKILL.md`.

---

## 7. Cross-references

- **Locked caption spec:** `specs/COURT_FILING_TX_BUSINESS_CAPTION_SPEC.md`
- **Base court-filing skill:** `.claude/skills/court-filing/SKILL.md`
- **Consolidated validator:** `.claude/skills/court-filing/scripts/validate_court_filing.py` (accepts `--court-type business`)
- **Patch script:** `.claude/skills/court-filing/scripts/patch_court_filing.py` (ListParagraph numPr + signature line; runs for all court types)
- **ListParagraph supplement:** (internal build reference — not needed to use this skill) (applies to BC filings too)
- **Global formatting preferences:** (internal build reference — not needed to use this skill)

---

## 8. Source authority

The non-caption rules in this module are unchanged from the original `(retired supplement)` and have been confirmed against published Business Court filings (CreateAI v. Bot Auto, Energy Transfer v. Culberson, Crain v. Northern). The 14 pt font override and signature-block formatting are observed conventions in those filings; the Certificate of Compliance and eFileTexas service language reflect the TBC Local Rules.
