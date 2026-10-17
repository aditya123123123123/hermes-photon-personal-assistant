# Set up Photon Personal Assistant

This is an optional Hermes add-on, not an iMessage service or a replacement for Photon. Read [disclosures](DISCLOSURES.md) before enabling it. The repository is currently local and unpublished; catalog files are drafts, not evidence of approval.

## 1. Prepare Hermes and Photon

Use Hermes **0.22.2 or newer** in the profile where you want this assistant. Check `hermes version`. A profile is a separate set of settings, accounts and history; keep the same profile selected for every command below. When using `HERMES_HOME`, point it at that profile before starting the commands.

Photon must already have its Spectrum project and the Node sidecar required by Hermes' bundled Photon integration. Run:

```sh
hermes plugins enable photon-platform
hermes photon setup
```

Follow Photon setup's instructions for project credentials, sidecar and authorized senders. Never paste credentials into a conversation. Verify ordinary Photon messaging works before adding this plugin; doctor checks configuration presence, not connectivity, Node health or account authentication.

This plugin requires the default in-process plugin mode:

```yaml
plugins:
  isolation: in_process
```

Merge this into your existing configuration; do not replace unrelated settings. If an administrator requires host isolation, ask them rather than bypassing that policy.

## 2. Install and enable

Once the repository has actually been published, use its reviewed Git URL:

```sh
hermes plugins install <reviewed-git-url>
hermes plugins enable photon-personal-assistant
```

For the current local checkout, use the local-link instructions in [README](README.md#install). Do not try installing the catalog draft's placeholder SHA.

## 3. Grant reaction permission

Run the enable command **interactively in a terminal** and review the consent question for `gateway.platform_actions`. Grant it if you want reactions. It permits platform actions; the plugin itself uses it for Photon reactions only.

```sh
hermes plugins enable photon-personal-assistant
hermes plugins capabilities photon-personal-assistant
```

`capabilities` displays grants; it does not grant them. Declining consent leaves reaction execution disabled. Non-interactive installation does not grant consent. You can return to interactive enable later; do not hand-edit a consent hash.

## 4. Restart and start a NEW conversation

Restart your gateway through its normal operator-controlled service workflow (for a Hermes-managed gateway, `hermes gateway restart`). This can interrupt active work, so choose an appropriate time. In Photon, start a **new** Hermes conversation using `/new`; an old conversation may retain its old tool surface and prompt.

The plugin supplies the generic `photon-personal-assistant:texting-assistant` skill. Authorized DM guidance asks the model to load it; you do not need to install a private personal-assistant skill.

## 5. Run the offline checks

```sh
hermes assistant doctor
hermes assistant doctor --json
```

Each line explains a check and a fix or optional setting. `✓` means the local prerequisite is satisfied, `⚠` means a limitation or optional incomplete feature, and `✗` needs attention. Exit status is 1 if any check fails, otherwise 0. A green result is not proof of actual message delivery or Google access.

The checker reads the active profile, uses Hermes' scoped secret accessor for presence only, and never displays credential values or recipient IDs. It does not call Photon, refresh OAuth, launch the calendar child, create stores/jobs or change configuration. Hermes' general CLI bootstrap can itself create normal host metadata before dispatch; the diagnostic handler does not. For a completely non-bootstrapping developer probe, `python -m assistant.doctor` runs from the checkout with Hermes on `PYTHONPATH`; that mode requires a host-provided secret scope or environment and does not load `.env` itself.

If the plugin is disabled, its CLI cannot be discovered: enable it first. An old Hermes version can refuse to load it before doctor is available; update Hermes first.

## Optional: calendar context

Calendar context is off by default. If wanted, load the bundled `google-workspace` skill and complete its existing OAuth setup **for this profile**. This plugin does not sign you in, discover accounts or copy credentials. The existing Workspace integration may refresh its own token during actual calendar reads; doctor only checks token-file presence, not validity.

Merge this into the plugin's settings, replacing the identity placeholders privately with the exact authorized DM and sender:

```yaml
plugins:
  entries:
    photon-personal-assistant:
      settings:
        calendar:
          enabled: true
          approval_granted: true
          provider: google_workspace
          calendar_id: primary
          approved_scopes:
            - chat_id: '<exact Photon DM GUID>'
              user_id: '<exact sender ID>'
```

These are trusted configuration grants, not message instructions. Context is read-only and bounded; it never permits calendar writes or automatic reminders. Missing or stale data means unknown, not “free all day.” See README for the cached-file alternative.

## Optional: follow-through and bubbles

`owner: auto` chooses `native` for a fresh profile. It chooses `legacy` when a `messaging-personal-assistant` skill or `scripts/messages_followthrough.py` already exists. The bundled texting skill has a different name and does not trigger this detection.

Inspect with `hermes assistant commitment owner`. To switch from legacy, first reconcile existing records/jobs and retire the legacy executable, then use a local terminal:

```sh
hermes assistant commitment owner --select native --expected legacy
```

The command refuses unresolved work and concurrent changes. Nothing migrates automatically; use the actual current owner shown by doctor for `--expected`.

Set `plugins.entries.photon-personal-assistant.settings.bubble_max_chars: 600` for the default soft bubble limit, or `0` to disable splitting. This is a profile-wide plugin setting, not a per-chat override.

## Optional: per-chat settings

Existing Photon `channel_prompts`, channel skill bindings and `channel_overrides` remain Hermes settings. For example, you can add a short channel prompt under `platforms.photon.channel_prompts` keyed by the exact private chat ID. Do not place real IDs in public bug reports. Use `/reasoning low` in a conversation for lower reasoning effort; avoid adding a `reasoning_effort` channel override until your host supports it.

## Known limitations on stock Hermes

- **Reactions are deferred.** Stock tool search hides `photon_react` behind `tool_describe` / `tool_call`, so spontaneous reactions are unlikely. Current workaround: let the model explicitly discover and call that tool when appropriate; do not disable tool search globally, which exposes plugin schemas in unrelated sessions. Advanced maintainers can review the unapplied `upstream-patches/0001-platform-tools-direct.patch`; it is not installed by this plugin. This will be **fixed automatically when Hermes ships <PR> (TODO: PR link)**. Doctor calls the host's `is_deferrable_tool_name` and turns green when that contract changes.
- **Per-chat reasoning overrides are ignored.** Stock `ChannelOverride` lacks `reasoning_effort`; setting it under a Photon `channel_overrides` entry silently does nothing. Current workaround: use `/reasoning low` in that chat, or intentionally choose a profile-wide reasoning setting. This will be **fixed automatically when Hermes ships <PR> (TODO: PR link)**. Doctor warns only if you configured the unsupported override.
- **Photo replies can be slow with a large `state.db`.** Stock media cleanup can run before delivery. This plugin cannot move the cleanup. Current workaround: wait for delivery and avoid duplicate retries; use the host's documented maintenance/backup workflow rather than deleting the database. This will be **fixed automatically when Hermes ships <PR> (TODO: PR link)**. This limitation is documentation-only, not a doctor performance probe.

## Removing the plugin

Reconcile or cancel reminders through their owning workflow first. Disable with `hermes plugins disable photon-personal-assistant`, restart normally, then remove the install/link. Disabling the plugin does not delete jobs or saved records. Keep data until it is reconciled.
