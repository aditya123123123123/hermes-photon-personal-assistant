"""Update-safe Photon assistant plugin entry point."""
import logging
from pathlib import Path


class _PlatformDeclaration:
    """Collect stock metadata without resolving its discovery-locked lazy loader."""
    values = None
    cli = None

    def register_platform(self, **values):
        self.values = values

    def register_cli_command(self, **kwargs):
        self.cli = kwargs


def register(ctx):
    from .assistant.personal_assistant import _on_pre_llm_call
    from .assistant.reaction_tool import PHOTON_REACT_SCHEMA, photon_react_tool
    from .assistant.commitments import register_commitment_cli, dispatch_commitment
    from .assistant.doctor import register_doctor_cli, main as doctor_main
    from tools.registry import tool_error

    async def react(args, **kwargs):
        if not ctx.has_plugin("photon-platform"):
            return tool_error("Photon platform is missing or disabled.")
        if not ctx.has_capability("gateway.platform_actions"):
            return tool_error("Platform-action consent is required for reactions.")
        return await photon_react_tool(args, **kwargs)

    def recall(**kwargs):
        if not ctx.has_plugin("photon-platform"):
            return None
        return _on_pre_llm_call(**kwargs)

    # Declarations match even in the isolated validator probe. Runtime gates
    # stay inside handlers, not session-dependent availability caches.
    ctx.register_tool(name="photon_react", toolset="photon", schema=PHOTON_REACT_SCHEMA,
                      handler=react, is_async=True)
    ctx.register_hook("pre_llm_call", recall)
    ctx.register_skill("texting-assistant", Path(__file__).parent / "skills/texting-assistant/SKILL.md",
                       description="Use when replying naturally in a personal text chat.")

    def dispatch(args):
        if getattr(args, "assistant_command", None) == "doctor":
            return doctor_main(args)
        if not ctx.has_plugin("photon-platform"):
            print("Photon platform is missing or disabled; run hermes assistant doctor.")
            return 1
        return dispatch_commitment(args)

    def setup(parser):
        subs = parser.add_subparsers(required=True)
        register_commitment_cli(subs)
        register_doctor_cli(subs)
        parser.set_defaults(func=dispatch)

    ctx.register_cli_command(name="assistant", help="Photon assistant setup and commitments",
                             setup_fn=setup, handler_fn=dispatch)
    if not ctx.has_plugin("photon-platform"):
        logging.getLogger(__name__).warning("Photon platform missing or disabled; transport inactive")
        return
    from plugins.platforms.photon.adapter import register as register_bundled
    from .assistant.adapter import PersonalAssistantPhotonAdapter
    declaration = _PlatformDeclaration()
    register_bundled(declaration)
    values = dict(declaration.values)
    values["adapter_factory"] = PersonalAssistantPhotonAdapter
    ctx.register_platform(**values)
    ctx.register_cli_command(**declaration.cli)
