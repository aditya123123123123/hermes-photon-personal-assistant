# Photon Personal Assistant

A standalone Hermes user plugin for natural iMessage replies and approval-gated personal follow-through, inspired by texting-first assistants. MIT licensed; no core files are edited or monkeypatched by the plugin.

**Compatibility:** requires Hermes >=0.22.2; tested against that release and upstream commit `963b4e7d5029e8520657a6bf3c8fecb883b7d648`, Python 3.14.7, macOS. Re-run the contracts when updating Hermes. Not listed in the Hermes plugin catalog.

Start with the step-by-step [setup guide](SETUP.md), then run `hermes assistant doctor`. Review [disclosures and catalog eligibility](DISCLOSURES.md) before installing.

**Known release blocker:** stock upstream defers `photon_react` behind `tool_search` / `tool_describe` / `tool_call`. The plugin works with that fallback, but does **not** deliver spontaneous directly-visible reactions on stock upstream. `upstream-patches/0001-platform-tools-direct.patch` is an optional, generic, **unapplied** proposal with an executable assembly test. It makes registered platforms' own toolsets direct and session-scoped. Do not claim complete behavioral parity until the host supplies that contract. See [compatibility notes](docs/compatibility.md).

## Features

- Long text replies split at paragraphs, then sentences, with a soft 600-character limit; words are never cut. Partial delivery resumes only the undelivered suffix. `0` disables splitting.
- `photon_react`: arbitrary Unicode emoji/sequences, optionally an exact message ID, otherwise the triggering event's ID. The model chooses; no automatic reaction, receipt acknowledgement, or narration. Most messages need no reaction. Missing triggering IDs fail closed rather than reacting to a newer message.
- Stable event-local assistant guidance and owner selection only for authorized one-to-one DMs, merged with configured `channel_prompts` and channel skill bindings. Groups, unknown chat types, and missing sender IDs get no assistant guidance. The gateway's authorization check remains authoritative.
- `pre_llm_call` adds read-only recall to the turn's context sidecar, never mutates the system prompt. Pending proposals, accepted commitments and trusted calendar observations stay scoped to the exact current DM. Cron, delegated children, kanban workers and session mismatches are excluded.
- `hermes assistant commitment` supports request, capture, propose, approve, list, show, cancel, block and complete, plus owner inspection/guarded selection. Explicit reminders use Hermes cron's scheduler registration API; general captures do not start workers or schedule notifications.
- Inbound dropped-thread observation creates proposals only. No background scanner, unsolicited notification or automatic approval is installed.
- Calendar access is disabled by default. Cached JSON or an existing authenticated Google Workspace client can supply bounded read-only observations; no calendar writes. Existing OAuth refresh can update its own token file.

## Install

First configure Hermes' bundled Photon platform using its normal `hermes photon setup` workflow. This plugin does not install or authenticate Photon.

Once this repository has a Git URL, install with:

```sh
hermes plugins install <git-url>
hermes plugins enable photon-personal-assistant
```

For a local checkout, clone or symlink it into the **chosen profile's** plugin directory (default is `~/.hermes/plugins/`):

```sh
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/plugins"
ln -s /absolute/path/to/hermes-photon-personal-assistant "${HERMES_HOME:-$HOME/.hermes}/plugins/photon-personal-assistant"
hermes plugins enable photon-personal-assistant
```

Review/approve the declared `gateway.platform_actions` capability through Hermes' plugin consent flow if reactions are desired. Without the grant, reaction execution returns an error. The plugin never self-grants consent. Activate through a normal operator-controlled gateway restart and a fresh conversation; do not hot-reload prompts into an ongoing conversation. This package's build/tests did **not** enable anything in a production profile or restart a gateway.

If `photon-platform` is missing or explicitly disabled, no platform replacement is installed. Diagnostic CLI, skill, tool and hook declarations remain registered so validation is accurate and setup is diagnosable; runtime handlers refuse or return no context without Photon.

## Configuration

Use the upstream manifest settings convention in the selected profile's `config.yaml`:

```yaml
plugins:
  enabled: [photon-personal-assistant]
  entries:
    photon-personal-assistant:
      settings:
        enabled: true
        bubble_max_chars: 600
        owner: auto
        calendar:
          enabled: false
          approval_granted: false
```

| Key | Default | Behavior |
| --- | --- | --- |
| `enabled` | `true` | Assistant event guidance, observation and recall; not a global plugin-disable switch. Disable the plugin itself to restore stock transport. |
| `bubble_max_chars` | `600` | Nonnegative integer, soft limit. `0` disables splitting. |
| `owner` | `auto` | `auto`, `native`, `legacy`, or `disabled`. Auto preserves an installed legacy follow-through script/skill. Explicit native ownership refuses while the legacy executable remains installed. |
| `calendar` | disabled | Trusted provider policy below. |

Legacy `plugins.entries.<id>.config` settings are accepted. A trusted `photon_followthrough.owner` value, if present, takes precedence over the plugin owner setting. Owner selection is not inferred from messages. Guarded local owner selection retains the branch's legacy config key for compatibility; it requires a genuine local operator TTY and quiescent work. No migration/cancellation happens implicitly.

Configured Photon `channel_prompts` and `channel_skill_bindings` / channel skills remain in the normal Photon platform config and are resolved by Hermes' existing helpers. The bundled namespaced `texting-assistant` skill contains only generic style guidance, not a private skill or personal prompt.

### Calendar

Example trusted cached provider policy (replace placeholders with exact identities privately):

```yaml
calendar:
  enabled: true
  approval_granted: true
  provider: cached_json
  approved_scopes:
    - chat_id: '<exact Photon DM GUID>'
      user_id: '<exact sender ID>'
  snapshot_path: photon-personal-assistant/calendar.json
  max_age_seconds: 3600
  source: local-cache
```

Place this under the plugin's `settings`, not in a message. Paths are relative to the current Hermes home unless absolute. Cached metadata includes `fetched_at` and explicit freshness; stale/unavailable is not treated as an empty calendar. For `provider: google_workspace`, set `calendar_id` and the same exact grants; an existing bundled Google Workspace skill/client/authentication must already be available. Reads have an eight-second subprocess timeout, a 31-day query bound, and no hidden pagination. Legacy trusted `platforms.photon.extra.calendar` is honored if no plugin calendar policy is set. No hook/model-supplied provider or approval override is accepted.

## Commitments and storage

Run `hermes assistant commitment --help` for flags. Mutating conversational commands require a real current Photon DM binding plus a matching durable gateway session/message in `state.db`; fabricated environment variables or an old message do not substitute for approval. Request/capture/approve require `--accepted --acceptance '<exact current-user quote>'`; a quote proves provenance, not semantic consent.

An accepted reminder creates one one-shot cron job with an exact Photon destination. Retry keys deduplicate; consumed/cancelled records are tombstones, not resend authority. Completion/blocking requires an inspected local artifact, literal SHA-256 and expected content, not scheduler success. List/show readback preserves uncertainty about actual task completion and delivery.

Plugin-owned data lives at `get_hermes_home()/photon-personal-assistant/store.json` (atomic JSON plus an interprocess lock). Cron jobs remain in Hermes' standard profile-local cron store. No top-level `cron`, `photon_assistant`, or `hermes_calendar` packages are installed. No code is injected into `cron/suggestions.py`.

This plugin never imports or migrates a legacy `cron/suggestions.json` file automatically. If one exists in another installation, reconcile explicitly before cutover.

## Testing

See [verification](docs/verification.md) for the exact tested invocation. A dedicated venv uses upstream via `PYTHONPATH`, with all profile state and pytest temporary files under scratch. The suite exercises real `PluginManager` directory discovery (also from an unrelated cwd in isolated Python), real model tool assembly, registered hooks, inbound event construction, retry delivery, subprocess commitment CLI round trips with actual cron JSON, synthetic calendar subprocesses, proposal concurrency and profile isolation. Network calls are refused in the main test process; transports and Workspace endpoints are synthetic. No real message or calendar request is sent.

The strict expected failure is an explicit unsatisfied stock-host requirement, not a passing reaction-directness claim.

## Uninstall

Cancel/reconcile active assistant reminders first using their owning workflow; disabling the plugin does not remove cron jobs. Then `hermes plugins disable photon-personal-assistant`, restart the gateway normally, and remove the checkout/symlink (or use `hermes plugins remove photon-personal-assistant` for a managed install). Keep the plugin data directory until records are reconciled. Nothing automatically deletes data or changes other plugins. Stock Photon resumes after reload.

## Recommended core settings/fixes

These are deliberately **not ported**: `channel_overrides.reasoning_effort`, outbound-only platform entries, media release after delivery, computer-use daemon cleanup, vault login backends, browser window-per-chat routing, sidecar health metrics, and completion-wake message-ID admission. Evaluate them separately against your Hermes version. Do not disable `tool_search` globally as a reaction workaround: the tested host then exposes plugin tool schemas to non-Photon sessions as well.

## License and provenance

MIT; retained Nous Research attribution in LICENSE. The vendored behavior was adapted from the Photon assistant branch's bubbles, reaction, commitment, coordinator, calendar and ranking implementations; implementation identifiers and examples are generic. This repository has no remote configured and has not been pushed or published.
