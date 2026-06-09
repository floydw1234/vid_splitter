# Task Plan: Add machine-readable BVF player dry-run JSON output

## Scope and constraints

- Ticket target: [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py)
- Primary test files to update:
  - [tools/test_bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/test_bvf_player.py)
  - [tests/test_cli_e2e.py](/tmp/terarchitect_runner_s_3cqs8w/tests/test_cli_e2e.py) if a CLI-level smoke test adds value
- No `docker-compose.yml` or similar compose file exists in this repo, so `docker compose up -d` / `down` is not applicable for this ticket.
- No frontend/UI code is involved, so UI/E2E browser test work is not applicable.
- Test data strategy: use existing lightweight temporary BVF generation via `BvfMuxer`; do not add heavyweight media fixtures unless a CLI smoke test requires generated demo data.

## Proposed JSON contract

- Add a `--json` CLI flag that is only valid with `--dry-run`.
- Preserve current human-readable `--dry-run` output when `--json` is absent.
- Emit a deterministic JSON object to stdout when `--dry-run --json` is used.
- Base the payload on resolved playback order, not raw manifest order alone.
- Include:
  - resolved profile
  - top-level movie/title summary
  - total segment count
  - total duration
  - ordered selected segment list
  - selected asset information per segment
- Keep field names stable and explicit, for example brief shapes like:
  - top level: `"resolved_profile": "child"`
  - segment entry: `"segment_id": "seg_002", "selected_asset_id": "filler_001"`

## Order of work

1. Review and pin current dry-run behavior
- Re-read the current `main()` dry-run branch in [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py:770).
- Re-read current playback metadata helper in [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py:621).
- Confirm which manifest fields already exist and can be surfaced without changing BVF format:
  - `start_ms`, `end_ms`
  - `media.asset_id`, `media.container`, `media.mime_type`
  - per-profile `action` / `segment_id`

2. First failing test: unit-level JSON payload shape
- File to touch: [tools/test_bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/test_bvf_player.py)
- Add a new test using the existing temporary BVF fixture helper or a small variant with swap behavior.
- Write the test first so it fails against current code.
- Validate the core serialized payload shape via a new player helper rather than shelling out immediately.
- Expected assertions:
  - payload contains resolved profile
  - payload contains deterministic ordered segments
  - each segment includes narrative segment id, action, selected target/asset id, duration, and timeline fields
  - selected asset metadata comes from the manifest/index, not from extraction side effects
- If needed, add a dedicated fixture with a `swap` mapping so the test can prove that `"segment_id"` and selected asset id differ.

3. Minimum implementation for unit test
- File to touch: [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py)
- Add a dedicated helper for machine-readable dry-run data, likely separate from `get_playback_info()` to avoid breaking existing callers.
- Keep the helper read-only: no extraction, no temp files, no playback.
- Reuse `resolve_playback_sequence()` for ordering and runtime action filtering.
- Pull asset metadata from manifest entries and/or segment index so the JSON includes selected segment/asset information in one place.
- Build dicts in a fixed field order; optionally serialize with `json.dumps(..., sort_keys=True)` if needed for stronger determinism in CLI output and tests.

4. Refactor after the first passing unit test
- If the new helper overlaps heavily with `get_playback_info()`, refactor shared summary computation into a small internal helper.
- Keep refactor minimal: do not change text dry-run formatting yet.
- Re-run the unit test file to confirm behavior stayed stable.

5. Second failing test: CLI flag validation
- Preferred file: [tools/test_bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/test_bvf_player.py) if parser logic is tested directly.
- Alternative file: add a small subprocess CLI test under [tests/test_cli_e2e.py](/tmp/terarchitect_runner_s_3cqs8w/tests/test_cli_e2e.py) if parser testing is awkward.
- Write a failing test first for invalid combinations:
  - `--json` without `--dry-run` should exit with a CLI usage error
- Assert non-zero exit and stderr/usage text rather than a generic exception.

6. Minimum implementation for CLI validation
- File to touch: [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py)
- Add `--json` to `build_parser()`.
- In `main()`, validate the flag combination immediately after parsing:
  - if `args.json` and not `args.dry_run`, call `parser.error(...)`
- Keep behavior explicit rather than silently ignoring `--json`.

7. Third failing test: CLI JSON dry-run output
- File to touch: [tests/test_cli_e2e.py](/tmp/terarchitect_runner_s_3cqs8w/tests/test_cli_e2e.py) or `tools/test_bvf_player.py` if subprocess coverage can stay lightweight there.
- Prefer a lightweight subprocess test using temporary BVF data if possible; use generated demo media only if necessary.
- Write the test first so it fails against current behavior.
- Assertions:
  - `python tools/bvf_player.py <fixture> --dry-run --json` returns valid JSON via `json.loads(...)`
  - payload matches resolved profile and expected segment count
  - swap case reports the selected filler asset in JSON
  - stdout contains JSON only, with no trailing human-readable dry-run banner

8. Minimum implementation for CLI JSON branch
- File to touch: [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py)
- Update the `if args.dry_run:` branch:
  - when `--json` is absent, preserve the current human-readable output exactly
  - when `--json` is present, serialize and print the structured payload only
- Ensure JSON mode does not emit extra text to stdout.
- Check whether verbose logging needs to be redirected or suppressed in JSON mode so the output remains parseable.

9. Regression test for existing human-readable dry-run behavior
- File to touch: [tests/test_cli_e2e.py](/tmp/terarchitect_runner_s_3cqs8w/tests/test_cli_e2e.py) only if needed
- Keep or tighten one existing text dry-run assertion to prove default behavior is unchanged.
- Example assertion style:
  - existing `--dry-run` still prints `"Profile: child"`
  - existing `--dry-run` still does not print raw JSON braces as the main output

10. Optional small documentation update
- File to touch: [README.md](/tmp/terarchitect_runner_s_3cqs8w/README.md)
- Add one example command for agent-friendly output, for example:
  - `python3 tools/bvf_player.py /tmp/bvf-demo/demo.bvf --user-json examples/child_user.json --dry-run --json`
- Keep this step last and only if project docs are expected to reflect new CLI surface area.

## Test plan in TDD sequence

1. Run focused existing tests before changes
- `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tools/test_bvf_player.py`
- Optional baseline:
  - `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_cli_e2e.py -k dry_run`

2. Add and run the new failing unit test for JSON payload structure
- Target:
  - `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tools/test_bvf_player.py -k json`

3. Implement minimum code and re-run the focused unit test until green

4. Add and run the new failing CLI validation test for `--json` without `--dry-run`

5. Implement minimum parser/main validation and re-run the focused CLI test until green

6. Add and run the new failing CLI JSON-output test
- If the test can use lightweight temporary BVF data, keep it in the fast test path.
- If subprocess wiring requires richer generated data, generate only what is required in the test itself.

7. Implement minimum CLI JSON branch and re-run:
- `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tools/test_bvf_player.py tests/test_cli_e2e.py`

8. Run broader regression coverage relevant to the touched area
- `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tools/test_bvf_player.py tests/test_cli_e2e.py tests/test_bvf_probe.py`

## File-by-file change plan

- [tools/bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/bvf_player.py)
  - add `--json` parser flag
  - add CLI validation for `--json` requiring `--dry-run`
  - add a structured dry-run payload helper
  - add JSON-only output path under `--dry-run`
  - preserve default text dry-run behavior

- [tools/test_bvf_player.py](/tmp/terarchitect_runner_s_3cqs8w/tools/test_bvf_player.py)
  - add unit tests for structured dry-run payload shape
  - add a swap-focused fixture/test if current helper is insufficient
  - optionally add parser-level invalid combination coverage if done without subprocess

- [tests/test_cli_e2e.py](/tmp/terarchitect_runner_s_3cqs8w/tests/test_cli_e2e.py)
  - add subprocess test for `--dry-run --json`
  - optionally add subprocess validation test for invalid `--json` usage
  - retain assertions proving current text dry-run remains unchanged by default

- [README.md](/tmp/terarchitect_runner_s_3cqs8w/README.md)
  - optional example update for the new agent-facing flag

## Dependencies and decision points

- The JSON schema should be decided before writing the first unit test, because the test should lock field names and ordering expectations.
- If `get_playback_info()` is expanded instead of adding a new helper, confirm no existing tests or callers depend on its current minimal shape.
- If verbose output currently goes to stdout, decide whether JSON mode will:
  - reject `--verbose` with `--json`, or
  - redirect verbose diagnostics to stderr
- Prefer lightweight fixture generation in `tools/test_bvf_player.py`; use ffmpeg-backed CLI E2E tests only when subprocess coverage adds behavior that unit tests cannot prove.

## Done criteria

- `--dry-run` without `--json` produces the same human-readable output as before.
- `--dry-run --json` prints valid deterministic JSON only.
- `--json` without `--dry-run` fails with a clear CLI usage error.
- Tests cover:
  - resolved profile in JSON
  - ordered selected segment list
  - selected asset/target information, including a swap case
  - default text dry-run regression
