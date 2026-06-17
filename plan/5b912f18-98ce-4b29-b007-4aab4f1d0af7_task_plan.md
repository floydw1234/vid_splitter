# Task Plan: Smoke comment marker in `analyzer/filler.py`

## Objective
Add one comment-only smoke marker near the top of `analyzer/filler.py` and keep runtime behavior unchanged.

## Scope
- Files to touch:
  - `tests/test_filler.py`
  - `analyzer/filler.py`
- No integration, UI/E2E, or service orchestration work is needed because this is a source-comment-only change.

## Step-by-step TDD plan

1. Update `tests/test_filler.py` first with a failing assertion that specifically requires the new smoke marker in `analyzer/filler.py`.
   - Add or extend a test near the top of the file so it checks the source text, not runtime behavior.
   - Keep the existing behavior checks in place, including the `__doc__ is None` assertion, so the ticket stays comment-only.
   - The new assertion should look for the marker text near the header, similar to:
     - `assert "smoke comment marker" in content.lower()`
   - If the test wants to be stricter, also verify the marker appears before the first substantial code block, but do not over-constrain formatting beyond what the ticket needs.
   - Dependency: this test should fail before the code change, proving the marker is actually missing.

2. Make the minimum code change in `analyzer/filler.py` to satisfy the new test.
   - Insert a single comment-only marker near the file header, alongside the existing header comment and before executable code.
   - Keep the change non-functional:
     - no docstring
     - no imports
     - no logic changes
     - no formatting refactor beyond inserting the comment
   - Use the same lightweight style already used elsewhere in the repo for smoke markers, so the file stays consistent with `analyzer/analyze.py`.

3. Run the focused test set after the code change.
   - Primary verification:
     - `pytest tests/test_filler.py`
   - If the new assertion was placed in a shared analyzer-package test instead, run that file as well, but keep the verification focused on the touched scope.
   - Confirm the test fails before the code change and passes after the code change.

4. Do a small cleanup pass only if needed.
   - If the new test is too broad or brittle, tighten it so it checks the marker text without depending on unrelated line numbers or exact spacing.
   - Avoid any refactor that changes behavior or spreads the edit outside the two planned files.

## Ordering and dependencies
- Step 1 must happen before Step 2 because the ticket uses TDD and needs a failing test first.
- Step 2 depends on Step 1’s expectation and should be the smallest possible source edit.
- Step 3 depends on both Step 1 and Step 2 and is the proof that the change is complete.
- Step 4 is optional and only used if the test or comment needs minor adjustment after verification.

## Verification criteria
- `tests/test_filler.py` passes.
- `analyzer/filler.py` contains exactly one new comment-only smoke marker near the header.
- Runtime behavior of `pick_filler_window`, `extract_filler_clip`, and the CLI remains unchanged.
- No additional files are modified.

## Notes
- No test data, fixture, docker, or external service setup is required for this ticket.
- No UI/E2E coverage is relevant because the change does not touch the frontend.
