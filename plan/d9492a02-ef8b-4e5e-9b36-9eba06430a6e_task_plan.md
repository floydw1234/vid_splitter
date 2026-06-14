# Ticket d9492a02-ef8b-4e5e-9b36-9eba06430a6e Task Plan

## Goal
Add exactly one tiny clarifying comment near the top of `tools/bvf_probe.py` explaining that the probe reports BVF structure for diagnostics, without changing runtime behavior.

## Constraints and scope
- Keep the implementation to a comment-only change in `tools/bvf_probe.py`.
- Preserve all runtime behavior, CLI behavior, and existing validation output.
- Follow TDD even for this smoke change by first adding a test that fails against the current file contents, then making the smallest edit to pass it.
- No frontend work is involved.
- No Docker Compose setup is present in this repository, so containerized integration steps are not applicable for this ticket.
- No web-service ports are involved; dynamic localhost port planning is not applicable.

## Files expected to touch
- `tests/test_bvf_probe.py`
- `tools/bvf_probe.py`
- `plan/d9492a02-ef8b-4e5e-9b36-9eba06430a6e_task_plan.md`

## Order of work

### 1. Confirm baseline and choose the narrowest test seam
- Re-read the top of `tools/bvf_probe.py` and the existing CLI-oriented tests in `tests/test_bvf_probe.py`.
- Confirm the current top-of-file text does not already contain the required clarifying phrase about reporting BVF structure for diagnostics.
- Use the existing probe test module as the test location because it already covers the script and keeps the smoke change localized.

Dependency:
- This step informs the exact assertion to add in the next step.

### 2. Add a failing test first
- Update `tests/test_bvf_probe.py` with one new test that inspects `tools/bvf_probe.py` as plain text and asserts the required clarifying comment is present near the top.
- Keep the test narrow and content-focused so it verifies the ticket requirement without coupling to unrelated formatting.
- Preferred assertion shape:
  - read the file text
  - inspect only the opening lines or a short prefix
  - assert a brief phrase such as `reports BVF structure for diagnostics` is present
- Keep this as a unit-style repository-content test rather than a behavioral integration test, because the ticket is explicitly about source commentary and must not alter runtime behavior.

Files:
- `tests/test_bvf_probe.py`

Brief snippet example:
```py
text = Path(... / "tools" / "bvf_probe.py").read_text()
assert "reports BVF structure for diagnostics" in "\n".join(text.splitlines()[:10])
```

Dependency:
- Must be added before editing `tools/bvf_probe.py` so the test fails on current contents.

### 3. Run the targeted test and observe failure
- Run the new targeted test from `tests/test_bvf_probe.py`.
- Expect failure because the current file header does not yet include the required clarifying comment text.
- Capture the failure only as confirmation that the test is meaningful; no code changes yet.

Suggested command:
- `pytest tests/test_bvf_probe.py -k diagnostic`
  - Final test name may determine the exact `-k` expression.

Dependency:
- Confirms the test is red before implementation.

### 4. Implement the minimum comment-only change
- Edit `tools/bvf_probe.py` near the top of the file.
- Add or adjust exactly one tiny clarifying comment so the header now explains that the probe reports BVF structure for diagnostics.
- Keep the edit confined to a single comment line if possible.
- Do not change the shebang, imports, logic, CLI arguments, return codes, or output strings.
- Prefer placing the clarifying text adjacent to the existing file header comments so the purpose is visible immediately.

Files:
- `tools/bvf_probe.py`

Implementation target:
- One short comment line near lines 2-5, e.g. wording equivalent to `Reports BVF structure for diagnostics.`

Dependency:
- This is the minimum code change required to satisfy the failing test.

### 5. Re-run the targeted test to confirm it passes
- Run the same focused test used in step 3.
- Verify the new content assertion now passes.

Suggested command:
- `pytest tests/test_bvf_probe.py -k diagnostic`

Dependency:
- Confirms the source-level requirement is met.

### 6. Run the broader probe test module to guard against regressions
- Run all tests in `tests/test_bvf_probe.py` after the comment change.
- This acts as the practical integration check for the script because these tests execute the CLI against generated BVF fixture data and validate stdout/JSON behavior.
- Since the change is comment-only, no fixture additions should be necessary.

Suggested command:
- `pytest tests/test_bvf_probe.py`

Integration-test note:
- Existing fixture generation in `tests/test_bvf_probe.py` already provides sufficient test data for the important scenarios this script covers.
- No external services, ports, seed DBs, or downloaded sample assets are needed.

Dependency:
- Ensures runtime behavior remains unchanged.

### 7. Refactor only if the test is too brittle
- If the initial content assertion is overly sensitive to whitespace or exact line positions, relax it slightly while still verifying the ticket requirement.
- Do not broaden the implementation beyond the single required comment.
- If no brittleness appears, skip refactoring.

Dependency:
- Only needed if test stability is poor after step 6.

### 8. Final verification and review
- Review the final diff to ensure only the intended files changed.
- Confirm the implementation still satisfies all ticket constraints:
  - exactly one tiny clarifying comment near the top of `tools/bvf_probe.py`
  - explains BVF structure diagnostics purpose
  - no runtime behavior changes
- Optionally run `git diff --stat` and inspect the patch for accidental edits.

## Test plan summary
- Add/update unit-style source-content test:
  - `tests/test_bvf_probe.py`: new test asserting the top of `tools/bvf_probe.py` contains the diagnostics-purpose comment.
- Run targeted red/green cycle:
  - `pytest tests/test_bvf_probe.py -k diagnostic`
- Run broader regression coverage:
  - `pytest tests/test_bvf_probe.py`

## Dependencies between steps
1. Baseline inspection must happen before writing the assertion.
2. The new test must fail before editing `tools/bvf_probe.py`.
3. The comment edit depends on the failing test being in place.
4. Full probe test-module regression run depends on the targeted test passing.
5. Refactor is optional and only follows successful behavior-preservation checks.

## Non-applicable items for this ticket
- Docker Compose integration setup: repository does not appear to include compose files.
- Dynamic localhost port allocation: no app server or browser-driven test flow is involved.
- UI/E2E automation: no frontend is touched by this ticket.
