# Host integration boundaries

Tested host: `963b4e7d5029e8520657a6bf3c8fecb883b7d648`.

## Runtime mode

Requires the default `plugins.isolation: in_process` mode. Hermes host-isolated plugins cannot register live adapter objects; this package does not opt out or bypass an operator-managed host-isolation policy.

## Platform registration

`requires_plugins: [{id: photon-platform}]` documents ordering. The host defers bundled platform adapters before dependency ordering and can emit an advisory “dependency not enabled/installed” warning even when its enabled placeholder exists. `ctx.has_plugin("photon-platform")` is the actual runtime gate.

Resolving `platform_registry.get("photon")` inside plugin `register()` would run the deferred loader under a conflicting discovery lock. Instead a small declaration collector calls the bundled adapter's `register()` entrypoint, captures its complete platform keyword declaration, changes only `adapter_factory`, then publishes via `ctx.register_platform`. The stock Photon CLI setup/handler are preserved, and a separate `assistant` CLI is registered. There is no core monkeypatch and no second sidecar. This integration depends on the bundled adapter's registration signature and needs regression testing on updates.

## Direct reaction limitation

`PluginContext.register_tool` has no public direct/nondeferrable or platform-session field. The `photon` toolset is automatically included as a plugin toolset, but `tools.tool_search.is_deferrable_tool_name` classifies non-core plugin tools as deferred. A schema injected by `llm_request` middleware alone would disagree with `agent.valid_tool_names`, used before dispatch by `agent/turn_tool_validation.py`. Adding/removing tools mid-turn would also undermine stable tool surfaces.

A session-based `check_fn` is not a sound platform gate: availability and outer tool-definition caches are profile-scoped, not Photon-session-scoped. Globally disabling tool search exposes the schema directly in CLI and Telegram too, including with explicit platform toolset lists. Tests retain this unsafe-workaround observation.

The stock fallback registers the exact tool schema with a runtime Photon guard and capability consent. It remains discoverable/deferred rather than spontaneously visible. It can be discovered on non-Photon sessions but execution refuses there. This is a documented limitation, not full platform invisibility.

`upstream-patches/0001-platform-tools-direct.patch` proposes two generic host changes: registered platform-named toolsets are session surfaces in `toolsets.py`, and are nondeferrable in `tools/tool_search.py` unless explicitly listed for deferral. It contains no Photon-specific strings. `tests/test_upstream_patch.py` applies the diff to source strings in memory and exercises the real model assembly path with test-scoped replacements. No checkout file is patched. The test proves Photon visibility and CLI/Telegram absence under the proposal; it does not activate production behavior.

## Reactions and authorization

The public `ctx.platform_actions` facade currently implements reactions for Telegram/Discord, not Photon. The plugin keeps a weak, profile-keyed reference to its own adapter and calls its existing `add_reaction` method, enforcing the same declared `gateway.platform_actions` consent itself. It does not read the private gateway-runner registry. Exact event IDs prevent last-inbound-message races; a missing current ID now refuses instead of falling back to whichever inbound message arrived most recently.

The gateway authorizes human turns before invoking hooks. Adapter guidance/observation additionally checks its installed authorization callback. Hook recall requires exact platform/DM/sender/session context and rejects cron/delegation/kanban. Like any plugin in a trusted Python process, it does not attempt to make forged in-process context into a separate security sandbox.

## Other differences

- CLI namespace changes from `hermes photon commitment` to `hermes assistant commitment`.
- Durable plugin data uses its own profile-local directory, not the core suggestions file; no automatic migration.
- Settings move to manifest-backed plugin settings; legacy owner/calendar configuration remains compatible.
- Reaction calls require explicit plugin capability consent and a triggering/exact message ID.
- Bubble splitting remains a soft boundary; an unbreakable word can exceed the configured limit. A delivered bubble without a returned ID is still counted as delivered for suffix retry. Non-bubble partial sends defer to the base resume method.
- No live transport/provider, production deployment, or full upstream test suite was exercised. Tests use synthetic envelopes/accounts; compatibility outside the pinned revision is not claimed.
