# Tanbook Citation Rules (NY Law Reports Style Manual, 2022 ed.)

Authoritative distillation of the citation rules from the *New York Law Reports
Style Manual* (2022), sections 1.0-4.0. Section numbers below (e.g. 2.2 [a] [7])
refer to the manual. This file is the rule source of truth for the engine in
`scripts/tanbook.py`. When the engine and this file disagree, this file wins.

> Governing mandate: "New York decisions shall be cited from the official
> reports, if any" (CPLR 5529 [e]); NY Official citations "shall be included, if
> available" (Rules of Ct of Appeals [22 NYCRR] 500.1 [g]).

---

## 1. Signature Tanbook differences from the Bluebook

These are the transforms that matter most when converting Bluebook -> Tanbook.

1. NO PERIODS in NY reporter abbreviations: NY, NY2d, NY3d, AD, AD2d, AD3d,
   App Div, Misc, Misc 2d, Misc 3d, NYS, NYS2d, NYS3d. Never N.Y.3d, A.D.3d.
2. Series number closes up to the reporter, no space: NY3d, AD3d, NYS2d, NE2d,
   F3d, F4th. BUT Misc keeps a space before the series: Misc 2d, Misc 3d (and
   App Div, App Term are spaced words).
3. Court + jurisdiction + year go in SQUARE BRACKETS, not parens: [2021],
   [1st Dept 2020], [Sup Ct, Kings County 2021]. The single most visible quirk.
4. "v" with NO period in case names: People v Wilkins, not People v.
5. Statutory subdivisions go in brackets: Penal Law 125.25 [1] [a],
   CPLR 5602 [b] [2] [iii]. (In running text they convert to parens.)
6. NO parallel unofficial citations for officially reported NY cases
   (2.2 [b] [1]). A NY case in NY3d/AD3d/Misc 3d is cited to the official
   report ONLY -- do NOT add the NYS2d or NE2d parallel. (Parallels are for
   out-of-state cases and US Supreme Court when US Reports is unavailable.)
7. NO comma between a signal and the citation: see Dalton v Pataki.
8. Page ranges are not truncated: 351-352, not 351-52; 125.21-125.25.

---

## 2. Case citation anatomy

Order (within-parentheses display, the manual default):

  (Case Name, VOL REPORTER PAGE[, PINPOINT] [COURT JURISDICTION YEAR][, history])

### 2.1 Basic forms (2.2 [a] [1])
- Court of Appeals: (Cayuga Nation v Campbell, 34 NY3d 282 [2019])
- Appellate Division: (Matter of Cornell Univ. v Beer, 16 AD3d 890 [3d Dept 2005])
- Trial / Misc: (Matter of DeOca, 75 Misc 3d 449 [Sur Ct, Erie County 2022])

### 2.2 Pinpoint pages (2.2 [a] [2])
- (People v Ramos, 90 NY2d 490, 495 [1997])
- If pinpoint = first page, repeat it: (275 AD2d 207, 207 [1st Dept 2000])
- Multi-page quotation uses a hyphen, untruncated: 91 NY2d 306, 316-317 [1997].

### 2.3 Footnotes in cited case (2.2 [a] [3])
- Sole footnote: 35 NY3d 75, 81 n [2020]
- Numbered: 100 NY2d 159, 168 n 3 [2003]
- Multiple: 253 AD2d 22, 25 nn 2, 3 [3d Dept 1999]
- Page + footnote: 60 AD3d 226, 229-230, 230 n 3 [1st Dept 2009]; 11 NY3d 223, 242 & n 10 [2008]

---

## 3. The bracketed parenthetical: court, jurisdiction, year (2.2 [a] [7])

Add court, jurisdiction, year in brackets. OMIT any element the reporter already
makes redundant. Key generation rule:

- Court of Appeals -- reporter NY/NY2d/NY3d implies it: bracket is YEAR ONLY: [2019].
- Appellate Division -- reporter AD/AD2d/AD3d/App Div implies it: DEPARTMENT + YEAR:
  [1st Dept 2020], [3d Dept 2005], [4th Dept 2019]. Departments ordinal: 1st, 2d, 3d, 4th.
- Appellate Term -- [App Term, 2d Dept, 9th & 10th Jud Dists 2020].
- Trial courts (Misc reporter) -- COURT, COUNTY, YEAR: [Sup Ct, Kings County 2021],
  [Civ Ct, Richmond County 2003], [Sur Ct, Erie County 2022], [Crim Ct, NY County 2011],
  [Nassau Dist Ct 2019].

### Court abbreviation table (2.2 [a] [7]) -- verbatim
Appellate Division -> App Div
Chancery Court -> Ch Ct
City Court -> [city] City Ct
Civil Court of the City of New York -> Civ Ct, [county] County
County Court -> [county] County Ct
Court of Appeals (Federal) -> [circuit] Cir
Court of Appeals (State) -> Ct App
Court of Claims -> Ct Cl
Criminal Court of the City of New York -> Crim Ct, [county] County
Department -> [number] Dept
District Court (Federal) -> D [forum] (e.g. SD NY, ED Wis, D Kan, ND Ind)
District Court (State) -> [Nassau or Suffolk] Dist Ct, [number] Dist
Drug Treatment Court -> Drug Treatment Ct
Family Court -> Fam Ct, [county] County
General Term -> Gen Term
Housing Part -> Hous Part
Judicial Districts -> [numbers] Jud Dists
Justice Court -> [town/village] Just Ct
Police Court -> Police Ct
Superior Court -> Super Ct
Supreme Court (Federal) -> US
Supreme Court (State) -> Sup Ct, [county] County
Supreme Court, Appellate Term -> App Term, [dept] Dept
Surrogate's Court -> Sur Ct, [county] County

### Optional info (include only if desired)
- Precise date + judge: [Feb. 18, 2021, DiFiore, Ch. J.]
- Dissent: [1st Dept 2018, Gische, J., dissenting]
- Decision type after year: [2006 plurality], [2d Dept 2006 mem], [3d Dept 2007 per curiam]

---

## 4. Appellate history (2.2 [a] [5])

Append after the bracket, comma-separated; history abbreviation italicized in
print (engine emits plain text). Only PERTINENT history need be included.

- (Flores v Lower E. Side Serv. Ctr., 3 AD3d 459 [1st Dept 2004], revd 4 NY3d 363 [2005])
- (Garden Homes Woodlands Co. v Town of Dover, 95 NY2d 516 [2000], revg 266 AD2d 187 [2d Dept 1999])
- (Matter of Carr v de Blasio, 197 AD3d 124 [1st Dept 2021], affg 70 Misc 3d 418 [Sup Ct, NY County 2020])
- Multi-step: (257 App Div 465 [2d Dept 1939], revd 284 NY 13 [1940], revd 313 US 221 [1941])

History abbreviations seen: affd, affg, revd, revg, mod, modfg, lv denied,
lv granted, lv dismissed, lv dismissed & denied, appeal dismissed, rearg denied,
cert denied, certs denied, affd on other grounds, affd without op,
affd for reasons stated below, affd on concurring op of [Judge], J.
(Full list = Appendix 3, not extracted; do not invent abbreviations, flag unknowns.)

sub nom. -- use when one party name changes on appeal. Do NOT use when
People/State changes to "New York". Not needed for cert denials.

---

## 5. Slip opinions and unreported NY cases (2.2 [a] [8], [b])

NY electronic citation system -- no Bluebook analog.

- Scheduled for print (report number not yet known): blank reporter with
  em-dashes + Slip Op number:
  (People v Daly, - Misc 3d -, 2011 NY Slip Op 21371 [Crim Ct, NY County 2011])
  (Tkeshelashvili v State of New York, - NY3d -, 2011 NY Slip Op 08451 [2011])
  Pinpoint star: (People v Burgos, - NY3d -, -, 2022 NY Slip Op 01868, *3 [2022])
- Unreported WITH abstract -- parallel Misc 3d abstract [A] + Slip Op [U]:
  (Matter of Lee v Chin, 1 Misc 3d 901[A], 2003 NY Slip Op 51455[U] [Sup Ct, NY County 2003])
  Pinpoint: ..., 2003 NY Slip Op 51455[U], *9 [...]; star ranges *1-3; lists *1, *3
- Unreported NO abstract -- Slip Op only:
  (Hwang v Cunningham, 2011 NY Slip Op 33038[U] [Sup Ct, Nassau County 2011])
  Short form: (Hwang, 2011 NY Slip Op 33038[U], *2) or (Hwang at *2) or (id. at *2)
- Unreported appellate motion: (Blair v Pierre, 2006 NY Slip Op 78812[U] [2d Dept 2006])
- Slip Op markers [A] and [U] are literal, attach with no space: 901[A], 51455[U].
- Short form repeats the Slip Op number, not "at *7" alone:
  (Lee, 2003 NY Slip Op 51455[U], *7) NOT (Lee at *7).

---

## 6. Short forms, id., signals (1.3, 1.4)

- Short-form case name = first nongovernmental party: Krom for People v Krom.
- Do NOT use supra to shorten a case/statute cite (1.3 [b] [2]). supra/infra are
  internal cross-references only (12.5).
- Shortened forms: (Matter of Murphy, 6 NY3d at 43), (Murphy, 6 NY3d 36),
  (Murphy at 43), (6 NY3d at 43), (Murphy).
- id. for immediately preceding authority: (id.), (id. at 495), (id. at *2),
  (id. 468-a). Capitalize Id. when first term in a citational sentence.
- Subsequent parallel cites: supply pinpoint for each; do not use "id. at" with
  parallel cites. (Verity, 164 Idaho at 842, 436 P3d at 663).
- Signals (no comma after; italic in print): e.g., see, but see, cf., but cf.,
  accord, see also, compare ... with ..., see e.g., but see e.g., see generally,
  contra, compare ... and ... with .... Do not italicize a signal used as the verb.

---

## 7. Federal & out-of-state cases (2.3)

- US Supreme Court: US Reports -- (Ohralick v Ohio State Bar Assn., 436 US 447 [1978]).
  If US cite unavailable, blank US + parallel S Ct or L Ed:
  (Hemphill v New York, 595 US -, -, 142 S Ct 681, 689 [2022]).
- Other federal: (Chrysafis v Marks, 15 F4th 208 [2d Cir 2021]),
  (United States v Seltzer, 227 F3d 36 [2d Cir 2000]),
  (Schultz v Frisby, 619 F Supp 792 [ED Wis 1985]),
  (Mavrovich v Vanderpool, 427 F Supp 2d 1084 [D Kan 2006]).
  Federal reporters period-free: F2d, F3d, F4th, F Supp, F Supp 2d, F Supp 3d, Fed Appx, BR.
- Out-of-state: official state report + parallel National Reporter cite --
  (Metcalf v Fitzgerald, 333 Conn 1, 214 A3d 361 [2019]). NY DOES want a parallel
  here. Regional reporters period-free: A3d, NW2d, SW3d, P3d, NE2d, So 3d.
  National-only -> add jurisdiction in brackets: (Brinker v First Natl. Bank,
  37 SW2d 136 [Tex Commn App 1931]).
- Public domain / medium-neutral precedes any parallel when adopted as official:
  (Smith v Rebsamen Med. Ctr., Inc., 2012 Ark 441, 424 SW3d 876 [2012]).

---

## 8. NY statutes (3.1)

- Do NOT abbreviate statute names unless the abbreviation is in Appendix 4
  (Vehicle and Traffic Law 1192, not VTL 1192). Use designated abbreviations in
  parentheticals (CPLR, CPL, ECL, RPTL, EPTL, etc.).
- Subdivisions in BRACKETS within parentheses: (Penal Law 125.25 [1] [a]),
  (CPLR 5602 [b] [2] [iii]), (Domestic Relations Law 236 [B] [6] [b] [4]).
- Section-symbol-LESS statutes (cite section number with no section sign):
  CPL, CPLR, ECL, EDPL, EPTL, N-PCL, PRHPL, RPAPL, RPTL, SCPA, UCC, UCCA, UDCA,
  UJCA. So CPLR 3211 [a] [7], CPL 30.20 [2] -- NOT CPLR sec 3211.
- All other NY statutes USE the section sign: Penal Law 125.25, Town Law 199.
- Multiple sections: two section symbols before first -- (Town Law 199 [1]; 200).
- Parallel hierarchy (same rank) separated by COMMA: (Penal Law 125.25 [1] [a], [b]).
  Ascending hierarchy (more inclusive follows) separated by SEMICOLON:
  (Town Law 199 [1] [a]; [3]).
- Session laws: abbreviate "Laws" to L only here -- L 2021, ch 417.
- Running text converts subdivision brackets to parens and uses the statute's
  own division terminology.
- Former statutes: (former Penal Law 221.10 [2]); (former Penal Law 221.10,
  repealed by L 2021, ch 92, sec 15).

---

## 9. Placement & punctuation (1.2)

- Default display = "citation within parentheses".
- Final period: inside the closing paren only when the parenthetical relates to
  more than one preceding sentence; otherwise period follows the closing paren.
- Citational footnote style (1.2 [d]-[e]): a footnote containing only a citation
  drops the outer parens and CONVERTS INTERNAL BRACKETS TO PARENS:
  Solomon v State of New York, 146 AD2d 439, 440 (1st Dept 1989). This bracket
  to paren flip also applies to statutory subdivisions and running-text conversions.

---

## 10. Engine scope & known gaps

Engine (scripts/tanbook.py) reliably handles, for within-parentheses style:
- De-periodizing NY + federal + regional reporter abbreviations.
- Closing reporter/series spacing (NY 3d -> NY3d); preserving Misc 2d / App Div / App Term.
- Bluebook (...) date/court -> Tanbook [...] brackets.
- v. -> v; page-range de-truncation (316-17 -> 316-317).
- Flagging improper NYS2d/NE2d parallels on officially-reported NY cases.
- Flagging slip-op, statute-bracket, and signal-comma issues in validation mode.

Deferred / model-judgment (not pure-deterministic):
- Generating a department from a county (NY judicial-department map -- starter subset, verify).
- Choosing which appellate history is "pertinent".
- Appendix 1 case-name word abbreviations and Appendix 3/4 tables -- NOT fully
  extracted; engine ships a starter subset and flags unknowns rather than guessing.
- Assembling missing parallel cites for out-of-state cases -- enrich mode via
  CourtListener, not the offline engine.
