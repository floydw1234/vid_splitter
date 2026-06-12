# Ticket Plan: a1ac79ae-43d6-46ea-8ee6-b96d94d2e0ac

## Ticket

Add machine-readable JSON output for BVF player list mode by extending `tools/bvf_player.py` so `--list --json` emits deterministic JSON instead of failing. Preserve existing non-JSON `--list` behavior and keep `--dry-run --json` working.

## Scope and Constraints

- Do not change playback, export, or seek behavior.
- Keep JSON output deterministic for scripting and test assertions.
- Avoid introducing a second unrelated JSON schema if the existing dry-run payload can be reused or minimally extended.
- Keep human-readable `--list` output as the default when `--json` is not present.
- This repo does not include Docker or app services for this ticket, so no `docker compose` work is needed.
- Existing tests already use generated BVF fixtures; continue using test data only.

## Files Likely To Touch

- `tools/bvf_player.py`
- `tools/test_bvf_player.py`
- `tests/test_cli_e2e.py`
- `README.md` if CLI examples or `--json` semantics need a brief usage update

## TDD Execution Order

### 1. Lock down current behavior and choose the JSON contract

Goal:
- Confirm the intended output shape for `--list --json` before implementation.

Actions:
1. Re-read the existing JSON-producing code in `tools/bvf_player.py`, especially `get_dry_run_json_payload()` and the `main()` branching for `--list`, `--dry-run`, and `--json`.
2. Decide whether `--list --json` should:
   - reuse the existing dry-run payload exactly, or
   - reuse it with a minimal mode-specific distinction only if required by current list semantics.
3. Prefer the smallest contract change that keeps script consumers consistent across inspection modes.

Dependency:
- This decision must be made before writing assertions in new tests.

### 2. Add a failing unit-style CLI test for `--list --json`

Goal:
- First prove the missing behavior with a focused failing test.

Primary file:
- `tools/test_bvf_player.py`

Actions:
1. Add a new test that invokes:
   - `python tools/bvf_player.py <fixture.bvf> --profile adult --list --json`
2. Parse `stdout` with `json.loads(...)`.
3. Assert the payload is deterministic and matches the agreed schema.
4. Assert `stderr` is empty and the process exits successfully.

Suggested assertions:
- top-level metadata such as `title`, `movie_id`, resolved profile, `total_segments`, `total_duration_ms`
- ordered `segments` array
- per-segment fields matching current resolved sequence semantics, for example:
  - `segment_id`
  - `action`
  - selected/target asset identifier
  - `duration_ms`
  - `start_ms`
  - `end_ms`

Why here:
- `tools/test_bvf_player.py` already contains the closest fixture and the current `--json requires --dry-run` coverage.

Expected initial failure:
- Current parser validation rejects `--json` unless `--dry-run`.

### 3. Replace the old rejection test with a narrower failing validation test

Goal:
- Preserve argument validation, but only for unsupported `--json` combinations.

Primary file:
- `tools/test_bvf_player.py`

Actions:
1. Update the existing test that currently asserts `--json` always requires `--dry-run`.
2. Change it to assert failure only when `--json` is used without any JSON-capable inspection mode, for example:
   - `python tools/bvf_player.py <fixture.bvf> --profile adult --json`
3. Assert:
   - return code remains `2`
   - `stderr` contains updated guidance such as requiring `--dry-run` or `--list`
   - usage text still appears

Why this matters:
- The old test will otherwise block the ticket by encoding the current limitation as desired behavior.

Dependency:
- The exact error string depends on the parser rule chosen in implementation.

### 4. Add an integration-style CLI test for a profile with branching behavior

Goal:
- Verify `--list --json` works on a more realistic sequence, not just the simplest two-segment case.

Primary file:
- `tests/test_cli_e2e.py`

Actions:
1. Add a new test using `_write_cli_fixture(tmp_path)`.
2. Invoke:
   - `python tools/bvf_player.py <fixture.bvf> --profile child --list --json`
3. Parse JSON and assert the output reflects resolved branching behavior, especially where:
   - a narrative segment maps to a filler asset via `swap`
   - sequence order remains narrative order
   - selected asset identifiers reflect the actual playback target
4. Keep the assertions concise and focused on the ticket’s behavior, not every field in the payload.

Why this test:
- `tests/test_cli_e2e.py` already covers the richer swap/filler path for `--dry-run --json`; list-mode JSON should be validated against the same kind of branch resolution.

Expected initial failure:
- Same parser rejection until implementation is added.

### 5. Implement the minimum CLI/parser change to make the new tests pass

Goal:
- Allow `--json` for list mode with minimal code movement.

Primary file:
- `tools/bvf_player.py`

Implementation plan:
1. Relax parser validation in `main()` so `--json` is accepted when either `--dry-run` or `--list` is present.
2. Keep invalid combinations rejected, for example bare `--json` without an inspection mode.
3. Update the `--json` help text to reflect both supported modes.
4. In the `args.list` branch:
   - if `args.json` is set, print deterministic JSON and return
   - otherwise preserve the current human-readable tabular output
5. Reuse an existing payload builder if possible.
6. If reuse is awkward, introduce a small shared helper rather than duplicating serialization logic across `--list` and `--dry-run`.

Preferred shape:
- Use the same payload generator for both `--list --json` and `--dry-run --json` unless there is a concrete semantic mismatch.

Minimum-code principle:
- Avoid modifying `resolve_playback_sequence()` unless the tests show list-mode needs different resolved data than dry-run currently exposes.

### 6. Refactor only if the implementation duplicates JSON assembly

Goal:
- Keep the player maintainable after the feature lands.

Primary file:
- `tools/bvf_player.py`

Actions:
1. If JSON assembly ends up duplicated between branches, extract a small helper with a neutral name such as a shared inspection payload builder.
2. Keep branch logic in `main()` simple:
   - `--list --json` -> print JSON payload
   - `--list` -> print human-readable text
   - `--dry-run --json` -> print same JSON payload
   - `--dry-run` -> print existing human-readable summary
3. Do not refactor unrelated playback/export code.

Dependency:
- Only do this if needed after the initial green test run.

### 7. Update documentation if behavior becomes user-facing enough to warrant it

Goal:
- Keep CLI usage discoverable for operators and agents.

Primary file:
- `README.md`

Actions:
1. Add or adjust one short example showing `--list --json`.
2. If appropriate, clarify that `--json` is intended for inspection modes rather than playback.
3. Keep the doc edit small and consistent with current README style.

Dependency:
- Only after the code path and payload shape are settled.

## Verification Plan

Run targeted tests first:

1. `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tools/test_bvf_player.py -q`
2. `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_cli_e2e.py -q`

Then run the combined relevant suite mentioned by the repo:

3. `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_bvf_muxer.py tools/test_bvf_player.py tests/test_cli_e2e.py`

Optional manual smoke checks after tests pass:

1. `python3 tools/bvf_player.py <fixture-or-demo.bvf> --profile adult --list`
2. `python3 tools/bvf_player.py <fixture-or-demo.bvf> --profile adult --list --json`
3. `python3 tools/bvf_player.py <fixture-or-demo.bvf> --profile adult --dry-run --json`
4. `python3 tools/bvf_player.py <fixture-or-demo.bvf> --profile adult --json`

Manual expectations:
- plain `--list` remains human-readable
- `--list --json` emits valid JSON only on stdout
- `--dry-run --json` remains backward compatible
- bare `--json` still fails with usage guidance

## Dependencies and Ordering Summary

1. Decide the JSON contract using the existing dry-run payload as the baseline.
2. Add the new failing unit-style CLI test for `--list --json`.
3. Update the old rejection test to encode the new intended validation rule.
4. Add the richer integration-style CLI test for swap/filler behavior.
5. Implement the smallest parser and branch changes in `tools/bvf_player.py`.
6. Refactor only if JSON payload generation is duplicated.
7. Update README only after behavior is stable.
8. Run the targeted and combined test suites.

## Risks To Watch During Implementation

- Accidental schema drift between `--list --json` and `--dry-run --json`
- Breaking the existing `--dry-run --json` tests while widening validation
- Mixing human-readable output with JSON on stdout
- Changing field names in a way that would break existing script consumers
