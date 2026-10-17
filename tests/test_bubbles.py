"""Photon splits a long reply into a few iMessage bubbles instead of one wall of text, and a
failure partway through never re-sends bubbles that already went out."""
from __future__ import annotations

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from assistant.adapter import PersonalAssistantPhotonAdapter as PhotonAdapter
from assistant.bubbles import split_bubbles

PARA = "This is one sentence that is reasonably long. And here is a second sentence too."


def _adapter(monkeypatch, **extra) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    monkeypatch.setattr("assistant.adapter.setting", lambda key, default=None: extra.get(key, default))
    return PhotonAdapter(PlatformConfig(enabled=True, token="", extra=extra))


def _record_sends(adapter, monkeypatch, fail_at=None, failures=1):
    sent: list[str] = []
    state = {"failures_left": failures}

    async def fake_sidecar_send(space_id, text, **_kw):
        if fail_at is not None and len(sent) == fail_at and state["failures_left"] > 0:
            state["failures_left"] -= 1
            return SendResult(success=False, error="reset reason: overflow", retryable=True)
        sent.append(text)
        return SendResult(success=True, message_id=f"m{len(sent)}")

    monkeypatch.setattr(adapter, "_sidecar_send", fake_sidecar_send)
    return sent


def test_short_text_is_one_bubble():
    assert split_bubbles("hey, sounds good", 600) == ["hey, sounds good"]


def test_paragraphs_split_and_every_bubble_fits():
    text = "\n\n".join([PARA] * 12)
    bubbles = split_bubbles(text, 200)
    assert len(bubbles) > 1
    assert all(len(b) <= 200 for b in bubbles)
    assert " ".join(" ".join(bubbles).split()) == " ".join(text.split())


def test_one_long_paragraph_splits_at_sentence_ends():
    text = " ".join([PARA] * 10)
    bubbles = split_bubbles(text, 200)
    assert len(bubbles) > 1
    assert all(b.rstrip().endswith((".", "!", "?")) for b in bubbles)


def test_unbreakable_text_is_never_cut_mid_word():
    url = "https://example.com/" + "a" * 900
    assert split_bubbles(url, 200) == [url]


def test_zero_limit_disables_splitting():
    text = "\n\n".join([PARA] * 12)
    assert split_bubbles(text, 0) == [text]


@pytest.mark.asyncio
async def test_long_reply_goes_out_as_several_bubbles(monkeypatch):
    adapter = _adapter(monkeypatch, bubble_max_chars=200)
    sent = _record_sends(adapter, monkeypatch)
    result = await adapter.send("any;-;+15550000000", "\n\n".join([PARA] * 6))
    assert result.success
    assert len(sent) > 1
    assert result.message_id == f"m{len(sent)}"


@pytest.mark.asyncio
async def test_retry_after_partial_failure_sends_only_the_rest(monkeypatch):
    adapter = _adapter(monkeypatch, bubble_max_chars=200)
    sent = _record_sends(adapter, monkeypatch, fail_at=1)
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    paragraphs = [f"Part {i}. {PARA}" for i in range(4)]
    result = await adapter._send_with_retry("any;-;+15550000000", "\n\n".join(paragraphs))
    assert result.success
    joined = "\n\n".join(sent)
    for p in paragraphs:
        assert joined.count(p) == 1, "every paragraph delivered exactly once"


async def _no_sleep(_delay):
    return None
