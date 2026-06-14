## Ticket

- ID: `b1473b48-ea3d-4c2d-8f4f-5afc9736b3b7`
- Title: `Smoke 08: add BVF player comment`
- Goal: add exactly one small clarifying comment near the top of `tools/bvf_player.py` explaining that the CLI reads BVF metadata for operator inspection, with no runtime behavior change.

## Constraints and assumptions

- This is a comment-only change. No Python logic, CLI flags, or output should change.
- No frontend, web service, database, or Docker Compose workflow is involved in this ticket.
- No fixed or dynamic localhost ports are needed because there is no app server or integration environment for this change.

## Files expected to change

- `tools/bvf_player.py`

## Order of work

1. Inspect the existing top-of-file structure in `tools/bvf_player.py`.
2. Add exactly one brief comment near the top of `tools/bvf_player.py`.
3. Review the diff to confirm the change is limited to the requested clarification and does not alter runtime behavior.

## Execution plan

### Step 1: Baseline inspection

- Read the top section of `tools/bvf_player.py` to identify the safest insertion point.
- Preferred location: near the module docstring or the first section banner so the comment is clearly “near the top” and describes CLI intent rather than implementation detail.
- Dependency: none.

### Step 2: Implement the minimum change

- Edit `tools/bvf_player.py` and add exactly one tiny clarifying comment near the top of the file.
- The comment should explain purpose, not mechanics. It should complement the existing module docstring rather than restate playback behavior.
- Keep the edit isolated to one comment line unless formatting requires touching an adjacent blank line.
- Do not alter imports, logic, CLI help text, docstrings, or output behavior.
- Dependency: Step 1.

### Step 3: Review and verify scope

- Inspect the resulting diff to confirm the source change is limited to a single clarifying comment near the top of `tools/bvf_player.py`.
- Confirm there are no functional edits, no additional comments elsewhere, and no changes to tests.
- Dependency: Step 2.

## Dependencies between steps

- The source edit depends on identifying a safe near-top insertion point first.
- Scope verification depends on the source edit being complete.

## Expected deliverable

- One new clarifying comment near the top of `tools/bvf_player.py`.
- No runtime behavior changes.
