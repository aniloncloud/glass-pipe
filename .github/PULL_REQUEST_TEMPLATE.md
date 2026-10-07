## What & why

## Tests
- [ ] `uv run --with pytest pytest` passes
- [ ] New behavior has a unit or integration test (fake worker); live tests only for real-worker behavior
- [ ] Golden briefs updated deliberately (`GP_UPDATE_GOLDEN=1`) if the worker prompt changed

## Safety
- [ ] No new path that lets a worker write outside its worktree, edit tests, or skip the gate
