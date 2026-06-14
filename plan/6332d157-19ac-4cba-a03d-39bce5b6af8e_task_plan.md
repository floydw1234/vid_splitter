## Ticket

`6332d157-19ac-4cba-a03d-39bce5b6af8e` - Smoke 10: clarify plugin README comment

## Scope Summary

The ticket is explicitly limited to a tiny documentation/comment-only change in `csharp_plugin/README.md`. The change must identify the file as concurrency smoke ticket 10 without altering plugin behavior, setup requirements, or rendered user guidance.

## Constraints And Repo Context

- Target file: `csharp_plugin/README.md`
- No code or runtime behavior changes are expected.
- No `docker-compose` or other compose files are present in the repo, so the Docker/service startup guidance does not apply to this ticket.
- No frontend/UI test framework is present for this area.
- Existing automated coverage is primarily Python `pytest` plus C# plugin tests; there is no existing documentation-specific test.

## TDD Execution Plan

### 1. Baseline inspection

Order:
1. Re-open `csharp_plugin/README.md` and confirm the exact insertion point near the top of the file.
2. Verify whether a `plan/` directory already exists and keep the change isolated to the README plus any narrowly-scoped test file.

Dependencies:
- None.

Files to inspect:
- `csharp_plugin/README.md`
- `README.md`
- `tests/`

### 2. Add a failing regression test first

Intent:
- Create a minimal automated guard that proves the README contains the required smoke marker and that the marker uses a harmless Markdown comment rather than visible rendered text.

Planned test change:
1. Add a new pytest file such as `tests/test_csharp_plugin_readme.py`.
2. Write a test that reads `csharp_plugin/README.md` and fails until the marker exists.
3. Assert a narrow condition, for example:
   - the file contains an HTML comment snippet like `<!-- ... -->`
   - the comment text references `concurrency smoke ticket 10`
4. Keep the assertion specific enough to protect the ticket requirement, but not so brittle that unrelated README edits break it.

Why this is the right “test first” step:
- There is no runtime behavior to validate.
- A lightweight file-content test is the smallest meaningful regression check for a docs-only ticket.

Files to touch in this step:
- `tests/test_csharp_plugin_readme.py` (new)

Expected initial result:
- The new pytest test fails because the README does not yet contain the smoke-ticket comment.

### 3. Implement the minimum README change to satisfy the test

Intent:
- Add the smallest possible harmless Markdown note that fulfills the ticket without changing rendered instructions.

Planned implementation:
1. Edit `csharp_plugin/README.md`.
2. Insert a short HTML comment near the top of the file, ideally immediately below the main title.
3. Use wording that identifies the ticket and clarifies it is docs-only, for example a short snippet shaped like:
   - `<!-- Concurrency smoke ticket 10 marker; docs-only note. -->`
4. Avoid modifying visible prose, prerequisites, commands, or behavior descriptions.

Files to touch in this step:
- `csharp_plugin/README.md`

Expected result:
- The new regression test passes.

### 4. Refactor only if needed

Intent:
- Keep the test and documentation change minimal.

Planned refactor checks:
1. Review the test name and assertion wording for clarity.
2. Confirm the test does not over-constrain line numbers or exact surrounding formatting.
3. Avoid any additional README cleanup unless required to keep the comment placement sensible.

Dependencies:
- Depends on step 3 completing.

Files that may be touched:
- `tests/test_csharp_plugin_readme.py`

### 5. Verification

Order:
1. Run the new focused pytest test:
   - `python -m pytest tests/test_csharp_plugin_readme.py`
2. Optionally run a small adjacent subset if the test harness requires confidence that nothing else was impacted:
   - `python -m pytest tests/test_cli_e2e.py` is not necessary for this docs-only ticket.
3. Confirm the README still renders as normal in raw Markdown terms because HTML comments are hidden content.

Verification goals:
- The new test passes.
- No behavior or requirement text changed in `csharp_plugin/README.md`.

## Integration Test Assessment

No new integration test is planned for this ticket.

Reasoning:
- The ticket does not affect application behavior, APIs, plugin loading, or web services.
- The repo has no compose stack for this area.
- There is no need for test data, fixtures, seeded databases, downloaded samples, or runtime port allocation because the change is limited to a hidden Markdown comment.

## File Change Plan

Planned files to add or update, in order:
1. `tests/test_csharp_plugin_readme.py` - add failing regression test first.
2. `csharp_plugin/README.md` - add the minimal hidden Markdown comment.

## Dependencies Between Steps

1. Inspection must happen before choosing the exact comment placement.
2. The pytest regression test must be added before editing the README.
3. The README edit depends on the failing test being in place.
4. Final verification depends on both the test file and README edit being complete.
