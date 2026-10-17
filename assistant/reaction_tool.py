"""Photon-only agent tool for native iMessage reactions."""

from __future__ import annotations

import json
from typing import Any, Awaitable, cast

from gateway.session_context import get_session_env
from tools.registry import tool_error
from .transport import current_adapter


PHOTON_REACT_SCHEMA = {
    "name": "photon_react",
    "description": (
        "React to the current iMessage with an arbitrary emoji or emoji sequence. "
        "This is a native iMessage tapback/custom reaction, not a text reply. "
        "Omit message_id to react to the triggering message. React on instinct, the way a "
        "close friend would when a message lands (❤️ good news, 😂 something funny, 👍 a plan "
        "confirmed, 😭 a rough moment); it is your call, and most messages need no reaction. "
        "Never react by habit or to acknowledge receipt, and do not narrate the reaction in your reply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": (
                    "Any non-empty Unicode emoji or emoji sequence, such as ❤️, 😂, 🫠, "
                    "or 🧠. Custom and multi-codepoint emoji are supported."
                ),
            },
            "message_id": {
                "type": "string",
                "description": (
                    "Optional Photon message id. Omit this to target the message that "
                    "triggered the current turn."
                ),
            },
        },
        "required": ["emoji"],
    },
}


async def photon_react_tool(args: dict[str, Any], **_kwargs: Any) -> str:
    """Add one arbitrary reaction through the active Photon adapter."""
    emoji = args.get("emoji") if isinstance(args, dict) else None
    if not isinstance(emoji, str) or not emoji.strip():
        return tool_error("'emoji' is required and must be a non-empty Unicode string.")

    platform_name = get_session_env("HERMES_SESSION_PLATFORM", "").strip().lower()
    if platform_name != "photon":
        return tool_error("photon_react is available only in a Photon session.")

    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "").strip()
    if not chat_id:
        return tool_error("No active Photon chat is bound to this turn.")
    message_id = args.get("message_id") or get_session_env("HERMES_SESSION_MESSAGE_ID", "")
    message_id = message_id.strip() if isinstance(message_id, str) else None
    if not message_id:
        return tool_error("No triggering message is bound; provide an exact message_id.")

    from hermes_cli.plugin_capabilities import plugin_capability_granted
    from hermes_cli.config import load_config_readonly
    if not plugin_capability_granted("photon-personal-assistant", "gateway.platform_actions",
                                     config=load_config_readonly()):
        return tool_error("Platform-action consent is required for reactions.")
    adapter = current_adapter()
    if adapter is None:
        return tool_error("Photon reactions require the running gateway adapter.")
    react = getattr(adapter, "add_reaction", None)
    if not callable(react):
        return tool_error("The active Photon adapter does not support reactions.")
    try:
        result = await cast(Awaitable[Any], react(
            chat_id=chat_id, message_id=message_id, emoji=emoji.strip()
        ))
    except Exception as exc:  # adapter boundary: return a model-readable tool error
        return tool_error(f"Photon reaction failed: {exc}")
    return json.dumps(result if isinstance(result, dict) else {"success": bool(result)}, ensure_ascii=False)
