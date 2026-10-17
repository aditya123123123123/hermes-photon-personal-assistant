# Host integration boundaries

Tested hosts: upstream `963b4e7d5029e8520657a6bf3c8fecb883b7d648` and tag `v0.22.2` (peeled commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`). Both expose `ctx.register_skill`; the manifest supports and declares `requires_hermes: ">=0.22.2"`.

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

## Diagnostics and public API use

Tool registration, hooks, CLI, platform registration and skill bundling use public `ctx.register_*` APIs. The registered reaction wrapper now uses `ctx.has_capability()` and the hook/tool handlers use `ctx.has_plugin()`. An isolated validator lacks Photon, so the old registration-time early return falsely disagreed with `provides_tools`/`provides_hooks`; declarations are now unconditional and execution remains gated. This does not make reactions globally executable.

Doctor deliberately does not call `load_config_readonly`: on a cold cache that host function creates directories/backups. It parses the selected profile YAML without bootstrap and applies the existing environment expansion/managed overlay. Credential presence uses the documented scoped secret accessor; it never reads `.env` itself. There are no online or child-calendar probes. CLI host bootstrap is separate and may write host metadata before dispatch.

### Remaining private-core imports and reasons

These are version-sensitive and remain validator warnings, not promises of a supported API. No core module is rebound or monkeypatched. Import sites are listed by module/function rather than unstable line numbers; the validator reports current file:line locations.

| Import | Sites | Why retained / upstream seam needed |
| --- | --- | --- |
| `plugins.platforms.photon.adapter.register`, `PhotonAdapter` | `__init__.py`, `assistant/adapter.py` | Preserve bundled metadata/transport while adding bubbles and event guidance; no public adapter decorator seam. Rule 15 concern remains. |
| `hermes_cli.config_read_errors.FailedConfigRead` | `assistant/calendar/configured.py` | Reject recovered/broken policy instead of treating last-known-good approval as fresh consent. |
| `gateway.session_context.get_session_env` | commitments, owner, personal_assistant, reaction_tool | Exact current event/DM/session identity also in standalone CLI children; context object is unavailable there. |
| `cron.jobs.parse_duration`, `list_jobs`, `load_jobs`, `remove_job` | commitments, coordinator, owner, store | Existing job semantics, exact readback, quiescent-owner gate and cancellation; do not duplicate a scheduler or change job formats. |
| `cron.scheduler.create_job_with_scheduler_registration`, `cron.scheduler_provider.resolve_cron_scheduler` | store | Bind durable jobs to the actual scheduler and its unregister operation, not merely JSON persistence. |
| `hermes_cli.plugins_manifest.running_hermes_version`, `version_satisfies` | doctor | Same host version identity and floor semantics; unknown identity is explicitly failed. |
| `hermes_cli.config._expand_env_vars`, `hermes_cli.managed_scope.apply_managed_overlay` | doctor | Honor active config semantics without the mutating normal loader; request a true no-write public config reader upstream. |
| `hermes_constants.__file__` | doctor | Locate the running host's bundled adapter without loading it or querying the sidecar. |
| `tools.tool_search.is_deferrable_tool_name` | doctor | Required host-owned runtime detection automatically recognizes the direct-visibility fix. |
| `hermes_cli.plugin_capabilities.plugin_capability_granted` | doctor, reaction_tool | Doctor passes already-read config to avoid bootstrap; direct handler tests/standalone calls retain a fail-closed consent check. Registered runtime wrapper uses public `ctx.has_capability`. |
| `hermes_cli.active_sessions._FileLock` | owner, store | Host-compatible interprocess locking for atomic owner/data operations; no equivalent context API in standalone CLI. |
| `hermes_cli.config._write_user_config` | owner | Preserve host-safe config writing in the guarded operator command; never used by doctor. |
| `hermes_time.now`, `get_timezone_name` | detector, store | Preserve configured timezone and host-relative-time behavior for saved records. |
| `agent.delegation_context.is_delegated_child_process_context`, `owned_kanban_task` | personal_assistant | Fail closed for delegated/kanban invocations even if an apparent Photon binding exists. |

The Google provider also imports the bundled Workspace script by its skill path, a version-sensitive integration rather than a new credential client. `ctx.platform_actions` currently lacks Photon reactions, so using it would not preserve behavior. These seams should be upstreamed, not disguised or silenced.

## Other differences

- CLI namespace changes from `hermes photon commitment` to `hermes assistant commitment`.
- Durable plugin data uses its own profile-local directory, not the core suggestions file; no automatic migration.
- Settings move to manifest-backed plugin settings; legacy owner/calendar configuration remains compatible.
- Reaction calls require explicit plugin capability consent and a triggering/exact message ID.
- Bubble splitting remains a soft boundary; an unbreakable word can exceed the configured limit. A delivered bubble without a returned ID is still counted as delivered for suffix retry. Non-bubble partial sends defer to the base resume method.
- No live transport/provider, production deployment, or full upstream test suite was exercised. Tests use synthetic envelopes/accounts; compatibility outside the two tested revisions is not claimed.
