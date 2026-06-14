## Ticket

- ID: `bf3cf00e-3f77-4488-adca-d57004bc1e3d`
- Title: `Smoke 03: add topic classifier comment`
- Scope: Add exactly one small clarifying comment near the top of `analyzer/topic_classifier.py` explaining that topic labels are intentionally lightweight heuristics, with no runtime behavior change.

## Constraints And Assumptions

- This is a comment-only change in production code, so implementation risk is low and no functional behavior should change.
- The requested work does not justify a new failing test because it does not alter executable behavior.
- There is no frontend, UI, E2E, service, or Docker Compose requirement relevant to this ticket because the requested change does not touch networked runtime behavior.

## Files Expected To Change

- `analyzer/topic_classifier.py`

## Step-By-Step Execution Plan

1. Inspect the current top-of-file structure in `analyzer/topic_classifier.py`.
   - Confirm the existing comment above `TOPIC_TAXONOMY` and identify the least disruptive place for the new clarifying comment.
   - Dependency: none.

2. Implement the minimum production change in `analyzer/topic_classifier.py`.
   - Add exactly one tiny clarifying comment near the top of the file, adjacent to the `TOPIC_TAXONOMY` block.
   - Preserve all existing code, imports, constants, and prompt logic.
   - Do not alter whitespace or surrounding lines more than needed for the single comment insertion.
   - Dependency: Step 1.

3. Verify the change with a simple file diff.
   - Check `git diff -- analyzer/topic_classifier.py` or equivalent.
   - Confirm there is exactly one clarifying comment added in production code and no runtime behavior changes.
   - Dependency: Step 2.

## Test Plan

- No automated test changes are planned because the ticket is strictly comment-only and must not affect runtime behavior.
- Verification will be diff-based:
  - `git diff -- analyzer/topic_classifier.py`
  - Confirm the diff shows only the single intended clarifying comment.

## Dependencies Between Steps

- The production comment edit depends only on confirming the correct insertion point.
- Diff verification depends on the comment having been added first.

## Out Of Scope

- No integration test environment, sample data, Docker Compose setup, or dynamic port handling is needed for this ticket because no service interaction is involved.
- No frontend or UI automation work is needed.
- No changes to tests, `analyzer/analyze.py`, `README.md`, or taxonomy behavior are planned.
