# Task Plan: Smoke 09 isolated lifecycle marker

## Goal

Add a tiny, isolated documentation artifact at `docs/terarchitect-smoke/ticket-09.md` that satisfies the ticket's exact content requirements without changing application or plugin behavior.

## Constraints and Scope

- Keep the change isolated to a new docs file and, if needed for TDD enforcement, a dedicated test file.
- Do not modify runtime code, packaging, plugin behavior, or existing smoke markers.
- Favor the smallest possible test surface so this remains independently mergeable.
- No frontend, browser, service, database, or Docker work is required for this ticket.

## Files Expected To Be Touched

- `docs/terarchitect-smoke/ticket-09.md`
- `tests/test_terarchitect_smoke_ticket_09.py`

## TDD Execution Order

### 1. Confirm baseline and choose the narrowest test location

- Re-read the ticket requirements and verify that `docs/terarchitect-smoke/` does not already exist.
- Review existing lightweight smoke-marker tests in `tests/test_bvf_spec_doc.py`, `tests/test_analyzer_package.py`, and `tests/test_csharp_plugin_readme.py`.
- Decision point:
  - Prefer a new dedicated test file instead of modifying unrelated tests.
  - Reason: the ticket explicitly asks for an isolated artifact, and adding assertions to an unrelated test file creates unnecessary coupling.

Dependency:
- This step informs the test shape in Step 2.

### 2. Write the failing test first

- Create `tests/test_terarchitect_smoke_ticket_09.py`.
- Add a focused test that initially fails because `docs/terarchitect-smoke/ticket-09.md` does not exist yet.
- The test should validate:
  - the file exists
  - the first heading is exactly `# Terarchitect Smoke Ticket 09`
  - one line is exactly `Ticket: 09`
  - one line is exactly `Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file.`
  - the file contains a short independence note mentioning that it is intentionally independent of the other nine smoke tickets

Suggested assertion style:

```python
contents = smoke_path.read_text(encoding="utf-8")
assert "# Terarchitect Smoke Ticket 09" in contents
```

- Run only this test first to confirm red:
  - `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_terarchitect_smoke_ticket_09.py`

Dependency:
- Must complete before creating the doc file so the workflow stays TDD-first.

### 3. Implement the minimum documentation change

- Create the new directory path if needed:
  - `docs/terarchitect-smoke/`
- Create `docs/terarchitect-smoke/ticket-09.md` with only the required content plus a brief independence note.
- Keep the file minimal; avoid adding unrelated explanation, links, metadata blocks, or extra structure.

Expected content structure:

```md
# Terarchitect Smoke Ticket 09
Ticket: 09
Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file.
```

- Add a short sentence below that explicitly states the file is intentionally independent of the other nine smoke tickets.

Dependency:
- This step is driven by the failures from Step 2.

### 4. Re-run the focused test and make the smallest fix if needed

- Re-run:
  - `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_terarchitect_smoke_ticket_09.py`
- If the test fails:
  - adjust only the doc file or the dedicated test for correctness
  - do not broaden scope into existing smoke tests unless a repo-wide convention requires it

Dependency:
- Verifies the green phase before any wider regression pass.

### 5. Run a small regression set around documentation tests

- Run the new smoke test plus the existing lightweight documentation tests to ensure no accidental regressions:
  - `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_terarchitect_smoke_ticket_09.py tests/test_bvf_spec_doc.py tests/test_analyzer_package.py tests/test_csharp_plugin_readme.py tests/test_readme_docs.py`

Why this set:
- It covers the repo’s existing smoke-marker/documentation convention without invoking heavy media, Jellyfin, or C# integration workflows.

Dependency:
- Should only happen after the dedicated test passes.

### 6. Refactor only if the test or file is noisier than necessary

- Keep refactoring minimal.
- Acceptable refactors:
  - tighten the dedicated test to exact-line matching if the initial assertions are too loose
  - simplify wording in the independence note while preserving the ticket’s intent
- Avoid refactors that:
  - move the marker into another file
  - consolidate this ticket with earlier smoke markers
  - introduce helper utilities for a single one-off docs test

Dependency:
- Only after green on the targeted and regression test runs.

## Unit and Integration Test Plan

### Unit-style / lightweight tests to add

- Add `tests/test_terarchitect_smoke_ticket_09.py`.
- Purpose: treat the documentation file as a tiny contract and verify exact required strings.

### Existing tests to run

- `tests/test_bvf_spec_doc.py`
- `tests/test_analyzer_package.py`
- `tests/test_csharp_plugin_readme.py`
- `tests/test_readme_docs.py`

### Integration tests

- No new integration test is planned.
- Rationale:
  - the ticket is docs-only
  - there is no behavior change to validate through service orchestration
  - Docker Compose, dynamic ports, seeded data, and runtime service startup are not applicable here

## Non-Applicable Items

- No Docker Compose usage is expected because the repo does not define a compose workflow for this ticket and no service needs to be started.
- No localhost port allocation is needed because no HTTP service or integration harness is involved.
- No UI or E2E browser automation is needed because the ticket does not touch frontend code.
- No sample media or generated fixtures are needed because the artifact is a standalone markdown file.

## Dependencies Between Steps

1. Baseline review must happen before deciding the dedicated test shape.
2. The dedicated test must be written and observed failing before creating `docs/terarchitect-smoke/ticket-09.md`.
3. The doc file is the minimum implementation needed to satisfy the failing test.
4. The focused test must pass before running the broader lightweight regression set.
5. Any refactor must preserve the exact ticket-mandated strings and isolated-file scope.

## Expected Outcome

- A new standalone file at `docs/terarchitect-smoke/ticket-09.md`.
- A dedicated lightweight pytest file that protects the required content.
- No runtime behavior changes and no expansion into unrelated parts of the repo.
