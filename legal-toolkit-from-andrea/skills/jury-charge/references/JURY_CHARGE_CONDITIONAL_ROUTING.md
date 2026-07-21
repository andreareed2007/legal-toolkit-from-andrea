# Jury Charge Conditional Routing — The Dependency-Graph Model

**Companion doc:** `JURY_CHARGE_WORKFLOW.md`
**Implemented by:** `scripts/routing_map.py` in the `jury-charge` skill.

## The problem

A jury charge is not a linear document — it is a directed graph. Each answer determines which questions the jury reaches next. The pattern instructions supply the building blocks (questions, instructions, definitions) but say little about wiring them together for a multi-party, multi-claim case ("submit broad-form whenever feasible" and "predicate appropriately" is roughly the entire guidance). The routing layer is largely the drafter's responsibility, and it is where the most dangerous errors live: a jury that answers the wrong questions because of a routing mistake can produce a verdict that is reversible on appeal.

Hand-maintained routing fails for a mechanical reason: it requires holding the full claim/defense/party matrix in working memory, and a single stale question number cascades silently. A charge with a routing instruction after Question 15 that still references "Question No. 1" is exactly this defect class. The fix is structural: **the routing map is data; the routing prose is generated; the generated prose is machine-checked against the map.**

## Core principle: stable keys, disposable numbers

Nodes are identified by stable string keys (`gc_acme_boc`), never by question numbers. Question numbers are assigned by the renderer at output time from the declared node order. Add or remove a question and every number and every routing instruction is re-derived from scratch — there is nothing to manually update, so there is nothing to miss.

## Node types

| Type | Meaning | Typical predicate |
|---|---|---|
| `liability` | Did party A prove its claim against party B? | Usually none (entry point), or an ALTERNATIVE_TO chain |
| `defense` | Is B's liability barred/excused on this claim? | "Yes" on the liability node it defends against |
| `damages` | What sum compensates A for this claim? | "Yes" on the liability node; skipped if a BLOCKS defense was found |
| `exemplary_predicate` | Clear-and-convincing finding (fraud/malice/gross negligence), unanimity required | "Yes" on the underlying liability node(s) |
| `exemplary` | Amount of exemplary damages, unanimity required | Unanimous "Yes" on the exemplary_predicate node |
| `fees` | Attorney's fees, with conditional appellate tiers | "Yes" on the fee-bearing liability node(s) |
| `instruction_only` | Instruction with no answer blank (limiting/inferential-rebuttal) | Rendered in place, no routing |

## Edge semantics

**`predicated_on`** — the question is answered only if the source node got a specific answer.

```json
"predicated_on": [
  { "source": "gc_acme_fraud", "answer": "Yes", "scope": "any_party", "unanimous": false }
]
```

- `scope: "any_party"` — per-party question; predicate fires if ANY listed party was found (renders "regarding any defendant... regarding that defendant").
- `scope: "same_party"` — predicate evaluated per-party (damages per defendant follow the finding per defendant).
- `scope: "all"` — single-answer question; plain "If you answered 'Yes' to Question No. X."
- `unanimous: true` — the predicate requires a unanimous finding (exemplary track, e.g., Tex. Civ. Prac. & Rem. Code ch. 41 where it governs).
- Multiple entries in the list are OR'd unless `"logic": "AND"` is set on the node (e.g., damages reached from either of two liability theories).

**`alternative_to`** — answered only if the primary theory failed. Encodes the breach → quantum meruit → unjust enrichment ladder: the alternative is reached on "No" (or unanswered-because-skipped) of the primary. Alternatives of alternatives chain.

**`blocks`** — a found defense cuts off recovery: if the defense node is answered "Yes," the listed damages node(s) are skipped even though the liability predicate fired. The renderer emits the compound instruction ("If you answered 'Yes' to Question No. L and 'No' to Question No. D, then answer...").

## Charge map schema (input to routing_map.py)

```json
{
  "matter": "Good Client, Inc. v. ACME Corp.",
  "court_type": "tx-state",
  "posture": "requested",
  "parts": [ { "key": "A", "title": "GOOD CLIENT'S CLAIMS AGAINST ACME" } ],
  "nodes": [
    {
      "key": "gc_acme_boc",
      "part": "A",
      "type": "liability",
      "label": "Breach of Contract",
      "asserting": "Good Client",
      "against": ["ACME Corp."],
      "per_party_answers": false,
      "claim_id": "BREACH_OF_CONTRACT_ONE_SIDE",
      "pjc": ["101.2A"],
      "statute": null,
      "predicated_on": [],
      "alternative_to": [],
      "blocks": [],
      "notes": "Variant call per ATTORNEY JUDGMENT flag: one-side breach → 101.2A."
    }
  ]
}
```

Field notes: `pjc` (or the jurisdiction-neutral `sources`) lists every section the node's text draws from (question + instructions + definitions) — each must appear in the Phase 2 lookup ledger. `statute` replaces the section list when no pattern exists; the renderer footnotes "No pattern exists; the instruction tracks the statutory elements." `per_party_answers: true` renders one answer blank per party in `against`.

## Validation checks (ERROR = must fix; WARNING = must justify)

Justification mechanism: once a human reason is recorded in a node's `notes` containing the prefix `Justified:`, checks 5 and 10 stop warning for that node. The justification text should say WHY the structure is intentional (e.g., "Justified: conspiracy recovers through the underlying tort damages"). Never add the prefix without a reason — it is an attestation, not a mute button.

1. **Unknown reference** (ERROR) — a `predicated_on.source`, `alternative_to`, or `blocks` key that matches no node.
2. **Cycle** (ERROR) — circular predication.
3. **Dead-end predicate** (ERROR) — predicate on a node type that has no Yes/No answer (e.g., predicated on a damages node's amount, or on `instruction_only`).
4. **Damages without liability** (ERROR) — a `damages` node with no liability in its predicate chain.
5. **Liability without damages** (WARNING) — a `liability` node no damages node depends on. Sometimes correct (civil conspiracy routes through the underlying tort's damages, which flags exactly this on the conspiracy node) — justify in `notes`.
6. **Exemplary chain** (ERROR) — `exemplary` not predicated (unanimous) on an `exemplary_predicate`; `exemplary_predicate` not predicated on a liability node.
7. **Blocks target** (ERROR) — `blocks` pointing at a non-damages node.
8. **Alternative target** (ERROR) — `alternative_to` pointing at a non-liability node.
9. **Scope mismatch** (ERROR) — `scope: any_party/same_party` predicating on a node with `per_party_answers: false`.
10. **Orphaned defense** (WARNING) — a `defense` node whose finding blocks nothing and predicates nothing.
11. **Unreachable node** (ERROR) — a predicated node whose predicate chain can never fire (e.g., predicated on "No" of a node that, when "No," is itself never reached).

## Render + crosscheck

`render` performs a stable topological sort within each part (declared order preserved among independents), assigns question numbers, and emits: the question sequence with routing instructions, per-part headers, and the footnote source list. `crosscheck` parses every "If you answered ... Question No. X ..." sentence in a drafted charge document and verifies each against the graph — source number, target number, answer, and scope — reporting a TOTALS line (checked/matched/mismatched). Any mismatch means the prose was hand-edited or the map changed after rendering; re-render, never hand-fix.

`audit` produces the standalone HTML routing-audit deliverable: every node, its predication chain, every path traced to termination, and the full validation report. This ships with every charge draft.

## Worked example — one slice

```
LIABILITY gc_acme_boc (Q: Did ACME fail to comply with the Contract?)
  ├─ Yes → DEFENSE acme_waiver_amd (waiver via the Amendment § 5)
  │         ├─ Yes → [BLOCKS] skip damages, proceed to next claim
  │         └─ No  → DAMAGES gc_acme_boc_dmg (115-series contract damages)
  └─ No  → next claim

LIABILITY gc_acme_fraud (broad-form: misrepresentation OR nondisclosure, 105 practice)
  ├─ Yes (any def.) → DAMAGES gc_acme_fraud_dmg
  │                  → EXEMPLARY_PREDICATE (unanimous, clear and convincing)
  │                        └─ Unanimous Yes → EXEMPLARY (per defendant)
  └─ No → next claim
```

In map form, `acme_waiver_amd` is `predicated_on: [{source: "gc_acme_boc", answer: "Yes", scope: "all"}]` with `blocks: ["gc_acme_boc_dmg"]`, and `gc_acme_boc_dmg` is `predicated_on` the liability node — the renderer combines the two into the compound conditional automatically.
