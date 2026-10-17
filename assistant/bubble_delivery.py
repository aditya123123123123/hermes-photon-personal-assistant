"""Natural bubble delivery layered over the stock sidecar."""
from typing import Any, Optional
from gateway.platforms.base import SendResult
from .bubbles import split_bubbles


class BubbleDelivery:
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[dict[str, Any]] = None) -> SendResult:
        bubbles = split_bubbles(content, self._bubble_max_chars) or [content]
        sent: list[str] = []
        delivered = False
        result = SendResult(success=False, error="nothing to send")
        for index, bubble in enumerate(bubbles):
            result = await self._sidecar_send(chat_id, self.format_message(bubble))
            if not result.success:
                if not delivered:
                    return result
                raw = dict(result.raw_response) if isinstance(result.raw_response, dict) else {}
                raw["partial_overflow"] = True
                raw["remaining_bubbles"] = bubbles[index:]
                return SendResult(success=False, error=result.error, retryable=result.retryable,
                                  raw_response=raw, message_id=sent[-1] if sent else None,
                                  continuation_message_ids=tuple(sent[:-1]))
            delivered = True
            if result.message_id:
                sent.append(result.message_id)
        if len(bubbles) == 1:
            return result
        return SendResult(success=True, message_id=sent[-1] if sent else None,
                          continuation_message_ids=tuple(sent[:-1]))

    async def _resume_partial_send(self, chat_id: str, result: SendResult, *,
                                   reply_to: Optional[str], metadata: Any) -> Optional[SendResult]:
        raw = result.raw_response if isinstance(result.raw_response, dict) else {}
        remaining = raw.get("remaining_bubbles")
        if remaining:
            return await self.send(chat_id, "\n\n".join(remaining), reply_to=reply_to, metadata=metadata)
        return await super()._resume_partial_send(chat_id, result, reply_to=reply_to, metadata=metadata)
