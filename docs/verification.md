# Verification receipt

Upstream: `963b4e7d5029e8520657a6bf3c8fecb883b7d648` (detached worktree, tracked files unchanged).
Runtime: Python 3.14.7, macOS. Dedicated scratch venv, dependencies pinned in `requirements-test.txt`.

## Invocation

From the plugin repository, with `SCRATCH` set to the operator-provided scratch directory:

```sh
HERMES_HOME="$SCRATCH/photon-test-home" \
PYTHONPATH="$SCRATCH/hermes-main" \
"$SCRATCH/photon-plugin-venv/bin/python" -m pytest tests -q \
  --basetemp="$SCRATCH/photon-pytest"
```

Latest full execution: **106 passed, 1 xfailed in 27.61s**; exit 0.
The strict xfail is `test_required_direct_visibility_on_stock_upstream`: an actual unmet requirement, not parity.

The canonical host runner was inspected but not invoked: it replaces the environment with `env -i`, dropping `HERMES_HOME`/`TMPDIR`/our upstream `PYTHONPATH`, and precompiles the entire upstream checkout. The expressly permitted dedicated-venv alternative preserves the task's state boundaries. No production interpreter dependencies were installed or changed. This is the plugin suite, not the full upstream suite.

Additional checks:

```sh
"$SCRATCH/photon-plugin-venv/bin/python" -m radon cc assistant -s -n D
git -C "$SCRATCH/hermes-main" apply --check "$PWD/upstream-patches/0001-platform-tools-direct.patch"
git diff --check
```

Radon emitted no D-or-higher blocks (CC > 20); patch applicability check passed. The patch was not applied to a checkout. Its actual tool-assembly behavior is separately exercised in memory by `test_proposed_generic_patch_real_assembly`.

## Coverage boundaries

- Real PluginManager directory discovery, isolated unrelated-cwd install, preserved stock CLI/platform metadata, disabled dependency.
- Real model_tools/tool_search classification: stock deferral, unsafe global-search-disable exposure, generic patch's direct Photon-only surface.
- Exact reaction description, arbitrary emoji forwarding, missing message, wrong platform and ungranted consent failures.
- Event prompt/skills and authorization, groups/unknown/missing sender; registered hook DM/cron/child/kanban/session mismatch gates.
- Bubble boundaries, disabled splitting, actual base retry path with only remaining bubbles sent.
- Subprocess commitment CLI request/list/show/cancel with real temporary state/cron stores; commitment ownership, proof and lifecycle tests.
- Dropped-thread proposal persistence/concurrency; coordinator ranking and calendar scoped grants, freshness, cached/Google synthetic subprocess contracts.

Transport sends and Google responses are synthetic; no live-message, live-calendar, production deployment or provider/model-generation test is claimed. Network is refused by the main test fixture, not an OS sandbox for all subprocesses.

## Lessons for later maintainers

Test real directory discovery outside the checkout cwd, not just imports from the root. Verify schema assembly separately from handler dispatch. Do not use profile-cached tool requirement checks as per-session visibility guards. Keep hook context read-only and per-turn; do not inject schemas with middleware unless the host also provides the same owning-agent dispatch authority. `set_session_vars` replaces the binding: supply the whole synthetic identity when changing one negative-test field. An expected failure must remain a release blocker until its host contract is implemented.
