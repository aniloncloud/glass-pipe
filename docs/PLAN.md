# glass-pipe: send Claude's subtasks to OpenCode and Codex

## Context

Claude Code (Max plan) becomes the **planner, dispatcher, and exception handler**. Coding runs on cheaper plans: OpenCode Go and Codex (`gpt-6-luna` is set in `~/.codex/config.toml`). Claude hands a subtask to `opencode` or `codex` the way it would hand it to a subagent.

**Who it's for.** This is a **personal rate-limit tool**: someone who hits the Max window and has idle Codex and OpenCode quota. Human stamps from your own terminal are acceptable. It is **not** a team dispatcher, and the README says so.

**The split:**
- **Claude writes the failing test and the card.**
- **The cheap model writes the implementation and may not edit tests.**
- **Unattended apply covers red-to-green `ok` cards only.** `behavior-unchanged` (refactors, moves, renames) and `tests-edited` are exceptions you stamp yourself. They are not the success metric.
- Skill floor: **don't delegate trivial edits.** For a one-file change of a few lines, Claude just edits. Writing a card plus waiting for a wake loses to that.

**The product is "one wake inside the prompt-cache TTL", not "small logs".** A 25-token receipt means nothing if the completion turn misses the cache. The normal shape is **one card, one wake**. Batches of disjoint cards are the minority case.

**Who reviews what:**
- **The gate accepts.** It checks red-before-green, tamper, and scope.
- **The human stamps** refactors and tamper overrides.
- **Claude reads a one-line status** and applies the clean path, or opens `gp show` when it won't. Claude is not the reviewer, and the README must not say it is.

**The failure mode to watch is not a bad patch**, because the gate exists to catch those. It's one of these:
- Claude doesn't write cards, or
- Claude writes them and then opens every receipt, so the pipe becomes a slower bridge with better logs.

The original design chat (not published) describes two pieces:
- **The glass pipe:** a shared board (`.glass/pipe.jsonl` → `THREAD.md`). It does not dispatch anything.
- **The existing bridges.** The installed `codex-plugin-cc` costs Claude tokens in three places:
  - it starts a **Sonnet forwarding subagent** per delegation,
  - it pastes **Codex's whole output** back into the conversation,
  - Claude then usually reviews the diff as well.

**The idea: Claude gets a receipt, not a transcript.**
- Claude writes a short card.
- A local dispatcher does the rest outside Claude's context: prompt building, isolation, the cheap agent's run, the accept gate, and retries on cheap tokens.
- Claude wakes once per batch and reads a receipt that holds a diffstat only.

**What this does not save.** Any tool return makes Claude re-read the session, and that includes the official bridge's subagent. The savings come from:
- no forwarding subagent,
- no transcript,
- no re-review of passing diffs,
- retries that run on cheap tokens,
- one wake per batch.

Phase 0 measures completion-turn input tokens and the **cache-read versus cache-write split**. A long batch can miss the prompt cache and pay for the session again.

**Your choices:**
- **Routing:** Claude picks OpenCode or Codex per job, like a subagent.
- **Scope:** everything. The interactive board adapters are gated on the token measurement (Phase 4).
- **Apply policy:** no preference given. The default is **no auto-apply**. Claude runs `gp apply` on the wake, and only for the fast clean path.

## Architecture

> **Source of truth:** the **Build order** section defines what gets built and when. Architecture, Key mechanics, and Innovations describe the **end state**, and Phase 1 implements only the Build order scope. Items marked *(gated)* are not in v1.

```
Claude ── one Bash(run_in_background) ──> gp run <<'EOF' {card}\n{card} EOF   → ONE wake per batch
   │        each card = detached job process (own process group, durable state in .glass/jobs/<id>/)
   │        parse-time rejects (job never starts): missing scope, scope overlap in batch,
   │          accept-test/protected path in scope (unless edit_tests), env-missing at snapshot
   │          ├─ snapshot: hash scope files + copied untracked → pooled worktree A (worker)
   │          ├─ baseline FIRST: accept ×2 on pristine tree B → flaky / vacuous stop here (no worker spent)
   │          ├─ brief compiler → worker (codex exec | opencode run), watchdog, logs → job dir only
   │          ├─ gate: scope → tamper → accept (argv-parsed allowlist, sandboxed) → fresh retry w/ failing tail (≤2)
   │          └─ receipt written to job dir the moment the job finishes (crash-safe)
   │        gp run = waiter: exits when all cards finish or --deadline hits; stdout = one line per job
   └─ on wake:  gp apply <id>   clean → git apply (seconds) | moved → returns `dirty` instantly
                gp regate <id>  (gated) separate background call → fresh snapshot, re-run accept, then apply
```

## How we guarantee the sub-task actually goes through glass-pipe

> This is the **end-state spec**. v1 ships `advisory` only. `routed` and `strict` are gated layers, built only if the measurements call for them (see Build order).

A skill alone is only a suggestion. The mode is a **repo policy** stored in `.glass/mode`, which the plugin hooks read. Any `claude` session that loads the plugin in the repo enforces it. `gp claude` is just a convenience launcher that loads the plugin.

| Mode | Mechanism | Guarantee |
|---|---|---|
| `advisory` | Slash commands (`/gp:codex`, `/gp:opencode`, `/gp:ask`) plus the skill | Each slash command guarantees that its task is delegated. Proactive delegation is best-effort. |
| `routed` | A PreToolUse hook on the subagent tool. The matcher is `Agent\|Task`, and Phase 0 confirms the real name. It denies `Explore` and `general-purpose` with a reason that shows the `gp ask` or `gp run` shape. | Exploration and write-capable subagents can't run on Claude tokens. Read-only judgment subagents, such as Sonnet, are still allowed (see below). |
| `strict` | Everything in `routed`, plus the four hard controls below | Code that exceeds the session budget **cannot** end up in the tree: the turn cannot end until it is reverted or gated. |

**Claude subagents (for example Opus calling Sonnet) are an explicit allow, not a loophole.** Your global CLAUDE.md defaults subagents to Sonnet, so `routed` and `strict` keep that path open for *judgment*. Labour goes through gp.

The subagent hook decides each Agent/Task call by `subagent_type`, using `.glass/config.toml`. It applies these rules in order:

| Order | Rule | Default |
|---|---|---|
| 1 | `deny_agents` | `["Explore", "general-purpose"]`. These are labour-shaped, and the deny reason points to `gp ask` or `gp run`. |
| 2 | `allow_agents` | Includes the shipped **`gp-judge`** agent: `model: sonnet`, tools `Read, Grep, Glob` only, for review, spec, test design, and diagnosing a `gp show --why` packet. |
| 3 | Any other agent, for example from `.claude/agents/` or a plugin | Allowed **only if its frontmatter `tools` is present and is a subset of exactly `Read, Grep, Glob`**. |

Rule 3 denies, with a "use gp run" reason:
- an agent with a **missing `tools` field**, because that means the default set, which includes Edit and Bash,
- any agent with other tools listed, including **any `mcp__*` tool**, because the parent's MCP deny doesn't bind a subagent's own tool list.

- **Plan mode:** the built-in `Plan` agent is **denied** in `routed` and `strict`, so plan-mode exploration also goes to `gp ask`.
- The result: Opus can still ask Sonnet to *think*, but any agent able to *write* is routed to OpenCode or Codex.
- **The judge's reply is capped.** Allowed subagents run on Claude tokens, and their reply lands in Opus's turn. So the `gp-judge` prompt caps its reply at about 300 tokens: a verdict and at most 5 findings with `file:line`. `gp stats` counts judge calls separately. That keeps a review from turning into a second forwarding subagent.
- Phase 0 confirms whether the hook payload carries `subagent_type`. If it doesn't, the hook falls back to the allowlist and denies everything else.

The four hard controls in `strict`:
1. **Bash is deny-by-default.**
   - It is parsed with the gate's shlex parser, and shell metacharacters are rejected.
   - Allowed: `gp` subcommands, except `gp note --harness human`, which only a human may use.
   - Allowed: an explicit read-only list (`rg`, `ls`, `cat`, `head`, `wc`, `git status/diff/log/show`).
   - Allowed: test runners from the accept allowlist.
   - **Read-only escape:** `python3`, `jq`, and `node` are allowed **only inside the accept `sandbox-exec` profile**, with repo writes denied, tmp allowed, and **stdout capped at about 2 KB** so the escape can't be used to dump files.
   - **Everything else is denied**, including `perl`, `cp`, `tee`, and any interpreter outside the sandbox. Each denial explains how to do the job through `gp`.
2. **Direct-edit budget is per session, with atomic reservation.**
   - The hook covers Edit, Write, MultiEdit, and NotebookEdit.
   - Each edit reserves its line delta from a session budget, for example 40 lines. The reservation happens under a flock, so parallel Edit calls cannot both spend the same remaining budget.
   - Over budget, the edit is denied and the reason shows the card shape.
   - Docs (`*.md`, `docs/**`) and `.glass/cards/**` are exempt.
   - Every other `.glass/` path, such as `ledger`, `pipe`, `jobs`, and `mode`, **cannot be edited**. Those files change only through `gp`.
3. **Stop blocks the turn rather than just logging, and the block survives a crash.**
   - **SessionStart (strict mode only)** records hashes of the tree: `git ls-files -s` plus the dirty set. It does not store blobs up front.
   - **Pre-images are stored lazily** in `.glass/preimages/`, mode 0700, and never in git's object store.
     - Files that were already dirty at session start are copied there.
     - Budgeted edits copy their file just before the write.
     - Clean tracked files are restored from `HEAD`.
   - At Stop, the hook works out the unaudited diff: changes that came from neither a `gp apply` patch nor a budgeted edit.
   - If that diff is non-empty, the hook returns a **block**, and Claude has to run one of two commands before the turn can end:
     - `gp unaudited --revert` restores those files exactly.
     - `gp adopt --accept "…"` runs the **same scope, tamper, and accept gate** on the diff in a worktree. It **never starts a model**, so a weakened test is still caught as tamper. If it passes, the diff is recorded as `gated-direct` and does not count as delegated.
   - **Crash latch.** Stop never runs after Ctrl-C, a crash, or a closed terminal. To cover that:
     - SessionStart detects any leftover unaudited set.
     - It then denies Edit and `gp run` until that set is reverted or adopted.
   - Every deny reason says **"do not retry with another tool"** to avoid a deny → retry → deny loop. A test measures that a second identical call does not happen.
4. **Other write paths.**
   - `cat`, `head`, and similar commands on `.glass/jobs/**` are denied, so Claude can't pull receipts back into context. It has to use `gp show`.
   - Phase 0 lists the MCP tools that can write files, and strict mode denies them with a PreToolUse matcher on `mcp__.*` plus a read-only allowlist.
   - `gp init` **commits the plugin enablement** to `.claude/settings.json`. A session without the plugin would otherwise fall back to `advisory` without anyone noticing.
5. **Fail-closed sandbox.** An accept command that would run `unsandboxed`, for example on Linux without `bwrap`, is **refused** in `strict`. In `advisory` it requires an explicit `--allow-unsandboxed`.

On plan mode under `routed`: if the payload carries `permission_mode`, plan mode is still routed, because exploration is exactly what `gp ask` is meant to take over from Claude. Phase 0 checks that `gp ask` can run under plan mode's permissions. If it can't, the docs will say plan mode cannot explore under `routed` or `strict`. The Agent tool is not reopened.

Two more pieces back this up:
- **Measurement:** `gp stats` reports the scorecard and the **offload rate** (see "Proving the offload worked"), along with these strict-mode counts:
  - **Delegated** = lines in applied `gp` patches. The configured `format` command runs inside the worktree before gating, so formatter output is part of the patch.
  - **Direct** = budgeted edits plus `gated-direct` lines.
  - Lines that were already dirty at SessionStart don't count either way.
- **Durability:** once a task is dispatched, it is not lost.
  - Each job is a detached process with its state on disk. `gp status` shows live jobs, and `gp wait <ids>` re-attaches.
  - If `gp run` is killed or hits its deadline, finished receipts are already in the job directory, and unfinished jobs keep running.
  - The SessionStart news line surfaces finished jobs in the next session.

## Repo layout

Python stdlib, matching the research `glass.py`. Startup is about 30 ms. pytest is a dev dependency only.

```
glass-pipe/
  gp/
    cli.py        init | mode | claude | run | wait | ask | show | apply | regate | undo | drop | diff | log
                  retry --hint | adopt | unaudited | status | stats | warm | note | thread | snapshot | hook
    cards.py      JSONL cards {to, goal, accept, scope:[files], kind?, timeout?, apply?} from heredoc or --cards file
    budget.py     session edit budget (flock-reserved); tree hashes + lazy preimages in .glass/preimages/ (strict only); crash latch
    plan.py       card DAG (`after`), parse-time validation, pinned staging tree, batch gate, all-or-nothing apply
    race.py, primer.py, lessons.py      (Phase 5 only)
    jobs.py       detached job runner (setsid), state machine, crash-safe receipt flush
    snapshot.py   hash scope files + copied untracked (not whole repo); untracked caps; env_files copy
    pool.py       warm worktrees under ~/.cache/glass-pipe/<repohash>/ (dir 0700), keyed by lockfile hash
    deps.py       CoW clone (macOS `cp -c -R`, Linux `cp --reflink=auto`) else `setup` cmd; fail → setup-failed
    brief.py      card → worker prompt; binding facts from human/Claude + applied jobs only
    gate.py       scope, tamper, accept allowlist (shlex argv, no shell), sandbox wrapper, baseline compare, taxonomy
    apply.py      clean-path apply, regate, post-hash record, LIFO undo stack
    receipt.py    receipts: status, diffstat, paths, ≤3 notes; no file bodies; regex redaction as defense-in-depth
    result.py     Codex --output-schema; OpenCode = LAST fenced block after `<<<GP-RESULT` marker; facts only from it;
                  missing marker / schema miss → status `no-result` (classified failure, nothing written to the pipe)
    pipe.py       pipe.jsonl (flock) + THREAD.md; facts carry {source, job}
    hooks.py      claude/codex/opencode hook stdin → pipe events; PreToolUse enforcement; Stop audit
    ledger.py     .glass/ledger.jsonl; scorecard counters (landed/stayed/rescued/card-stable) + rates; `gp stats`
    workers/{base,codex,opencode,fake}.py   templates/{worker,ask}.md   schemas/worker_result.schema.json
  adapters/claude-plugin/   commands/{codex,opencode,ask,apply}.md, skills/glass-pipe/SKILL.md, hooks/hooks.json,
                            agents/gp-judge.md (sonnet, Read/Grep/Glob)
  adapters/codex-plugin/, adapters/opencode-plugin/glass-pipe.js        (Phase 5)
  bin/gp   tests/{unit,integration,live}/   pyproject.toml   README.md
```

## Key mechanics

> End state. Phase 1 implements only the Build order scope; items marked *(gated)* are not in v1.

**Card**
- Cards are JSONL on stdin, passed through a **quoted heredoc**, so apostrophes and `---` in a goal are safe:
  ```
  gp run <<'EOF'
  {"to":"codex","goal":"make tests/test_export.py::test_stream pass","accept":"pytest -q tests/test_export.py::test_stream","scope":["src/export/csv.py"]}
  {"to":"opencode","goal":"make the trailing-comma tests in parser.test.ts pass","accept":"bun test parser","scope":["src/parser.ts"]}
  EOF
  ```
- If no allow rule matches the heredoc shape, the fallback is `gp run --cards .glass/cards/<id>.jsonl`, which strict mode permits.
- Several lines in one call is the fan-out, and it costs one wake. **This is the minority case.** Real tasks share types and tests, so the common path is one card, one wake.
- **Cards in a batch must have disjoint scopes.** Overlapping `scope` lists are rejected when the batch is parsed, before any job starts. Disjoint scopes are what make independent batching safe without a staging tree or regate.
  - Overlap is checked on **normalized paths**: resolved relative to the repo root, with `.` and `..` collapsed and symlinks resolved.
  - **A directory scope overlaps every path under it.** So a card with `src/export/` and a card with `src/export/csv.py` are rejected, the same way as two cards that name the same file.
- The skill says to batch only jobs of similar size, because the slowest card holds the batch's receipts.
- **The deadline trades against the cache.**
  - A `--deadline` shorter than the cache TTL keeps the completion turn on a warm cache. Jobs still running when it expires are detached, so Claude has to call `gp wait` later, and that **second wake often misses the cache**.
  - **The deadline is a measured constant, not the word "TTL".**
    - Claude Code has 5-minute and 1-hour cache lifetimes, depending on auth and settings.
    - Phase 0 records the **observed** cache TTL for this Max session: does `cache_read` survive a gap of N minutes?
    - `GP_DEADLINE` = the observed TTL minus a margin. It applies to every run, single cards included.
  - **Stdout at the deadline:** any job still running gets its own line, `gp#a3f running codex 4m → gp wait a3f`. Claude then has an id to pass on and doesn't open the job directory.
  - Phase 0 logs the second wake as well.

**Isolation**
- Worktrees are pooled outside the repo, with mode 0700.
- Reset runs `git reset --hard <snap>` then `git clean -ffdx` with exclusions for the dependency directories.
- `env_files` (for example `.env`) are wiped on reset and on `gp drop`.
- The pool is keyed by a hash of **every lockfile present**: `package-lock.json`, `bun.lock(b)`, `pnpm-lock.yaml`, `yarn.lock`, `uv.lock`, `poetry.lock`, `requirements*.txt`, `Cargo.lock`, and `go.sum`. Any change triggers a dependency re-clone.
- The pool is sized in **pairs**: **2 by default in v1** (4 trees), raised only after Phase 4. Each pair is a worker tree plus a baseline tree.
  - Trees are held as pid leases.
  - **The baseline tree is released as soon as accept is classified.**
  - The waiter never holds leases, so a batch larger than the pool queues instead of deadlocking.
  - `gp run` and `gp status` reclaim the leases of dead pids.
- **Ignored files that accept depends on.** If an ignored file sits under a path named in the accept argv and isn't in `env_files`, the card fails **at snapshot time** with `env-missing <path>`, and no worker starts. A live test uses such a fixture on purpose.
- Untracked files are capped at 1 MB each and 20 MB in total. The cap is reported.
- Snapshot hashes cover only the scope files and the copied untracked files.

**Freeze before accept**
- The worker's diff is **frozen**: the patch is saved and the scope files hashed, **before** accept runs.
- After accept, the hashes are checked again. If accept changed any scope or protected file, the job is invalidated as `tamper (accept wrote tree)`.
- The classified patch is always the frozen one. An accept command can never write the green.

**Baseline**
- **The sequence is ordered, not concurrent**, so a green or flaky test never spends a Codex or OpenCode job:
  1. Take the snapshot.
  2. Run accept **twice** on pristine tree B, with `git reset --hard` and `git clean -ffdx` **between the runs** so the second is also pristine.
  3. If both runs are green and the card is not `kind: refactor`, the status is `vacuous`, and the worker never starts. A refactor card proceeds, with `behavior-unchanged` as its only possible pass.
  4. If both runs are red, the brief is compiled with the **failing assertion inlined** from the baseline output, and then the worker starts on tree A.
  5. The worker's diff is frozen, then accept runs on tree A. A scope or protected-file hash change after the freeze means `tamper (accept wrote tree)`. The patch that gets applied is always the frozen one.
- This gives up overlap between the baseline and the worker's start, which was never the expensive part. In exchange, golden prompts always have a real assertion to snapshot.
- **`kind: refactor` with a red baseline is rejected** as `refactor-red`, and the worker never starts. A refactor's only pass is `behavior-unchanged`. To make a red test pass, drop the `kind`.

**Classification**

*Before any worker exists*, decided on tree B:

| Order | Condition | Status |
|---|---|---|
| B1 | Either baseline run **cannot run** (exit 127, collection error, missing import) | `env`, with no retries |
| B2 | One run red and one green | `flaky`: no worker, no retry, never apply-eligible |
| B3 | Both green, not `kind: refactor` | `vacuous` (early): no worker |
| B4 | Both red, `kind: refactor` | `refactor-red`: no worker |
| — | Both red (normal card), or both green (refactor) | The worker starts |

*Only if a worker ran*, in order of precedence:

| Order | Condition | Status |
|---|---|---|
| 1 | Accept **cannot run** on tree A | `env` |
| 2a | The patch adds skip or xfail patterns, touches runner config, touches any protected file the card did **not** override, or **accept wrote the tree after the freeze** | `tamper`. This always wins. |
| 2b | The card set `edit_tests`, and the patch touches **any** overridden protected file, **even if scope files changed too** | `tests-edited`, which can be applied only with a human stamp and never counts as delegated |
| 3 | The patch is out of scope | `scope` |
| 4 | A normal card, red at baseline, green after | `ok` |
| 5 | A refactor card, green at baseline, green after, **and the diff touches no tests** | `behavior-unchanged`, which can be applied **only with a human stamp** |
| 6 | A normal card, still red after | `fail`, which goes to a fresh retry |
| 7 | A refactor card, red after | `fail` |

For `behavior-unchanged`:
- The human stamp is `gp note --job <id> --kind stamp --harness human`.
- `gp-judge` (routed mode only) may **recommend** a stamp, but it may not give one.
- These lines are never counted as delegated labour.

  `env` covers only "could not run". It never covers "same output as baseline".

**Accept safety**
- The command is parsed with `shlex` into argv and run without a shell. Metacharacters (`&&`, `;`, `|`, `$()`, redirects) are rejected.
- `argv[0]` (and the subcommand) must be on the allowlist: pytest, `npm|bun|pnpm test`, `cargo test`, `go test`, tsc, eslint, ruff, mypy, plus entries added in `.glass/config.toml`.
- On macOS it runs under `sandbox-exec`, with **loopback allowed**, external network denied, and writes limited to the worktree and tmp.
- On Linux it uses `bwrap` when available. Otherwise the status is `unsandboxed`. Strict mode refuses it, and advisory mode needs `--allow-unsandboxed`.

**Tamper.** The classification precedence (rows 2a and 2b) decides which status applies. Without `edit_tests`, it is tamper when the diff touches any of these:
- files named by accept,
- `conftest.py` or `tests/**/fixtures`,
- pytest, jest, or vitest config,
- `package.json` scripts,

or **added lines** in the diff match syntax-level patterns, not substrings. Examples: `@pytest.mark.skip`/`xfail`, `pytest.skip(`, `it.skip(`/`describe.only(`, `#[ignore]`, `t.Skip(`.

Tamper is never apply-eligible.
- The receipt shows the offending line.
- A false positive can only be overridden by a **human**: `gp note --job <id> --kind override --harness human`, run from your own terminal. Strict mode denies that command to Claude.

**Taxonomy**

| Status | Meaning | Handling |
|---|---|---|
| `quota` / `auth` / `unavailable` | The worker's plan or service can't take the job | Falls back to the other worker **once**. If the other worker returns one of the same three, the job stops, with no hop back. These three are the only fallback triggers. |
| `fail` | Accept failed | A **fresh run** with the 60-line failing tail in the brief, up to 2 times. Resuming the same session is used instead only if Phase 0 proves it reliable. It never falls back. |
| `scope` / `tamper` | Out-of-scope or tampering diff | Kept in the worktree, never applied. |
| `timeout` | Ran too long | Killed, worktree kept. |
| `stalled` | No worker event within N seconds, for example OpenCode waiting on a permission prompt | Killed. |
| `env` / `setup-failed` | The accept command or setup couldn't run | No retries. |
| `no-result` | The OpenCode `<<<GP-RESULT` marker is missing, or Codex's output fails the schema | A fresh retry within the same 2-retry budget. Nothing is written to the pipe. |
| `flaky` | The two baseline runs disagree | No retry, never apply-eligible. Fix the test. |
| `refactor-red` | A `kind: refactor` card whose baseline is red | Rejected before any worker starts. Drop the `kind` to make it a normal card. |
| `env-missing` | **Set at snapshot time, before any worker starts.** An ignored file under an accept path wasn't copied. | No retries, no fallback. Kept separate from `env` so that fixture-copy bugs show up in the failure mix. |

**Apply**
- `gp apply` only does the **fast clean path**: the touched files must match their snapshot hashes, then it runs `git apply` and records the post-apply hashes.
- Otherwise it returns `dirty` immediately, **with the reason**: `scope file X changed since snapshot (job Y applied)` or `(unrelated edit)`. That lets Phase 4 tell a design limit from a bug. In v1 that's the end of the path: the patch stays available through `gp show`.
- *(gated)* `gp regate <id>` is a separate background call that rebases onto a fresh snapshot, re-runs accept, and applies. Re-gating doesn't detect semantic conflicts, so **the receipt itself** says `ok (regate: tests only, semantic conflicts unchecked)`.
- `gp undo` is **LIFO**. It refuses if the files changed after apply, and it names the later job to undo first.
- **The landing revert is a separate path from `gp undo`.**
  - When the post-apply accept on the main tree is red, gp reverses the **frozen patch**, then re-checks the **scope-file hashes**.
  - It ignores accept debris such as `__pycache__`, `.pytest_cache`, and coverage files.
  - It never refuses on that debris.
  - It records `landed=false`.
- Auto-apply (`--apply`) is opt-in. It requires an explicit file-list scope, no tamper, a real accept command, and an unchanged tree. `--review` never qualifies.

**`gp ask`**
- It runs in a pooled snapshot with the worker's read-only sandbox.
- It returns `answer` (about 400 tokens, or more with `--long`), `refs[file:line]`, and `read_next[]`.
- It is **the exception, not the default planning path**: its answer lands in the session, so it is a standing cost. Phase 4 counts ask tokens separately, and the skill's default is test-first cards.

**Receipt hygiene**
- Logs go to the job directory, never stdout.
- **stdout is one line per job**, about 25 tokens. Example: `gp#a3f ok codex 2try 3f +63-18 accept✓ → .glass/jobs/a3f/receipt.txt`.
  - The full receipt (diffstat, paths, notes) stays on disk.
  - `gp show <id>` reads it on demand.
  - That way old receipts don't pile up in every later turn's context.

## Innovations: what makes this more than a cheaper `/codex:rescue`

The economics are inverted. **Claude's tokens are scarce, cheap-plan tokens are abundant, and CPU time is free.** So every mechanism below spends cheap tokens or CPU to save Claude tokens or Claude wakes.

> End state. Only item 2 (the discriminating gate), item 3 (test-first), and item 4 (symbol hints) are in Phase 1. The DAG is gated on one-card apply being reliable (see Build order).

1. *(gated)* **A plan becomes one wake: card DAGs and a staging tree.** (`plan.py`)
   - Cards can name dependencies (`"id":"b","after":["a"]`), so Claude writes the whole plan in one `gp run`.
   - Independent cards run in parallel, and each green patch lands in a **staging tree**.
   - A dependent card starts from the staging state, after its dependencies have landed.
   - At the end, a **batch gate** runs the union of all accept commands on the combined staging tree once. This is the first mechanism that catches two cards that are green alone but fail together.
   - Claude wakes **once per plan** and runs `gp apply @batch`.
   - **Scheduler rules:**
     - Cycles, and `after` ids that aren't in the batch, fail at **parse time**, before any job starts.
     - A dependency ending in `fail`, `tamper`, `vacuous`, `scope`, or `env` means its children **never start**. They are marked `blocked`.
     - Any failed node fails the batch.
     - **Apply is all or nothing.** If the batch gate fails, nothing lands, including cards that were green on their own. Their patches stay available for `gp show`, and a new batch can be submitted.
     - **A merge conflict between cards in the staging tree fails the batch.** No worker or model merges anything. The patches stay available through `gp show`, and the retry is a new batch. The main tree is untouched until `gp apply @batch`, which follows the same clean-path, `dirty`, and regate rules.
     - The staging tree is **pinned for the life of the plan, outside the reusable pool**, so a pool reset can never move a dependent card's base. It is released on apply or drop.

2. **Discriminating gate: green must have been red before.** (`gate.py`, free)
   - The baseline already runs accept on the pristine snapshot.
   - If the baseline was green and the post-worker run is also green, the status is **`vacuous`** and the job is not apply-eligible.
   - **Refactors:**
     - A `kind: refactor` card that is green before and after, and whose diff touches no tests, is classified `behavior-unchanged`.
     - It can be applied only with a **human stamp**. The judge may recommend one but cannot give it.
     - It is never counted as delegated, which removes the incentive to label ordinary work as a refactor.

3. **Test-first is the default card, not just a pattern.**
   - The skill's **default** flow:
     1. Claude writes or names a **failing test** in the session. There is no budget in advisory v1, and the skill must not mention one.
     2. The card is `{"to":"codex","goal":"make tests/test_export.py::test_stream pass","accept":"pytest -q tests/test_export.py::test_stream","scope":["src/export/csv.py"]}`.
        - `scope` is **required**, and it lists implementation files only.
        - An omitted scope is a parse error. It never means "the whole repo".
   - Prose-only cards are the exception and must justify themselves, for example docs or config.
   - The test is both the brief and the gate: tamper protects it, and the discriminating gate shows it was red.

4. **Symbol hints in `gp show`.**
   - The changed functions come from git hunk headers, for example `csv.py: ~write_rows() +stream_rows()`.
   - They are labeled **hints**: a hunk header names the enclosing or previous function, so it can be wrong.

**Deferred to Phase 5, after the Phase 4 token number exists.** None of these is part of that comparison.

5. **Speculative racing: best-of-2 on cheap compute.** (`race.py`)
   - `"race":true` sends the same card to **both** OpenCode and Codex in separate tree pairs.
   - The winner is picked deterministically:
     - green and not tamper or vacuous,
     - then the smallest diff,
     - then the fastest.
   - The loser is cancelled.
   - Claude sees one receipt. This buys latency and reliability with abundant tokens. It is opt-in, so the plans don't burn quota on every job.
   - Both sides must run **the same accept command**. Smallest-diff only breaks ties between non-vacuous winners, because with weak tests it would favor stubs.

6. **Hint escalation: Claude as the senior engineer, not the rescuer.** When resume isn't proven, the hint goes into a fresh run's brief instead.
   - When retries run out, the receipt points to a **diagnostic packet** (`gp show <id> --why`). It holds only the failing assertion, the relevant hunk, and the worker's last stated plan, in about 300 tokens.
   - Claude answers with `gp retry <id> --hint "the timeout is in seconds, not ms"`. The hint goes into a **fresh run's brief**, and that is the only path. Same-session resume stays an optimisation that Phase 0 has to earn.
   - Claude spends a sentence instead of taking over the task.

7. **A repo primer and an ask cache.** (`primer.py`)
   - A cheap worker writes a primer: repo map, conventions, and test commands.
   - `gp ask` answers are cached, and cards can cite them (`"context":["ask:7c2"]`).
   - **Known risk:** both go stale when the relevant fact sits in a file they did not hash. So both are marked "may be stale", carry the files they cited, and expire when any cited file changes.

8. **Lessons loop.**
   - Lessons are worker-scoped facts added to later briefs.
   - A lesson enters **only from a human revert or a human override**, never from Claude's own hint, so one bad hint can't poison every later brief.

## Proving the offload worked: score the work, not only the bill

A run can spend fewer Claude tokens and still land a vacuous patch, a stamped exception, or a diff that gets reverted the same night. That is a slower bridge with a ledger. **Token savings mean nothing without this scorecard,** because it is what tells a saved turn apart from a deferred one.

**A card is a successful offload only if all five counters below pass.** Anything else is an attempt.

| Counter | Success | Failure |
|---|---|---|
| `landed` | Clean apply, **and accept is green on the main tree after apply** (the landing check), with no stamp | `dirty`, apply skipped, or accept red after apply |
| `gate-accepted` | `ok` only | `vacuous`, `tamper`, `scope`, `fail`, `env`, `env-missing`; `behavior-unchanged` and `tests-edited` count as accepted *exceptions*, not as labour |
| `stayed` | No `gp undo`, and no fix-up commit on the scope files inside a window fixed before the trial (7 days) | `gp undo`, or a later commit whose diff or message repairs the same goal |
| `not-rescued` | Claude never edits a scope file after the receipt | An Edit or Write on a scope file after `gp show` or after a non-`ok` line. It counts as Claude labour, and the patch is not counted as delegated. |
| `card-stable` | Goal and accept are unchanged since first submit (tightening scope is fine), **and the accept-named test, its `conftest.py`, and its fixtures are unchanged after submit** | The goal changed, an assertion was removed, `edit_tests` was added, or **anyone, Claude included, edited the protected test files after submit** |

**Success rate** = landed ∧ gate-accepted ∧ stayed ∧ not-rescued ∧ card-stable, divided by **cards submitted**. The denominator is always reported.

Three rates matter more than the token delta:
- **Offload rate** = applied lines that stayed ÷ (applied lines + rescued lines + fix-up lines). This replaces the naive delegation ratio, which counted a patch the moment it applied.
- **Vacuous rate** = (`vacuous`, early included, + `tests-edited` + stamped `behavior-unchanged`) ÷ **cards that finished baseline**, early `vacuous` and `flaky` included. `flaky` is counted in the denominator and the failure mix, not the numerator. If it is high, tokens were saved by proving nothing. Racing, and any claim that "the gate reviews", stay gated on this rate.
- **Net time** = wall-clock from the first card to a stayed apply, including round-trips, retries, `gp show`, and the landing check, compared with Claude doing the same task with Edit.

**Implementation:** no dashboard, no quality model, and no second judge.
- `gp apply` runs the **landing check**: accept on the main tree after apply, recorded in the ledger. **If it's red, the patch is reverted automatically** through the same LIFO undo, and `landed=false` is recorded, so a failing patch never stays in the tree. The receipt still says `semantic conflicts unchecked`.
- `rescued` needs the edit log, which arrives with the Phase 3 adapter. Phase 1 records it from a stub log.
- `ledger.py` records `landed`, `stayed` (via `gp stats --window 7d`, which scans `git log` on the scope files), `rescued` (from the edit-log hook), and `card-stable` (from card-JSON revisions **plus a hash of the accept-named test, its `conftest.py`, and its fixtures, stored at submit and compared at apply and at the end of the window**).
- **`gp stats` refuses to print a savings number unless all five counters are printed next to it, with the denominator.**

## Build order: thin core first, then gated layers

> **Definition of done:**
> - **Phase 1** is done when FakeWorker proves the precedence table, the landing-check revert, and the one-line receipt.
> - **Phase 2** is done when one real worker does the same on a deliberately bad green patch.
>
> No module that doesn't serve those belongs in those phases.
>
> - **`strict`, if it is ever built, exempts test files Claude writes before a card from the edit budget.** This matches the split: Claude writes tests, cheap models write implementation. **The exemption ends when the card is submitted.** After that, an edit to the test is a budgeted edit and sets `card-stable=false`.
> - **No offload rate is quoted before Phase 3.** Until then `not-rescued` comes from a stub log.

Everything above is the **end-state spec**. It gets built in layers, and **each layer ships only when the previous layer's measurement shows it's needed.** Enforcement is an arms race with a harness we don't control, so it has to earn its place with data.

**Spec deltas adopted from the independent review:**
- **`behavior-unchanged` status.** A refactor card that is green before and after is apply-eligible only when the diff touches no tests **and you stamp it** with `gp note --harness human`. `gp-judge` can only recommend. It is never counted as delegated labour.
- **Disjoint scopes per batch.** Overlapping card scopes are rejected at parse time, so one card's apply can't make another card `dirty`.
- **By default, worker scope excludes the accept test.**
  - At parse time, a card whose `scope` includes a file that accept runs or names (the test file, and its `conftest.py` and fixtures) is rejected, unless the card sets `"edit_tests": true`.
  - With that override, the receipt is flagged `tests-edited`, and apply needs a human stamp.
  - This is what stops a worker from turning red into green by narrowing the test.
- **Missing ignored files fail early.** If an ignored file sits under a path the accept argv names, and it isn't in `env_files`, the card fails **at snapshot time** with `env-missing <path>`. It does not start a worker.
- **Every apply-eligible receipt says `semantic conflicts unchecked`,** including a single card. The limit comes from the accept command, not from batching, and it is permanent.
- **What counts as "a file accept names"** (`gate.protected_paths`):
  - **Explicit paths in the accept argv:** each one is protected, after `::node` ids and `-k` expressions are stripped. So are the `conftest.py` files in that path's directory and its parents up to the repo root, and fixture directories next to it (`fixtures/`, `__snapshots__/`, `testdata/`).
  - **A bare runner** (`pytest -q`, `bun test`, `npm test`, `cargo test`, `go test ./...`) protects all test files:
    - `tests/**`, `test/**`, and `__tests__/**`,
    - `*_test.*`, `*.test.*`, `*.spec.*`, and `test_*.py`,
    - the runner's config: `pytest.ini`, the `[tool.pytest*]` section of `pyproject.toml`, `jest`/`vitest`/`bunfig` config, and the `package.json` scripts.
  - Either way, a card must name a scope with at least one path outside this protected set.
- **Regate caveat in the receipt.** It reads: `ok (regate: tests only, semantic conflicts unchecked)`.
- **Strict pins the Claude Code version.**
  - `gp mode strict` records the version.
  - If a session starts on a different version, enforcement drops back to `advisory` until a re-check is run, **and the SessionStart line says so** (`glass: strict suspended — Claude Code X≠Y, run gp mode --recheck`).
  - A change in the hook surface counts as breaking, and the drop is never silent.
- **Read-only escape in strict Bash.**
  - `python3`, `jq`, and `node` run only inside the accept `sandbox-exec` profile, with repo writes denied and tmp allowed.
  - **Stdout is capped at about 2 KB.**
  - Claude keeps its planning one-liners and still can't write code.
- **Fresh-run retry is the default.** The failing tail goes into a new brief. Resuming the same worker session is an optimisation, used only if Phase 0 proves it reliable.

### Phase 0: spike log (not a spec)

Record what is actually true in a spike log (kept private; findings are in docs/ARCHITECTURE.md).

**Worker checks:**
- `git init`.
- Codex:
  - does `exec resume` keep the sandbox and cwd (via `-c sandbox_mode=…`)?
  - **can `workspace-write` write inside a `~/.cache` worktree?**
- OpenCode:
  - does the background service run tools in the client's cwd, or is `--standalone` needed? What is the cold start?
  - does `--auto` stall headless on a permission prompt?
  - does `-s` resume?
- The real model ids (`gpt-6-luna`, `opencode-go/*`), the JSON event shapes, and how to get the session id.
- `sandbox-exec` with pytest and `bun test` (loopback allowed).
- **A write test of the escape:**
  - `python3 -c 'open("src/x.py","w")'` cannot create or modify a tracked file. The same must hold for `node` and `jq`.
  - The test runs against the **real worktree and repo paths**, including resolved symlinks, because a profile that only denies a literal path fails open.
  - The stdout cap is enforced.

**Claude-side checks:**
- Does an allow rule match the `gp run <<'EOF'` heredoc without prompting? If not, use `--cards`.
- Hook payload facts:
  - the subagent tool name, and whether `subagent_type` and `permission_mode` are present,
  - whether a deny reason reaches the model,
  - whether a Stop block continues the turn.
- **Claude Code version.**

**Token log**, for `/codex:rescue` and a hand-run prototype of `gp`:
- completion-turn input tokens,
- cache read versus write,
- **tokens from the follow-up `gp show`**, and how often Claude opens the receipt,
- *Not a spike exit:* deny-reason tokens, including the probe sequence. Those denials only exist once `routed` or `strict` exists, so they are recorded only if a hook is stubbed anyway. They are the entry ticket for strict mode.

**Kill switches.** If either one fails, the pool is only a sketch and Phase 1 waits:
- headless Codex **writes inside a `~/.cache` worktree**,
- headless OpenCode **does not stall on a permission prompt**.

**The spike exits on five things:**
- the worker argv,
- sandbox write-denial,
- how the heredoc allow rule behaves,
- a rough completion-turn comparison against `/codex:rescue`, including a `gp show`,
- **the observed cache TTL** for this Max session, which `GP_DEADLINE` is derived from.

**Recorded, but not an exit:**
- **Is the applied diff taken before accept, and can accept change a scope hash?** This is checked by hand on a toy repo.
- **`opencode run` specifically**, not the tmux path, whose Enter failure is already known: cold start, and how to get the session id.
- **Card round-trips:** how many times Claude rewrites scope or accept before a worker starts. This is the planning-turn cost, which nothing else prices.
- **Failure mix on a fixed tiny task, per worker:** `ok`, `vacuous`, `tamper`, `scope`, `fail`, and `env`.
- **Wall-clock time per applied line,** including pool setup, cold start, and retries, compared with Claude just editing.

**Prior art:** a 30-minute review of **how** `claude-opencode-delegate`, `cc-suite`, and the multiharness mod actually start OpenCode and Codex: the exact argv, how they submit the prompt, and how they handle cwd and permissions. The goal is invocation lessons, for example the OpenCode prompt-submit failure in tmux, not feature lists.

### Phase 1: thin core (tests first, FakeWorker)

**Scope:**
- Cards that each go to one worker, with **independent cards batched behind one waiter** (one wake) and no DAG.
- **No pool in Phase 1.** Each card gets one **temporary** worktree pair under `~/.cache/glass-pipe/`, deleted on apply or drop. The baseline tree is deleted once accept is classified. The pair pool, leases, and CoW dependency clones arrive only when a 5-card batch is a measured workflow.
- Baseline accept with the classification precedence, plus scope and tamper.
- Receipt on disk and one stdout line.
- `gp apply` **clean path only**: `dirty` returns `dirty` and there is no regate.
- LIFO undo.
- Pipe facts from applied jobs only.
- **The brief compiler is a first-class artifact,** because it is what the cheap model actually sees.
  - It takes binding facts from applied jobs only.
  - Symbol hints are labelled as hints.
  - The failing tail on retry is capped at 60 lines.
  - Scope and accept are stated as hard rules.
  - **The brief contains:**
    - the test path,
    - the **failing assertion inlined** from the baseline output,
    - an instruction to **open and read the test first**,
    - "read anything; **write only** the scope files; never edit tests".

    Cheap models fail by not opening the test. This content is pinned in the template **before** the golden tests are written, so the snapshots don't lock in a weak brief.
  - It is covered by **golden-prompt tests**, where each card → brief result is snapshot-tested, so prompt regressions show up in review.

**Not in Phase 1:** regate, the DAG, routed or strict mode, Stop blocks, the session budget, subagent denies, and auto-apply.

**FakeWorker integration tests cover:**

| Area | Cases |
|---|---|
| **Sandbox (the first test written)** | The accept sandbox can't create or modify a tracked file through the real worktree path, its resolved symlink, or a `..` path. Any scope-hash change during accept invalidates the job. The stdout cap holds. On Linux without `bwrap`, the job is refused with `unsandboxed` and is never a quiet success. |
| Card parsing | A missing scope is rejected. `protected_paths` is checked for both explicit-path accept (`pytest -q tests/test_export.py::test_stream`) and bare-runner accept (`pytest -q`, `bun test`). A scope that includes a protected path is rejected unless `edit_tests`. A scope with only protected paths is rejected. An uncopied ignored file gives `env-missing` before any worker starts. **Both README/skill example cards parse as legal.** |
| Precedence | With `edit_tests`, a patch touching the overridden test **plus `src/export/csv.py`** gives `tests-edited`, not `ok`. Adding a skip, editing config, or touching a non-overridden test gives `tamper`. Both stamp paths work, and stamped lines are not counted as delegated. |
| Scorecard | The landing check runs accept on the main tree after apply. Red gives an **automatic revert** and `landed=false`, and the **scope files** end up byte-identical to their state before the apply. A rescue (an Edit on a scope file after the receipt) is detected from the edit log. A card revision that changes the goal or adds `edit_tests` gives `card-stable=false`. **Claude editing the accept test after submit also gives `card-stable=false`.** `gp stats` refuses to print savings without all five counters. **The landing revert also works when accept writes `__pycache__`, `.pytest_cache`, or coverage files:** the frozen patch is reversed, and the scope files are byte-identical while that debris is ignored. |
| Brief compiler | Golden prompts. Facts from unapplied jobs are excluded. The tail cap holds. |
| Classification | green, `vacuous` (with the flaky-baseline retry), `behavior-unchanged` (refused without a human stamp, applied with one; a refactor that touches tests is not eligible) |
| Batch parsing | overlapping card scopes rejected before any job starts: the same file, `src/export/` against `src/export/csv.py`, and `./src/x.py` against `src/x.py` |
| Rejection | `tamper` (conftest, added skip, ignoring the word "skip" in a comment), `scope`, `env` (cannot run) |
| Worker failures | `fail` → **fresh** retry with the failing tail → pass, quota → fallback, accept fail without fallback, `stalled` |
| Apply | clean apply, a mid-job edit giving `dirty`, LIFO undo refusal |
| Worktrees (no pool) | Temp pair created and deleted on apply or drop. Baseline tree deleted after classification. **Order is baseline first:** a green baseline means `vacuous` and a disagreeing one means `flaky`, both with **no worker started**, and a red baseline gives a brief with the assertion inlined. Untracked caps hold. **`gp status` shows trees older than 1 day, and `gp drop --stale` removes them.** |
| Fallback and budget | quota → other worker → quota stops, with no hop back. `fail` and `no-result` share **one 2-retry budget** on the worker that actually took the job. |
| Durability and parsing | `gp run` killed with finished receipts surviving. A decoy `GLASS:` line is ignored. **A missing `<<<GP-RESULT` marker or a schema miss gives `no-result`, and nothing reaches `THREAD.md`.** |
| Freeze and flake | **Accept that writes a scope file after the freeze gives `tamper (accept wrote tree)`.** **One red and one green baseline gives `flaky`; one baseline that *can't run* gives `env`, not `flaky`. Tree B is reset between the two runs, so a cache file written by the first run can't flip the second. A refactor with a red baseline gives `refactor-red` with no worker.** **A `dirty` result prints its reason** (job Y applied, or an unrelated edit). |

### Phase 2: real workers, `tests/live/`

- One task per worker.
- A deliberately bad green patch that must be caught or undone.
- A 2-card independent batch.
- **A landing-check assertion:** after every clean apply, accept runs on the main tree, and the test fails if it is red.
- **A semantic-clash fixture:** green in the worktree but red after apply in the main tree. The scorecard records it as `landed=false`, while the receipt still, correctly, says `semantic conflicts unchecked`.

### Phase 3: advisory Claude adapter

- `/gp:codex`, `/gp:opencode`, `/gp:ask`, and `/gp:apply`.
- A SKILL.md of about 300 tokens. Its default flow is **test-first, one card, one wake**. It skips delegation for trivial edits. Batches are only for disjoint files that share no types.
- A SessionStart news line, the edit log, and `gp init` (allow rules and the CLAUDE.md snippet, for your approval).

### Phase 4: measure (decision point)

- Run 5 real tasks **three ways**: `gp` (advisory), `/codex:rescue`, and Claude editing directly, each **inside and past the cache TTL**.
- The 7-day `stayed` window is fixed before the trial starts.
- Write the Phase 4 report, with the **scorecard (all five counters, success rate with its denominator, offload rate, vacuous rate, net time) next to the token table**.
- **The rule: continue only if `gp`'s stayed offloads beat `/codex:rescue` on the scorecard and the token drop survives `gp show`. A token drop with a worse stayed rate is a no.**
- Count:
  - completion-turn tokens **including the follow-up `gp show`**,
  - **`gp ask` tokens, counted separately.** If they dominate, planning stays on Claude's own read tools and `ask` stays the exception,
  - the cache split,
  - the offload rate and vacuous rate (from the scorecard),
  - the human-review time `gp show` creates for you,
  - **card round-trips per task**, which is the planning-turn cost,
  - **the `gp show` follow-up rate.** If it's routine, the transcript has just come back one command later,
  - **the failure mix**, split by `vacuous`, `tamper`, `tests-edited`, `scope`, `fail`, `env`, and `env-missing`,
  - **wall-clock time per applied line** against Claude editing directly.
- **Continue only if the drop survives Claude inspecting the receipt.**
- **If the offload rate is low, fix the skill and the test-first default first.** Don't reach for `routed` to cover what is really a prompt problem.

### Gated layers, each needing a measurement before it is built

| Layer | Build when |
|---|---|
| Regate | `dirty` shows up often in real use. |
| `routed` mode, including the `gp-judge` allowlist | The advisory offload rate is still low **after** the skill has been fixed. `routed` and `strict` may never ship, and that's acceptable. |
| `strict` mode (deny-by-default Bash with sandboxed escape, session budget, Stop block, crash latch, `.glass` immutability, MCP deny, version pin) | `routed` still leaks, **and** the measured deny-reason tokens don't eat the savings. |
| The DAG, staging tree, batch gate, and `gp apply @batch` | One-card apply is reliable on both workers. |
| Phase 5: the interactive board adapters, `--to auto`, the Codex app-server transport, racing, hint escalation, the primer and ask cache, and lessons | The core is proven. Lessons stay human-sourced only. Racing doesn't ship until the real-task `vacuous` rate is visible. The brief compiler **refuses to cite an expired ask**. |

The test lists already written for strict mode and the DAG (above, in the mechanics sections) become each layer's acceptance tests when it is built.

## Verification

- **Phase 1:** `python3 -m pytest tests/unit tests/integration` is deterministic and spends no plan quota.
- **Phase 2:** `GP_LIVE=1 pytest tests/live` uses real workers. The clean-path apply holds, the bad green patch is caught, and LIFO undo restores the tree.
- **Phase 3 end to end (the primary path):** load `claude --plugin-dir adapters/claude-plugin` and ask for a single change.
  1. Claude writes a failing test, then **one card**.
  2. Expect one wake and one one-line receipt.
  3. `gp apply` lands it, and the landing check is green.
  - **Secondary:** a 2-card batch on files that share no types.
  4. `THREAD.md` shows only the applied facts.
- **Phase 4:** the token table, including `gp show` follow-ups, against the `/codex:rescue` baseline decides whether any gated layer gets built.
