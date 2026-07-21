"""
C7 -- token-aware chunking via semchunk (Project: Isaacus Integration).

Rewritten 2026.06.24 (semchunk 4.x + offsets). The prior version never
actually ran semchunk in the Cowork sandbox -- it always fell back to a
dumb paragraph splitter because (a) semchunk was not installed and (b) the
HuggingFace kanon-2-tokenizer download returns 403 in the sandbox.

Fixes:
  * No-download character-based token counter by default, so semchunk loads
    without fetching any HuggingFace tokenizer.
  * Returns exact (start, end) offsets per chunk, so the caller maps
    enricher spans back to source text precisely (no len()-accumulation).
  * Reports the engine actually used, so the pipeline can log the truth and
    fail loudly when it degrades instead of claiming mode="ai".

Design: the cite-check pipeline re-enriches every chunk to detect
citations, so AI chunking (which itself calls the enricher to chunk) would
double the API bill for marginal benefit. Default is non-AI token-aware
semchunk -- free, offline, exact-offset. AI chunking is available via
force_ai=True for edge cases, using the Isaacus client.

Public surface:
    build_chunker(chunk_size, ai=False) -> (chunk_fn, engine)
        chunk_fn(text) -> (chunks, offsets)
    chunk_if_needed(text, ...) -> (chunks, offsets, info)
        info = {"mode": "skip"|"fast"|"ai", "engine": str, "chunks": int}
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional, Tuple

DEFAULT_AI_THRESHOLD_CHARS = 32_000
DEFAULT_CHUNK_SIZE = 4096
_CHARS_PER_TOKEN = 4


def _default_token_counter(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _offsets_for_chunks(text: str, chunks: List[str]) -> List[Tuple[int, int]]:
    """Best-effort offsets for a chunk list (fallback path only)."""
    offsets: List[Tuple[int, int]] = []
    cursor = 0
    for c in chunks:
        idx = text.find(c, cursor)
        if idx == -1:
            idx = text.find(c.strip(), cursor)
            if idx == -1:
                idx = cursor
        offsets.append((idx, idx + len(c)))
        cursor = idx + len(c)
    return offsets


def _simple_chunk(text: str, target_chars: int) -> List[str]:
    """Paragraph-respecting fallback chunker (no tokenizer required)."""
    if not text:
        return []
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for p in paras:
        p_len = len(p) + 2
        if current and current_len + p_len > target_chars:
            chunks.append("\n\n".join(current))
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += p_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _get_isaacus_client():
    from isaacus_config import get_client
    return get_client()


def build_chunker(
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    *,
    ai: bool = False,
    token_counter: Optional[Callable[[str], int]] = None,
    isaacus_client=None,
) -> Tuple[Callable[[str], Tuple[List[str], List[Tuple[int, int]]]], str]:
    """Construct a chunker. Returns (chunk_fn, engine).

    chunk_fn(text) -> (chunks, offsets). engine is one of:
        "semchunk"     token-aware semchunk (non-AI)
        "semchunk-ai"  semchunk with kanon-2-enricher AI chunking
        "fallback"     paragraph splitter (semchunk unavailable)
    """
    counter = token_counter or _default_token_counter
    try:
        import semchunk  # deferred

        if ai:
            client = isaacus_client or _get_isaacus_client()
            try:
                sc = semchunk.chunkerify(
                    counter,
                    chunk_size,
                    chunking_model="kanon-2-enricher",
                    isaacus_client=client,
                )
                engine = "semchunk-ai"
            except Exception as exc:  # noqa: BLE001
                print(
                    "[isaacus_chunker] AI chunking unavailable ("
                    + type(exc).__name__
                    + "); using token-aware semchunk.",
                    file=sys.stderr,
                )
                sc = semchunk.chunkerify(counter, chunk_size)
                engine = "semchunk"
        else:
            sc = semchunk.chunkerify(counter, chunk_size)
            engine = "semchunk"

        def _chunk_one(text: str):
            if not text:
                return [], []
            chunks, offsets = sc(text, offsets=True)
            return list(chunks), [tuple(o) for o in offsets]

        return _chunk_one, engine

    except (OSError, ImportError, ValueError) as exc:
        print(
            "[isaacus_chunker] semchunk unavailable ("
            + type(exc).__name__
            + "); falling back to paragraph chunker.",
            file=sys.stderr,
        )
        target_chars = _CHARS_PER_TOKEN * chunk_size

        def _chunk_one(text: str):
            if not text:
                return [], []
            chunks = _simple_chunk(text, target_chars)
            return chunks, _offsets_for_chunks(text, chunks)

        return _chunk_one, "fallback"


def chunk_if_needed(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    *,
    ai_threshold_chars: int = DEFAULT_AI_THRESHOLD_CHARS,
    force_ai: Optional[bool] = None,
):
    """Chunk text -> (chunks, offsets, info).

    force_ai True -> AI; False/None -> token-aware semchunk. AI is not
    auto-selected because the pipeline re-enriches chunks anyway.
    info["mode"] is intent; info["engine"] is what actually ran -- a
    mismatch (mode="fast"/"ai" with engine="fallback") is the loud signal.
    """
    if not text:
        return [], [], {"mode": "skip", "engine": "none", "chunks": 0}

    ai = bool(force_ai)

    if not ai and len(text) < _CHARS_PER_TOKEN * chunk_size:
        return [text], [(0, len(text))], {"mode": "skip", "engine": "none", "chunks": 1}

    chunk_fn, engine = build_chunker(chunk_size, ai=ai)
    chunks, offsets = chunk_fn(text)
    if not chunks:
        return [text], [(0, len(text))], {"mode": "skip", "engine": engine, "chunks": 1}

    mode = "ai" if ai else "fast"
    return chunks, offsets, {"mode": mode, "engine": engine, "chunks": len(chunks)}


__all__ = [
    "DEFAULT_AI_THRESHOLD_CHARS",
    "DEFAULT_CHUNK_SIZE",
    "build_chunker",
    "chunk_if_needed",
]
