---
name: glass-pipe
description: Use when an implementation sub-task has a testable outcome and touches a few files - hand it to an OpenCode worker on a lower-cost plan with /gp:opencode instead of writing the code yourself. Not for one-line edits or exploratory work.
---

# glass-pipe

You plan and write the failing test; a worker agent writes the implementation; a gate accepts only red→green inside scope. You get one line back, not a transcript.

**Delegate when** the change has a test you can name and spans more than a few lines. **Don't** for trivial edits (each job costs ~10s of worker start-up plus a wake), for design questions, or when the tests can't express the goal.

**Flow:** write or pick one failing test → one card → one background `gp run <<'EOF'` call → on `ok`, `gp apply <id>`. Full steps: the `/gp:opencode` command.

**Card:** `{"to":"opencode","goal":"make <test> pass","accept":"<one runner command>","scope":["<impl files only>"]}`. Several cards in one call only if their files are disjoint and share no types.

**Steering the worker:** card `"notes":[…]` for this task; `gp retry <id> --hint "…"` after a `fail`; `gp note --kind constraint --harness claude --text "…"` for a rule every future brief must follow.

**Rules:**
- Never edit tests via the worker; never put test files in `scope` (unless the user asks for `"edit_tests": true`).
- Never stamp or override a job (`gp note --kind stamp|override`): that is the user's decision.
- Don't edit scope files yourself after dispatch; don't restate receipts; open `gp show` only for non-`ok` results you must act on.
- Every result says `semantic conflicts unchecked`: the gate knows only what the test tests.
- Read-only questions: `/gp:ask` (capped answer) - the exception, not the default.
