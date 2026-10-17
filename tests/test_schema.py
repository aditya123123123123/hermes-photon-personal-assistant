from assistant.reaction_tool import PHOTON_REACT_SCHEMA


def test_required_reaction_description():
    assert PHOTON_REACT_SCHEMA["description"] == "React to the current iMessage with an arbitrary emoji or emoji sequence. This is a native iMessage tapback/custom reaction, not a text reply. Omit message_id to react to the triggering message. React on instinct, the way a close friend would when a message lands (❤️ good news, 😂 something funny, 👍 a plan confirmed, 😭 a rough moment); it is your call, and most messages need no reaction. Never react by habit or to acknowledge receipt, and do not narrate the reaction in your reply."
