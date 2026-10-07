---
description: Hand an implementation sub-task to an OpenCode worker on a lower-cost plan behind glass-pipe's red-before-green gate
argument-hint: <what to implement>
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(gp run *), Bash(gp retry *), Bash(gp wait *), Bash(gp apply *), Bash(gp show *)
---

Delegate this to glass-pipe instead of writing the implementation yourself: $ARGUMENTS

1. **Spec as a failing test.** Write or pick ONE small test that fails today and passes when the task is done. You may edit test files; the worker may not. Don't run it yourself: the gate runs it twice before any worker starts.
2. **One card**, implementation files only in `scope` (no test files, no dirs wider than needed):
   `{"to":"opencode","goal":"make <test id> pass","accept":"<single test-runner command for that test>","scope":["<impl file>"]}`
   Optional `"notes":["…"]` (≤5 short lines) for guidance the test can't express: conventions, files to mirror, approaches to avoid.
   `accept` must be one allowlisted runner command (pytest, python -m pytest|unittest, npm|bun|pnpm|yarn test, cargo test, go test, tsc, eslint, ruff, mypy); no `&&`, pipes, or redirects.
3. **One Bash call with `run_in_background: true`**, exactly this shape:
   ```
   gp run <<'EOF'
   <the card JSON on one line>
   EOF
   ```
   Then keep working on something else or stop; you are notified when it exits.
4. **On the result line:**
   - `ok` → run `gp apply <id>` now. `applied landed✓` means done; tell the user in one line.
   - `vacuous` → your test already passes; fix the test, not the code. `flaky` / `env` / `env-missing` → the test or environment needs fixing; say which.
   - `fail` → if `gp show <id> --why` shows a misunderstanding you can correct in one sentence, `gp retry <id> --hint "<that sentence>"` (background, like `gp run`); otherwise tell the user.
   - `tamper`, `scope`, `no-result`, `dirty`, `landing-red` → one sentence to the user; read `gp show <id> --why` only if you need the reason to decide.
   - `needs human stamp` → ask the user; never stamp or override it yourself.
5. Do not edit the card's scope files yourself after dispatch, and do not repeat the receipt text back.
