"""Update-safe Photon assistant plugin entry point."""
import logging


class _PlatformDeclaration:
    """Collect stock metadata without resolving its discovery-locked lazy loader."""
    values = None
    cli = None

    def register_platform(self, **values):
        self.values = values

    def register_cli_command(self, **kwargs):
        self.cli = kwargs


def register(ctx):
    if not ctx.has_plugin("photon-platform"):
        logging.getLogger(__name__).warning("Photon platform missing or disabled; assistant inactive")
        return
    from plugins.platforms.photon.adapter import register as register_bundled
    from .assistant.adapter import PersonalAssistantPhotonAdapter
    from .assistant.personal_assistant import _on_pre_llm_call
    from .assistant.reaction_tool import PHOTON_REACT_SCHEMA, photon_react_tool
    from .assistant.commitments import register_commitment_cli, dispatch_commitment

    declaration = _PlatformDeclaration()
    register_bundled(declaration)
    values = dict(declaration.values)
    values["adapter_factory"] = PersonalAssistantPhotonAdapter
    ctx.register_platform(**values)
    # Preserve the bundled command implementation when replacing its lazy loader.
    ctx.register_cli_command(**declaration.cli)
    ctx.register_tool(name="photon_react", toolset="photon", schema=PHOTON_REACT_SCHEMA,
                      handler=photon_react_tool, is_async=True)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    def setup(parser):
        register_commitment_cli(parser.add_subparsers(required=True))
        parser.set_defaults(func=dispatch_commitment)

    ctx.register_cli_command(name="assistant", help="Photon personal-assistant commitments",
                             setup_fn=setup, handler_fn=dispatch_commitment)
