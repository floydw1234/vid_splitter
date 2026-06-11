# Task Plan: Add JSON Output Mode to BVF Probe CLI

## Scope

Ticket: `a7e3bece-930f-4d5b-ab97-ba5e3d8c22a9`

Goal: enhance [`tools/bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_probe.py) with a `--json` flag that emits deterministic machine-readable validation output while preserving the current human-readable default output. Update [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py) to cover valid and invalid JSON output paths.

Out of scope:

- Changing validation semantics beyond what is needed to expose results in JSON.
- Changing exit-code behavior.
- Updating unrelated CLIs.

## Relevant Existing Files

- [`tools/bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_probe.py): current BVF validation CLI and output formatting.
- [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py): current CLI coverage for human-readable probe output.
- [`tools/bvf_player.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_player.py): established repo precedent for `--json` CLI output using deterministic JSON serialization.
- [`tools/test_bvf_player.py`](/tmp/terarchitect_runner_uafd83it/tools/test_bvf_player.py): precedent for asserting full JSON payloads with `json.loads(...)`.

## Constraints and Assumptions

- This ticket is CLI-only; no frontend/UI/E2E work is required.
- No app web service, localhost port, or Docker Compose flow is involved in this change.
- Existing BVF fixture generation in `tests/test_bvf_probe.py` is sufficient; no new external sample data is needed.
- JSON output should be deterministic for agents and tests. Plan to follow the existing repo pattern:
  - emit a single JSON object on `stdout`
  - preserve exit codes
  - serialize with `sort_keys=True`

## TDD Execution Order

### 1. Baseline verification and fixture review

Purpose:

- Confirm the current tests and fixture helpers are enough to drive the change without introducing new test data.
- Verify how `BvfMuxer.read_bvf(...)` exposes the counts needed by the ticket.

Files to inspect:

- [`tools/bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_probe.py)
- [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py)
- [`vid_splitter/bvf_muxer.py`](/tmp/terarchitect_runner_uafd83it/vid_splitter/bvf_muxer.py)

Expected outcome:

- Confirm `segment_count` can be sourced from parsed manifest or header.
- Confirm `profile_count` can be derived from `manifest["profiles"]`.
- Confirm existing `_write_fixture(...)` and `_rewrite_manifest(...)` can cover both valid and invalid JSON cases.

Dependencies:

- None.

### 2. Add failing tests for JSON success output first

Purpose:

- Define the exact machine-readable contract before changing implementation.

Primary file to update:

- [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py)

Test additions:

- Add a test for a valid BVF with `--json`, likely alongside the existing valid probe test.
- Parse `result.stdout` with `json.loads(...)` and assert the full payload, not fragments.

Planned assertions:

- `result.returncode == 0`
- `result.stderr == ""`
- payload matches the required shape exactly:
  - `"path": str(bvf)`
  - `"valid": True`
  - `"profile": "child"` when `--profile child` is supplied
  - `"issues": []`
  - `"segment_count": 2`
  - `"profile_count": 2`

Brief illustrative payload snippet:

```json
{"issues":[],"path":"/tmp/.../fixture.bvf","profile":"child","profile_count":2,"segment_count":2,"valid":true}
```

Why first:

- This locks down the CLI contract and deterministic JSON structure before implementation.

Dependencies:

- Step 1.

### 3. Add failing tests for JSON invalid output

Purpose:

- Define the invalid-path contract before implementation, especially counts and issue handling.

Primary file to update:

- [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py)

Test additions:

- Add a JSON-mode variant of one existing invalid scenario. The best candidate is the current missing swap target case because it already has a focused single validation error.

Planned assertions:

- `result.returncode != 0`
- `result.stderr == ""`
- parsed payload fields:
  - `"path": str(bvf)`
  - `"valid": False`
  - `"profile": "child"`
  - `"issues"` contains the expected swap-target error string
  - `"segment_count": 2`
  - `"profile_count": 2`

Potential second invalid test if needed:

- If implementation needs separate coverage for parse/early-failure behavior, add a second test for a nonexistent file and assert deterministic fallback counts:
  - `"segment_count": 0`
  - `"profile_count": 0`

This second invalid test is optional unless the implementation naturally introduces a separate early-failure path worth locking down.

Why first:

- Invalid machine output is the riskier contract for agent workflows; it needs to be explicit before code changes.

Dependencies:

- Step 2.

### 4. Run the targeted probe tests to confirm they fail for the right reason

Purpose:

- Validate the new tests fail because `--json` is not implemented yet, not because the fixture or assertions are wrong.

Command plan:

```bash
pytest tests/test_bvf_probe.py -q
```

Expected failure mode:

- CLI argument parsing rejects `--json`, or JSON assertions fail because plain text is still emitted.

Dependencies:

- Steps 2 and 3.

### 5. Implement the minimum JSON output support in the probe CLI

Purpose:

- Make the new tests pass with the smallest coherent change set.

Primary file to update:

- [`tools/bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_probe.py)

Implementation plan:

1. Add `import json`.
2. Add `parser.add_argument("--json", action="store_true", ...)`.
3. Refactor validation/result handling so `main()` can emit either human text or JSON without reparsing or duplicating logic.

Planned code-shape changes:

- Introduce a small helper to build a result payload from:
  - input path
  - optional profile
  - issues list
  - parsed BVF data when available
- Prefer a helper return shape like:

```python
{
    "path": str(path),
    "valid": not issues,
    "profile": profile,
    "issues": issues,
    "segment_count": ...,
    "profile_count": ...,
}
```

- Use deterministic JSON serialization:

```python
print(json.dumps(payload, sort_keys=True))
```

- Keep human-readable default behavior unchanged except for any internal refactor needed to avoid reparsing.

Implementation details to decide during coding:

- Whether to evolve `validate_bvf(...)` to return richer data, or add a new helper that wraps validation plus parsed metadata.
- For early failures such as missing file or parse failure, emit deterministic counts of `0` because no manifest data is available.

Dependencies:

- Step 4.

### 6. Re-run targeted tests and iterate to green

Purpose:

- Confirm the new JSON contract passes without regressing the existing human-readable tests.

Command plan:

```bash
pytest tests/test_bvf_probe.py -q
```

Success criteria:

- Existing text-output tests still pass.
- New valid/invalid JSON tests pass.

Dependencies:

- Step 5.

### 7. Refactor only if needed after tests pass

Purpose:

- Clean up implementation details while preserving behavior.

Candidate refactors:

- Remove duplicate `BvfMuxer.read_bvf(...)` calls on the success path.
- Consolidate count derivation in one helper instead of branching in multiple places.
- Keep output-formatting responsibilities isolated:
  - one helper for payload construction
  - one helper for human success message
  - one helper for JSON serialization if that improves clarity

Rule:

- Refactor only after the probe tests are green.

Dependencies:

- Step 6.

### 8. Run broader regression checks appropriate to the touched area

Purpose:

- Verify the CLI change does not break nearby behavior and aligns with existing conventions.

Recommended commands:

```bash
pytest tests/test_bvf_probe.py tools/test_bvf_player.py -q
```

Optional broader pass if time permits:

```bash
pytest -q
```

Rationale:

- `tools/test_bvf_player.py` is the closest existing JSON-output precedent in this repo.
- Full-suite execution is useful but secondary to the targeted CLI coverage for this ticket.

Dependencies:

- Step 7.

## Files Planned To Change

- [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py)
  - Add JSON success test first.
  - Add JSON invalid test next.
  - Keep existing human-readable tests intact.

- [`tools/bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tools/bvf_probe.py)
  - Add `--json` CLI flag.
  - Add payload-building logic for deterministic machine-readable output.
  - Preserve plain text default output and exit codes.

## Dependencies Between Steps

1. Baseline verification informs exact assertions for counts and fallback behavior.
2. JSON success test should be written before implementation.
3. JSON invalid test should be written before implementation.
4. Both new tests should be observed failing before code changes.
5. Implementation should be minimal and target only the failing assertions.
6. Refactoring should happen only after the targeted tests are green.
7. Broader regression checks should run after the implementation stabilizes.

## Test Strategy Summary

Unit/integration scope for this ticket:

- CLI-level tests in [`tests/test_bvf_probe.py`](/tmp/terarchitect_runner_uafd83it/tests/test_bvf_probe.py) are sufficient and already act as lightweight integration tests because they execute the script in a subprocess against generated BVF fixtures.

Test data plan:

- Reuse in-test generated fixture BVFs from `_write_fixture(...)`.
- Reuse manifest mutation helper `_rewrite_manifest(...)` for invalid cases.
- No external downloads, fixed ports, services, or Docker Compose setup required.

## Completion Criteria

The ticket is complete when:

- `tools/bvf_probe.py` accepts `--json`.
- JSON mode emits exactly one deterministic JSON object with:
  - `path`
  - `valid`
  - `profile`
  - `issues`
  - `segment_count`
  - `profile_count`
- Human-readable default output still works as before.
- `tests/test_bvf_probe.py` covers at least one valid JSON path and one invalid JSON path.
- Targeted tests pass, and nearby CLI JSON regression checks pass.
