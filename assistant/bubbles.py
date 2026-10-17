"""Split a long reply into a few natural iMessage bubbles.

People text in short bursts, not walls. Paragraphs are the split points; a single paragraph
longer than the limit is split at sentence ends. Text with no safe boundary (a long URL, a
single run-on sentence) stays whole rather than being cut mid-word.
"""

from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=\S)")


def _pieces(paragraph: str, limit: int) -> list[str]:
    if len(paragraph) <= limit:
        return [paragraph]
    return [s for s in _SENTENCE_END.split(paragraph) if s]


def split_bubbles(text: str, limit: int) -> list[str]:
    """Return ``text`` as bubbles of at most ``limit`` chars where boundaries allow.

    ``limit <= 0`` disables splitting. Pieces are packed greedily so short paragraphs stay
    together; paragraph breaks inside one bubble are preserved.
    """
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return [text] if text else []
    bubbles: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        joiner = "\n\n"
        for piece in _pieces(paragraph, limit):
            candidate = f"{current}{joiner}{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    bubbles.append(current)
                current = piece
            joiner = " "  # sentences of one paragraph rejoin with a space
    if current:
        bubbles.append(current)
    return bubbles
