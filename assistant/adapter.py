"""Subclass only: preserve the bundled transport and gateway authorization."""
from __future__ import annotations

import asyncio
import logging

from plugins.platforms.photon.adapter import PhotonAdapter
from gateway.platforms.base import resolve_channel_prompt, resolve_channel_skills
from .bubble_delivery import BubbleDelivery
from .personal_assistant import personal_assistant_channel_prompt
from .settings import setting

logger = logging.getLogger(__name__)


class PersonalAssistantPhotonAdapter(BubbleDelivery, PhotonAdapter):
    def __init__(self, config):
        super().__init__(config)
        from .transport import remember
        remember(self)
        value = setting("bubble_max_chars", 600)
        self._bubble_max_chars = value if type(value) is int and value >= 0 else 600

    async def handle_message(self, event):
        raw = event.raw_message
        # Stock normalization infers DM/sender from incomplete envelopes. Never let
        # that inference grant assistant scope or create a durable proposal.
        if isinstance(raw, dict):
            space, sender = raw.get("space") or {}, raw.get("sender") or {}
            event.source.chat_type = space.get("type") if space.get("type") in {"dm", "group"} else "unknown"
            event.source.user_id = sender.get("id") or ""
            if self._assistant_authorized(event):
                extra = self.config.extra or {}
                event.channel_prompt = personal_assistant_channel_prompt(
                    resolve_channel_prompt(extra, event.source.chat_id))
                event.auto_skill = resolve_channel_skills(extra, event.source.chat_id)
                await self._observe(raw)
        await super().handle_message(event)

    def _assistant_authorized(self, event):
        source = event.source
        return (setting("enabled", True) is True and source.chat_type == "dm"
                and bool(source.chat_id) and bool(source.user_id)
                and self._is_sender_authorized(source.user_id, "dm", source.chat_id) is True)

    async def _observe(self, raw):
        from .dropped_thread_detector import observe_photon_inbound
        try:
            await asyncio.to_thread(observe_photon_inbound, raw)
        except (OSError, ValueError):
            logger.warning("Dropped-thread observation unavailable", exc_info=True)
