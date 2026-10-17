# Installation and catalog disclosures

This is a local, unpublished community plugin, not a Nous-approved listing. The catalog YAML is a draft with a deliberately invalid SHA placeholder. Its repository URL is a requested future location, not proof the repository exists. Replace the SHA only after publication/review is separately authorized.

## Network and execution

- No telemetry, analytics, self-update, remote code loader or additional network service is installed.
- Messages and reactions go through Hermes' existing Photon adapter/Node sidecar and Spectrum integration. The model/provider traffic is Hermes' normal traffic, not a new plugin endpoint.
- Optional calendar reads use the existing bundled Google Workspace integration for Google Calendar and its normal OAuth refresh. There are no calendar writes, account discovery or standalone credential clients.
- Calendar reads spawn a bounded Python child process with an eight-second timeout; no shell is used. The child imports the existing Workspace skill client. No extra daemon is started. Doctor never launches that child or calls a sidecar; the host version resolver may run local read-only Git commands.
- The unapplied upstream patch is review material, not runtime code that modifies Hermes.

## Credentials and data

- The plugin does not directly open credential stores or handle OAuth tokens. Doctor asks Hermes' scoped `get_secret` API only whether Photon fields are present, and checks the existence (not contents) of the selected profile's `google_token.json`. Normal Workspace OAuth refresh may write its own token file.
- Trusted configuration, current event/session bindings, and the host's profile-local `state.db` establish authorization. Current message text is used for acceptance provenance. Calendar cached JSON is read only when configured, and artifact-proof commands read the explicitly supplied local artifact. These paths can be outside the plugin data directory.
- Plugin writes: profile-local `photon-personal-assistant/store.json`, atomic temporary/lock files; Hermes' standard profile-local cron job store for explicitly accepted reminders; profile-local owner-selection lock and configuration only through the guarded local operator owner command. Normal Hermes bootstrap can write its own metadata/backups. No writes occur in the doctor handler.
- Proposals and accepted records can contain user message-derived task text and identifiers. Protect/back up the profile as sensitive local data. Disabling/removing the plugin does not delete or reconcile stored records and jobs.
- Privileged manifest capability: `gateway.platform_actions`. The catalog `capabilities` mapping mirrors tool/hook/middleware/env declarations; host consent is documented in a comment because the catalog schema has no host-capability field. No plugin-owned environment variables or credentials are required: Photon owns its project credentials.

## Catalog rule 15: no skins or forks of bundled plugins

**Admission is not established and is likely blocked in the current architecture.** The plugin subclasses `plugins.platforms.photon.adapter.PhotonAdapter`, collects the bundled registration, and re-registers the same `photon` platform with that subclass. Public registration and a passing “no core override” check do not exempt this from rule 15. Calling it an assistant rather than a Photon fork does not change the architecture.

The useful independent contribution is approval-gated commitments, read-only recall/calendar context and generic texting guidance. Suggested framing for review is “an assistant extension using Photon,” with the adapter replacement disclosed explicitly—not a new Photon transport or a vendor-branded skin. Maintainers must decide admission; do not submit the current catalog draft as compliant by default.

Preferred upstream split: contribute bubble delivery/resume behavior and Photon reaction support to the bundled adapter/platform-action facade; contribute platform-scoped direct tool visibility and needed event-local prompt hooks to Hermes. Then retain the independent commitment/context/skill logic as a plugin using those public seams, without subclassing or re-registering the bundled adapter. Per-chat reasoning and media-cleanup ordering are separate host fixes. All PR links remain TODO; nothing has been submitted.

## Remaining integration risk

See [compatibility notes](docs/compatibility.md) for each remaining private-core dependency and why an equivalent public context API is not currently sufficient. `plugins.isolation: in_process` is required; this is trusted Python code in the host, not a sandbox. Offline tests do not prove live delivery, authentication, physical iMessage behavior or admission compliance.
