# Contributing to glass-pipe

Thanks for helping. glass-pipe is small on purpose. Please keep it that way.

## Ground rules

- **Zero runtime dependencies.** The engine uses the Python 3.11+ stdlib only. pytest is a dev tool.
- **Tests first.**
  - Every behavior change comes with a unit or integration test that uses the scripted fake worker (`gp/workers/fake.py`), which needs no API keys and costs nothing.
  - Live tests (`tests/live`, `GP_LIVE=1`) are only for behavior that needs a real model.
- **Safety invariants are not negotiable** (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#10-design-rules-we-dont-break)). A change must never let a worker:
  - write outside its worktree,
  - edit tests without a human stamp,
  - skip the gate.
- **The brief is a contract.** If you change what the worker model is told, regenerate the golden files deliberately with `GP_UPDATE_GOLDEN=1` and explain why in the PR.
- **Receipts stay one line.** Anything new goes into `.glass/jobs/<id>/`, not stdout.

## Dev loop

```bash
uv run --with pytest pytest                        # unit + integration (~2-3 min, macOS)
uv run --with pytest pytest tests/unit             # fast
GP_LIVE=1 uv run --with pytest pytest tests/live   # real OpenCode Go; a few cents
claude plugin validate --strict . && claude plugin validate --strict ./.claude-plugin/plugin.json
```

## Adding a worker adapter

1. Subclass `Worker` in `gp/workers/` and implement two methods:
   - `argv(brief_path, cwd)` returns `(argv, env)`.
   - `classify(rc, events)` returns a `WorkerResult`. Map the worker's errors to `quota`, `auth`, `unavailable` or `crash`, read the `<<<GP-RESULT` block with `parse_result_block`, and report `usage`.
2. Make sure tools can't write outside the worktree. Prove it with a test like `test_server_profile_denies_nested_writes_to_main_repo`.
3. Register the type in `make_worker`, add unit tests for `classify` built from recorded real events, and add one live test.

## Commits & PRs

- Keep PRs small, and say in the PR body *what* changed and *why*.
- Never commit API keys, `.glass/`, or recorded events that contain secrets. Scan them first.
