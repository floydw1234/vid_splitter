# Execution Plan: Smoke 02 Isolated Lifecycle Marker

## Goal

Add a new docs-only artifact at `docs/terarchitect-smoke/ticket-02.md` with the exact required heading and content, without changing runtime behavior.

## Constraints And Scope

- Keep the change isolated to documentation and tests.
- Do not alter application code paths, packaging logic, or plugin/runtime behavior.
- Follow TDD: add a failing test first, then the minimum implementation, then refactor only if needed.
- No Docker Compose workflow is applicable here because the repository does not contain a compose file.
- No UI/E2E automation is applicable here because the ticket does not touch frontend code and the repo does not include a browser test framework.
- No network, localhost port, or test-service setup is needed because this ticket is file-content validation only.

## Planned Files To Touch

- `tests/test_terarchitect_smoke_docs.py`
- `docs/terarchitect-smoke/ticket-02.md`

Optional only if needed during implementation review:

- `plan/8cac8b82-0e5e-4cd7-ba90-c1c15f5ef0be_task_plan.md`

## TDD Execution Order

### 1. Add a failing repository-level documentation test

Purpose:
- Lock in the required file path and exact content before adding the document.

File:
- `tests/test_terarchitect_smoke_docs.py`

Test shape:
- Add a focused pytest test that resolves the repo root with `Path(__file__).resolve().parents[1]`.
- Assert that `docs/terarchitect-smoke/ticket-02.md` exists.
- Read the file as UTF-8 and assert:
  - the first-level heading `# Terarchitect Smoke Ticket 02` is present
  - the exact line `Ticket: 02` is present
  - the exact line `Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file.` is present
  - a short independence note is present, using a robust assertion such as checking for both `independent` and `other nine smoke tickets`

Why this test belongs here:
- Existing repo conventions already use lightweight content tests for docs markers, for example `tests/test_bvf_spec_doc.py` and `tests/test_csharp_plugin_readme.py`.
- A dedicated test file keeps this ticket isolated instead of coupling the new marker to unrelated README/spec tests.

Expected initial result:
- The new test fails because `docs/terarchitect-smoke/ticket-02.md` does not exist yet.

### 2. Run the new targeted test and confirm failure

Command to plan for:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_terarchitect_smoke_docs.py
```

Purpose:
- Verify the test fails for the intended reason before implementation.

Dependency:
- Step 1 must be completed first.

### 3. Add the minimum documentation artifact to satisfy the failing test

File:
- `docs/terarchitect-smoke/ticket-02.md`

Implementation approach:
- Create the new `docs/terarchitect-smoke/` directory if it does not exist.
- Add only the required minimal content:
  - exact H1
  - exact `Ticket: 02` line
  - exact `Purpose: ...` line
  - one brief sentence explaining that the file is intentionally independent of the other nine smoke tickets

Content style guidance:
- Keep the file plain Markdown.
- Avoid adding unrelated metadata, timestamps, links, HTML comments, or references to runtime systems.
- Preserve the ticket’s “isolated file” intent by not turning this into a broader docs index.

Dependency:
- Step 2 failure should be observed first.

### 4. Re-run the targeted test and confirm it passes

Command to plan for:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_terarchitect_smoke_docs.py
```

Purpose:
- Confirm the implementation satisfies the exact contract introduced in Step 1.

Dependency:
- Step 3 must be completed first.

### 5. Run adjacent documentation tests as a regression check

Commands to plan for:

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest \
  tests/test_readme_docs.py \
  tests/test_bvf_spec_doc.py \
  tests/test_csharp_plugin_readme.py \
  tests/test_analyzer_package.py \
  tests/test_terarchitect_smoke_docs.py
```

Purpose:
- Ensure the new isolated smoke marker does not accidentally disturb existing marker/documentation conventions.

Why this set:
- These are the nearest existing tests that enforce documentation-marker patterns in the repo.

Dependency:
- Step 4 should already pass.

### 6. Refactor only if the test or file structure needs cleanup

Possible refactors:
- Tighten the new test wording if the initial assertion is too brittle or too vague.
- Normalize the independence-note assertion so it validates intent without over-constraining exact prose.

Non-goals:
- Do not refactor unrelated documentation tests.
- Do not move existing smoke markers or consolidate them into a shared helper unless that becomes necessary, which is unlikely for this ticket.

Dependency:
- Only consider after Step 5, and only if there is clear value.

## Integration Test Plan

No service-level integration test is required for this ticket because:

- the change is a static Markdown file
- there is no application behavior change
- the repository does not provide a compose-based service stack for this path
- there is no need for generated ports, `BASE_URL`, seeded databases, or test fixtures beyond the new file itself

If reviewers require a broader validation layer, the closest appropriate form would still be a repository-level pytest that validates file presence and contents, not a networked integration test.

## Dependency Notes

- The new test file depends on existing pytest conventions only; no new libraries should be required.
- The document file depends on the new `docs/terarchitect-smoke/` directory being created.
- Regression checks depend on the targeted test passing first so failures are easier to interpret.

## Acceptance Checklist For Implementation Phase

- `docs/terarchitect-smoke/ticket-02.md` exists.
- The file contains `# Terarchitect Smoke Ticket 02`.
- The file contains `Ticket: 02`.
- The file contains `Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file.`
- The file contains a short note stating it is intentionally independent of the other nine smoke tickets.
- The new targeted pytest passes.
- Nearby documentation-marker tests still pass.
