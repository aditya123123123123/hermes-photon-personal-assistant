"""Deterministic relevance ranking for recalled memory candidates.

Ranking is deliberately local and metadata-first: providers may supply a text string,
``{"content": ...}``, or a richer mapping.  No provider/network call is made here.
The result is suitable for the dynamic per-turn user-context sidecar and therefore
never changes the stable system-prompt prefix.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Iterable, Mapping, Optional

_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)
_DEFAULT_HALF_LIFE_DAYS = 7.0


@dataclass(frozen=True)
class MemoryCandidate:
    """A normalized candidate accepted by :func:`rank_memories`."""

    content: str
    created_at: Optional[datetime] = None
    importance: float = 0.5
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]
    original_index: int = 0


@dataclass(frozen=True)
class RankedMemory:
    """Candidate plus its deterministic relevance score and components."""

    content: str
    score: float
    recency: float
    importance: float
    topic_similarity: float
    context_alignment: float
    metadata: Mapping[str, Any]
    original_index: int


def _tokens(value: Any) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(str(value or ""))}


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _candidate(value: Any, index: int) -> MemoryCandidate:
    if isinstance(value, MemoryCandidate):
        return value
    if isinstance(value, Mapping):
        content = value.get("content", value.get("text", value.get("memory", "")))
        metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else value
        created = value.get("created_at", value.get("timestamp", value.get("updated_at")))
        importance = value.get("importance", value.get("priority", 0.5))
    else:
        content, metadata, created, importance = value, {}, None, 0.5
    try:
        importance = float(importance)
    except (TypeError, ValueError):
        importance = 0.5
    return MemoryCandidate(
        content=str(content or ""), created_at=_as_datetime(created),
        importance=max(0.0, min(1.0, importance)), metadata=dict(metadata or {}),
        original_index=index,
    )


def _recency(created_at: Optional[datetime], now: datetime, half_life_days: float) -> float:
    if created_at is None:
        return 0.5
    age = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.pow(0.5, age / half_life_days)


def _context_alignment(metadata: Mapping[str, Any], context: Optional[Mapping[str, Any]]) -> float:
    if not context:
        return 0.5
    matches = 0
    considered = 0
    for key, expected in context.items():
        actual = metadata.get(key)
        if expected in (None, ""):
            continue
        considered += 1
        if actual == expected or str(actual).casefold() == str(expected).casefold():
            matches += 1
    return matches / considered if considered else 0.5


def relevance_score(
    memory: Any,
    query: str = "",
    *,
    now: Optional[datetime] = None,
    context: Optional[Mapping[str, Any]] = None,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Return a score in ``[0, 1]`` using recency, importance, topic and context.

    The weights are intentionally explicit and stable: topic (0.45), context (0.25),
    importance (0.20), and recency (0.10). An un-timestamped candidate gets a neutral
    recency value rather than an accidental freshness advantage.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    candidate = _candidate(memory, 0)
    current = _as_datetime(now) or datetime.now(UTC)
    query_tokens, memory_tokens = _tokens(query), _tokens(candidate.content)
    topic = (len(query_tokens & memory_tokens) / len(query_tokens | memory_tokens)
             if query_tokens and memory_tokens else 0.0 if query_tokens else 0.5)
    recency = _recency(candidate.created_at, current, half_life_days)
    alignment = _context_alignment(candidate.metadata, context)
    return 0.45 * topic + 0.25 * alignment + 0.20 * candidate.importance + 0.10 * recency


def rank_memories(
    memories: Iterable[Any],
    query: str = "",
    *,
    now: Optional[datetime] = None,
    context: Optional[Mapping[str, Any]] = None,
    limit: Optional[int] = None,
    half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
) -> list[RankedMemory]:
    """Normalize, score, and stably rank candidates without mutating the input."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative or None")
    current = _as_datetime(now) or datetime.now(UTC)
    ranked: list[RankedMemory] = []
    for index, raw in enumerate(memories):
        candidate = _candidate(raw, index)
        score = relevance_score(candidate, query, now=current, context=context, half_life_days=half_life_days)
        query_tokens, memory_tokens = _tokens(query), _tokens(candidate.content)
        topic = (len(query_tokens & memory_tokens) / len(query_tokens | memory_tokens)
                 if query_tokens and memory_tokens else 0.0 if query_tokens else 0.5)
        ranked.append(RankedMemory(
            content=candidate.content, score=score,
            recency=_recency(candidate.created_at, current, half_life_days),
            importance=candidate.importance, topic_similarity=topic,
            context_alignment=_context_alignment(candidate.metadata, context),
            metadata=dict(candidate.metadata), original_index=index,
        ))
    ranked.sort(key=lambda item: (-item.score, item.original_index))
    return ranked if limit is None else ranked[:limit]
