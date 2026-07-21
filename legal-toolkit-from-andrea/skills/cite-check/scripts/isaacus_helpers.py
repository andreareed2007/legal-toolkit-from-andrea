"""
S3 + S5 -- Isaacus SDK wrapper module (Project: Isaacus Integration, Plan v3).

Thin normalizing layer around the five SDK resources plus the text2markdown
library:

    * rerank(query, texts, top_n=None)              -> list of dicts
    * verify(proposition, text)                     -> dict
    * classify(query, texts, is_iql=True)           -> list of dicts
    * embed(texts, task="retrieval/document")       -> list of list[float]
    * enrich(texts)                                 -> list of dicts
    * to_markdown(text, **flags)                    -> str

Design contract:
    * Every function catches SDK exceptions and returns None (or [] for
      list-returning functions) on failure.  Skills are expected to check
      for falsy returns and degrade gracefully.
    * Every function accepts an optional ``client`` argument so callers
      can pass a shared client or inject a mock for testing.
    * Return shapes are dicts with stable keys; they do NOT expose raw
      SDK response objects.  Upstream API changes should touch this file
      and no others.

Credential discipline is enforced by isaacus_config.get_client().  No
credential ever passes through this module's code paths.

Model defaults are pinned here so skills do not hard-code model names.
Update this module (and the plan) when Isaacus releases a new model.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional, Sequence

from isaacus_config import get_client

# Pinned model names (plan v3, section 2).
MODEL_RERANKER = "kanon-2-reranker"
MODEL_ANSWER = "kanon-answer-extractor"
MODEL_CLASSIFIER = "kanon-universal-classifier"
MODEL_EMBEDDER = "kanon-2-embedder"
MODEL_ENRICHER = "kanon-2-enricher"


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------
def _client(client):
    """Resolve a client: use provided, else lazily construct one."""
    return client if client is not None else get_client()


def _safe(fn: Callable[[], Any], fallback: Any) -> Any:
    """Run fn(); on any exception return fallback.

    Swallows all exceptions from the SDK deliberately.  Skills that need
    error diagnostics should call the raw SDK directly, not this wrapper.
    The wrapper's job is graceful degradation.
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001 - broad by design; see docstring
        return fallback


# --------------------------------------------------------------------------
# rerank (C4)
# --------------------------------------------------------------------------
def rerank(
    query: str,
    texts: Sequence[str],
    top_n: Optional[int] = None,
    *,
    scoring_method: Optional[str] = None,
    client=None,
) -> List[dict]:
    """Score and reorder ``texts`` against ``query``.

    Returns a list of dicts ordered best-first:
        {"index": int, "score": float, "text": str}

    ``index`` points back into the original ``texts`` sequence so callers
    can recover their own objects (e.g., CourtListener opinion records).

    Empty list on failure or empty input.
    """
    if not texts:
        return []

    def _call():
        cli = _client(client)
        kwargs = {
            "model": MODEL_RERANKER,
            "query": query,
            "texts": list(texts),
        }
        if top_n is not None:
            kwargs["top_n"] = top_n
        if scoring_method is not None:
            kwargs["scoring_method"] = scoring_method
        resp = cli.rerankings.create(**kwargs)
        out = []
        for r in resp.results:
            idx = getattr(r, "index", None)
            score = getattr(r, "score", None)
            if idx is None or score is None:
                continue
            out.append({
                "index": idx,
                "score": float(score),
                "text": texts[idx] if 0 <= idx < len(texts) else "",
            })
        out.sort(key=lambda d: -d["score"])
        return out

    return _safe(_call, [])


# --------------------------------------------------------------------------
# verify (C1)
# --------------------------------------------------------------------------
def verify(proposition: str, text: str, *, client=None,
           chunking_options: Optional[dict] = None,
           scoring_method: str = "auto") -> Optional[dict]:
    """Run universal classification to test whether ``text`` supports ``proposition``.

    Returns:
        {
            "passage": str,                  # top-scoring chunk text (evidence)
            "score": float,                  # 0-1 support probability
            "inextractability_score": float, # 1 - score; high = NOT supported
            "span": (int, int) | None,       # char offsets of top chunk in `text`
            "supports": bool,                # score > 0.5
        }
    None on failure.

    Uses ``kanon-universal-classifier``, which is the endpoint Isaacus
    designed for zero-shot "is this statement supported by this document"
    questions.  The returned ``score`` is a probability in [0, 1], and
    >0.5 is a positive classification per the Isaacus docs.

    An earlier implementation of this function used extractive QA
    (``kanon-answer-extractor``) with a ``score > 0.3`` threshold.  That
    was wrong: extractive QA returns a span-selection probability that
    is distributed across all candidate spans in the passage and is not
    commensurate with a support-confidence threshold -- even a correct
    extraction typically scores well below 0.3.  The universal classifier
    returns a single 0-1 support probability and is the correct endpoint.

    ``inextractability_score`` is preserved in the return shape for
    backward compatibility with downstream callers (cite_check.py,
    cite_check_report.py) and is computed as ``1 - score`` so that its
    semantics ("high value = NOT supported") carry over cleanly.
    """
    if not text or not proposition:
        return None

    def _call():
        cli = _client(client)
        resp = cli.classifications.universal.create(
            model=MODEL_CLASSIFIER,
            query=proposition,
            is_iql=False,
            texts=[text],
            # Phase 4 (2026.07.04): a modest overlap stops evidence passages
            # from being split at chunk boundaries (audit Part 4); "auto"
            # lets the API pick the scoring method. Both are ADDITIVE --
            # the >0.5 supports rule and return shape are unchanged.
            scoring_method=scoring_method,
            chunking_options=(chunking_options if chunking_options is not None
                              else {"overlap_ratio": 0.15}),
        )
        if not resp.classifications:
            return None
        c = resp.classifications[0]
        score = float(getattr(c, "score", 0.0) or 0.0)
        chunks = getattr(c, "chunks", None) or []
        if chunks:
            # Docs say the overall score equals the max chunk score; sort
            # defensively in case chunk order is not guaranteed.
            top = max(chunks, key=lambda ch: float(getattr(ch, "score", 0.0) or 0.0))
            passage = getattr(top, "text", "") or ""
            start = getattr(top, "start", None)
            end = getattr(top, "end", None)
            span = (start, end) if start is not None and end is not None else None
        else:
            passage, span = "", None
        return {
            "passage": passage,
            "score": score,
            "inextractability_score": 1.0 - score,
            "span": span,
            "supports": score > 0.5,
        }

    return _safe(_call, None)


# --------------------------------------------------------------------------
# extract_answer (Phase 4, 2026.07.04): Kanon Answer Extractor second opinion
# --------------------------------------------------------------------------
def extract_answer(question: str, text: str, *, client=None) -> Optional[dict]:
    """Extractive QA over ``text`` (kanon-answer-extractor).

    Used ONLY as a second opinion on close-call verdicts (Flagged / Somewhat
    Supports / Does Not Support). ``ignore_inextractability=True`` per the
    rebuild contract so an answer always comes back with its score; the
    caller shows it BESIDE the classifier verdict, never instead of it.
    Returns {"answer", "score", "start", "end", "inextractability_score"}
    or None.
    """
    if not text or not question:
        return None

    def _call():
        cli = _client(client)
        resp = cli.extractions.qa.create(
            model=MODEL_ANSWER,
            query=question,
            texts=[text],
            ignore_inextractability=True,
            top_k=1,
        )
        exts = getattr(resp, "extractions", None) or []
        if not exts:
            return None
        e0 = exts[0]
        answers = getattr(e0, "answers", None) or []
        if not answers:
            return None
        a = answers[0]
        return {
            "answer": (getattr(a, "text", "") or "").strip(),
            "score": float(getattr(a, "score", 0.0) or 0.0),
            "start": getattr(a, "start", None),
            "end": getattr(a, "end", None),
            "inextractability_score": float(
                getattr(e0, "inextractability_score", 0.0) or 0.0),
        }

    return _safe(_call, None)


# --------------------------------------------------------------------------
# classify (D1, not in cite-check scope but exposed for later)
# --------------------------------------------------------------------------
def classify(
    query: str,
    texts: Sequence[str],
    *,
    is_iql: bool = True,
    client=None,
) -> List[dict]:
    """Score each text in ``texts`` against an IQL (or natural-language) query.

    IQL queries must be wrapped in curly braces, e.g.
        "{This document describes an executive order from the President}"
    Validation errors otherwise (plan v3, section 3).

    Returns a list of dicts aligned with ``texts``:
        {"index": int, "score": float}
    """
    if not texts:
        return []

    def _call():
        cli = _client(client)
        resp = cli.classifications.universal.create(
            model=MODEL_CLASSIFIER,
            query=query,
            is_iql=is_iql,
            texts=list(texts),
        )
        out = []
        for i, c in enumerate(resp.classifications):
            score = getattr(c, "score", None)
            if score is None:
                continue
            out.append({"index": i, "score": float(score)})
        return out

    return _safe(_call, [])


# --------------------------------------------------------------------------
# embed (D2, not in cite-check scope but exposed for later)
# --------------------------------------------------------------------------
def embed(
    texts: Sequence[str],
    task: str = "retrieval/document",
    *,
    dimensions: Optional[int] = None,
    client=None,
) -> List[List[float]]:
    """Return 1792-dim embeddings for ``texts``.

    ``task`` must be ``"retrieval/query"`` for probes or
    ``"retrieval/document"`` for corpus items (asymmetric model).

    Empty list on failure.
    """
    if not texts:
        return []

    def _call():
        cli = _client(client)
        kwargs = {
            "model": MODEL_EMBEDDER,
            "texts": list(texts),
            "task": task,
        }
        if dimensions is not None:
            kwargs["dimensions"] = dimensions
        resp = cli.embeddings.create(**kwargs)
        return [list(e.embedding) for e in resp.embeddings]

    return _safe(_call, [])


# --------------------------------------------------------------------------
# enrich (C3)
# --------------------------------------------------------------------------
def enrich(texts: Sequence[str], *, client=None) -> List[Optional[dict]]:
    """Run kanon-2-enricher over each input text.

    Returns a list aligned with ``texts``.  Each entry is either None (on
    per-document failure) or a normalized dict:

        {
            "jurisdiction": str | None,
            "document_type": str | None,
            "segments": list[dict],
            "persons": list[dict],        # judges, parties, etc.
            "external_documents": list[dict],  # citations with span/pinpoint
            "dates": list[dict],
            "quotes": list[dict],
            "headings": list[dict],
            "raw": <ILGSDocument>,        # the SDK object, for chunk() reuse
        }

    ``raw`` preserves the ILGSDocument so callers can pass it straight to
    ``semchunk.chunk(text=doc, ...)`` without paying for re-enrichment
    (plan v3, section 3).
    """
    if not texts:
        return []

    def _call():
        cli = _client(client)
        resp = cli.enrichments.create(
            model=MODEL_ENRICHER,
            texts=list(texts),
        )
        out: List[Optional[dict]] = []
        for res in resp.results:
            doc = getattr(res, "document", None)
            if doc is None:
                out.append(None)
                continue
            out.append({
                "jurisdiction": getattr(doc, "jurisdiction", None),
                "document_type": getattr(doc, "document_type", None),
                "segments": _attrs_to_dicts(getattr(doc, "segments", None)),
                "persons": _attrs_to_dicts(getattr(doc, "persons", None)),
                "external_documents": _attrs_to_dicts(getattr(doc, "external_documents", None)),
                "dates": _attrs_to_dicts(getattr(doc, "dates", None)),
                "quotes": _attrs_to_dicts(getattr(doc, "quotes", None)),
                "headings": _attrs_to_dicts(getattr(doc, "headings", None)),
                "raw": doc,
            })
        return out

    return _safe(_call, [None] * len(texts))


def _attrs_to_dicts(seq: Optional[Iterable[Any]]) -> List[dict]:
    """Convert a list of SDK model objects into a list of plain dicts.

    Uses ``model_dump`` if available (Pydantic v2); falls back to a
    shallow ``vars()`` copy.  Never raises.
    """
    if not seq:
        return []
    out: List[dict] = []
    for item in seq:
        dump = getattr(item, "model_dump", None)
        if callable(dump):
            try:
                out.append(dump())
                continue
            except Exception:  # noqa: BLE001
                pass
        try:
            out.append(dict(vars(item)))
        except Exception:  # noqa: BLE001
            out.append({"_repr": repr(item)})
    return out


# --------------------------------------------------------------------------
# to_markdown (S5)
# --------------------------------------------------------------------------
def to_markdown(
    text: str,
    *,
    link_xrefs: bool = True,
    italicize_refs: bool = True,
    italicize_terms: bool = True,
    block_quotes: bool = True,
    strike_junk: bool = False,
    escape_lists: bool = True,
    client=None,
) -> Optional[str]:
    """Convert raw text to structured Markdown via the text2markdown library.

    The library wraps the enricher; one API call per document.  Returns
    the Markdown string, or None on failure.

    Defaults chosen for the author's workflow:
        * link_xrefs=True       -- turn "see section 3" into anchor links
        * italicize_refs=True   -- italicize case citations
        * italicize_terms=True  -- italicize defined terms
        * block_quotes=True     -- detect and format block quotations
        * strike_junk=False     -- off by default; enable for OCR noise
        * escape_lists=True     -- safe default

    Gotcha (plan v3, section 8): when feeding the result downstream to
    QA or classification, strip Markdown formatting first; link noise
    distorts token counts.
    """
    if not text:
        return None

    def _call():
        # Deferred import so this module imports even if text2markdown
        # isn't installed yet.
        from text2markdown import text2markdown as _t2m
        return _t2m(
            text,
            link_xrefs=link_xrefs,
            italicize_refs=italicize_refs,
            italicize_terms=italicize_terms,
            block_quotes=block_quotes,
            strike_junk=strike_junk,
            escape_lists=escape_lists,
            isaacus_client=_client(client) if client is not None else None,
        )

    return _safe(_call, None)


__all__ = [
    "MODEL_RERANKER", "MODEL_ANSWER", "MODEL_CLASSIFIER",
    "MODEL_EMBEDDER", "MODEL_ENRICHER",
    "rerank", "verify", "classify", "embed", "enrich", "to_markdown",
]
