# Under the hood

This page is for people who want to know exactly what `gp` does with their repo, their processes, and their money. Every claim here is backed by a test; module names are in `gp/`.

- [1. The big picture](#1-the-big-picture)
- [2. Processes](#2-processes)
- [3. A job, step by step](#3-a-job-step-by-step)
- [4. Classification order](#4-classification-order)
- [5. The sandboxes](#5-the-sandboxes)
- [6. Applying, landing, undoing](#6-applying-landing-undoing)
- [7. What lives where](#7-what-lives-where)
- [8. The brief (what the worker model sees)](#8-the-brief)
- [9. The scorecard](#9-the-scorecard)
- [10. Design rules we don't break](#10-design-rules-we-dont-break)

---

## 1. The big picture

```
 ┌──────────────── your Claude Code session (scarce: Max plan window) ────────────────┐
 │                                                                                     │
 │  you ──▶ Claude: plans, writes the FAILING TEST, writes a CARD (~150 tokens)        │
 │            │                                                                        │
 │            │ one Bash call, run_in_background                                       │
 │            ▼                                                                        │
 │      gp run <<'EOF' {card} EOF ─────────────┐        ...Claude keeps working...     │
 │                                             │                                       │
 │      ◀── one line per job (~25 tokens) ─────┤◀── harness wakes Claude on exit       │
 │            │                                │                                       │
 │            ▼                                │                                       │
 │      gp apply <id>  (only on `ok`)          │                                       │
 └─────────────────────────────────────────────┼───────────────────────────────────────┘
                                               │  zero Claude tokens from here on
 ┌──────────── glass-pipe (local, no daemon, no network of its own) ───────────────────┐
 │  snapshot ─▶ baseline ×2 ─▶ brief ─▶ worker ─▶ freeze ─▶ gate ─▶ receipt on disk    │
 └──────────────────────────────────────────────┬──────────────────────────────────────┘
                                                │  lower-cost tokens (OpenCode Go, Codex)
                                     ┌──────────▼───────────┐
                                     │  worker agent  │
                                     │  in its own worktree │
                                     └──────────────────────┘
```

The bet: **Claude's tokens are scarce, lower-cost-plan tokens are abundant, CPU is free.** Every mechanism spends CPU or lower-cost tokens to save Claude tokens or Claude wakes.

## 2. Processes

```
 Claude's Bash tool
   └─ gp run                      waiter: validates cards, spawns jobs, waits ≤ deadline, prints lines
       ├─ gp _job <repo> <id>     one DETACHED process per card (own session); survives the waiter
       │   ├─ sandbox-exec … python3 -m unittest …   accept runs (baseline ×2, gate, landing)
       │   └─ opencode run --server http://127.0.0.1:<port> …   the worker client (cwd + $PWD = worktree)
       └─ gp _job …               (another card)

 opencode serve  (one PRIVATE server per job, started by that job, stopped when it ends)
   └─ runs INSIDE sandbox-exec: tools (shell/edit/write/read/grep/glob) inherit the sandbox
      env: allowlist only (PATH, HOME, locale) + gp's own XDG_CONFIG_HOME, minimal config, random password
```

- **Detached jobs** (`runner.py`): if the waiter is killed, or hits its deadline, jobs keep running and `gp wait <id>` re-attaches. Receipts are written to disk *before* a job is marked terminal, so nothing finished is ever lost. A job that dies anyway gets a `crash` receipt and a ledger entry.
- **Deadline** (`deadline_min`, default 50): chosen to keep the wake inside Claude's 1-hour prompt-cache TTL (measured in the Phase 0 spike: this session's cache writes were all 1-hour). Jobs still running at the deadline print `→ gp wait <id>`.
- **Why one server per job**: a shared server would let one job's worker tamper with another job's tree or with the server's own state (security audit). A fresh server costs about 1–3 s.
- **Why `$PWD`**: `opencode run` resolves its directory from `$PWD`, not the process cwd. gp sets both (found by a live test; regression-tested with the fake worker).

## 3. A job, step by step

```
 card ──▶ parse (cards.py) ──▶ reject early? ── yes ──▶ "gp! rejected, no jobs started"
           │                    (bad JSON, no scope, overlap, test file in scope,
           │                     accept not allowlisted, unknown worker)
           ▼
   env-missing check ─ ignored file under an accept path not in env_files ─▶ env-missing (no baseline)
           │
           ▼
   SNAPSHOT (trees.py)  git stash create (or HEAD) + untracked files (capped 1 MB / 20 MB)
           │            → private index → commit-tree → refs/glass/<id>     your tree untouched
           ▼
   tree B = worktree at base ─▶ accept #1 ─▶ reset --hard + clean -ffdx ─▶ accept #2 ─▶ delete B
           │
           ├─ either run can't start ───────────▶ env           (no worker spent)
           ├─ runs disagree ────────────────────▶ flaky         (no worker spent)
           ├─ both green, normal card ──────────▶ vacuous       (no worker spent)
           ├─ both red, refactor card ──────────▶ refactor-red  (no worker spent)
           ▼
   tree A = worktree at base
   ┌─▶ BRIEF (brief.py): failing assertion inlined, test path, scope, rules, notes, facts, retry tail
   │        ▼
   │   WORKER (workers/*.py) ── quota/auth/unavailable ─▶ fallback to the other worker ONCE
   │        │                ── stalled / timeout ──────▶ killed (process group)
   │        │                ── no <<<GP-RESULT block ──▶ retry (shared budget)
   │        ▼
   │   FREEZE: gp (not the worker) runs git add/commit in A → patch.diff
   │        ▼
   │   ACCEPT on A (sandboxed) → anything changed after the freeze? → tamper (accept wrote tree)
   │        ▼
   │   TAMPER / SCOPE (tamper.py) → tamper | scope
   │        ▼
   └── red & retries left → reset A to base, fresh run with the last 60 lines of failure
            ▼
   green → tests-edited | behavior-unchanged | ok      → receipt.txt, ledger "done", one stdout line
```

**Fresh retries, not resumed sessions**: a retry starts from a clean tree with the failure tail in the brief. Session resume works (verified in the Phase 0 spike) but has to earn its place in measurement first.

## 4. Classification order

Decided **before any worker exists** (tree B):

| | Condition | Status |
|---|---|---|
| B1 | either baseline run cannot start (exit 126/127, missing third-party module, nothing collected) | `env` |
| B2 | one red, one green | `flaky` |
| B3 | both green, normal card | `vacuous` |
| B4 | both red, refactor card | `refactor-red` |

Decided **after the worker**, first match wins:

| | Condition | Status |
|---|---|---|
| 1 | accept cannot start on tree A | `env` |
| 2a | skip/xfail/only marker added · runner config touched · protected test file touched without override · accept wrote the tree after the freeze | `tamper` |
| 2b | `edit_tests` set and an overridden test file touched (even alongside scope files) | `tests-edited` |
| 3 | path outside `scope` | `scope` |
| 4 | normal card: red → green | `ok` |
| 5 | refactor card: green → green, no tests touched | `behavior-unchanged` |
| 6/7 | still red | `fail` (retry while budget lasts) |

"Cannot start" vs "red" matters for test-first work: `ImportError: cannot import name 'stream_rows'` is **red** (the card asks for it), while `ModuleNotFoundError: No module named 'requests'` is **env** (`accept.cannot_run`).

**Protected paths** (`protect.py`): explicit accept paths (`tests/test_x.py::node`, `tests.test_x`) plus their `conftest.py` chain and sibling `fixtures/`, `__snapshots__/`, `testdata/`; a bare runner (`pytest -q`, `bun test`) protects every test-looking file; runner config (`pytest.ini`, `package.json`, `jest.config.*`, …) is always protected.

## 5. The sandboxes

All of them are **write allowlists**: deny every write, then allow a short list of resolved roots. A path alias (symlink, `..`) can only reach a root that was already writable, so aliases can't fail open. All of them also **read-deny** common secret locations (`~/.ssh`, `~/.aws`, `~/.config/gh`, `~/.gnupg`, `~/.kube`, `~/.docker`, `~/.netrc`, `~/.git-credentials`, `~/.codex`, `~/.config/opencode`…).

```
 ACCEPT (tree A/B)                LANDING CHECK (your tree)           WORKER SERVER (one per job)
 ┌───────────────────────────┐    ┌──────────────────────────────┐    ┌──────────────────────────────────┐
 │ write: the tree under test│    │ write: ONLY inert debris:    │    │ write: this job's worktree       │
 │        private TMPDIR     │    │  .pytest_cache .mypy_cache   │    │        OpenCode session data/db  │
 │ network: loopback only    │    │  coverage .coverage* …       │    │        private tmp               │
 │                           │    │ NOT .git .glass .venv        │    │ NOT: your repo · other jobs ·    │
 │                           │    │ node_modules *.pyc  (no      │    │  gp's server config · OpenCode's │
 │                           │    │ bytecode written at all)     │    │  plugin/npm cache                │
 │                           │    │ network: loopback only       │    │ network: open (model API)        │
 └───────────────────────────┘    └──────────────────────────────┘    │ env: allowlist, no API keys      │
                                                                     └──────────────────────────────────┘
```

- **gp never trusts a worktree's `.git`.** The worktree's `.git` file is worker-writable. If gp let git discover the repo through it, a worker could point git at a config that runs commands (`core.fsmonitor`, hooks). So every gp git call on a worktree passes the admin dir recorded at creation (`--git-dir`/`--work-tree`), with hooks and fsmonitor disabled (`trees.wt_git`). Regression-tested by a worker that rewrites `.git`.
- macOS uses `sandbox-exec` (seatbelt). Linux uses `bwrap` if present; its landing check can't use regex rules, so it falls back to "repo writable except `.git`". With neither, accept runs and workers are refused (`unsandboxed`) unless `allow_unsandboxed = true`.
- `gp ask` starts a **read-only** server for one question: `edit`, `write`, `shell` denied, and nothing writable but OpenCode's own session data.
- Not covered, by design: workers can *read* other user-readable files (including OpenCode's provider login, which the server needs) and *reach the network*. See [SECURITY.md](../SECURITY.md).

## 6. Applying, landing, undoing

```
 gp apply <id>
   │
   ├─ eligible?  ok → yes · tests-edited/behavior-unchanged → human stamp · tamper → human override
   │
   ├─ every patch file's bytes == snapshot bytes?  ── no ──▶ dirty: "src/x.py changed since snapshot
   │                                                          (job ab12 applied | unrelated edit)"
   ├─ git apply
   ├─ LANDING CHECK: accept on YOUR tree (sandboxed, your repo writable for this one run)
   │      └─ red ──▶ restore each patch file from the snapshot blobs (byte-exact; test debris like
   │                 .pytest_cache is ignored) ──▶ landing-red, landed=false
   └─ green ──▶ push {job, post-apply hashes} on .glass/applied.json (LIFO stack)
                publish the job's facts to the pipe · delete the worktree · drop refs/glass/<id>

 gp undo <id>   only the top of the stack · refuses if any file changed after apply · git apply -R
```

`semantic conflicts unchecked` is on every receipt because a landing check can only see what the accept command exercises: two patches can each be green and still be wrong together.

## 7. What lives where

```
 your-repo/
 └─ .glass/                      (git-ignored by `gp init`)
    ├─ config.toml               workers, deadline, env_files, accept_allow, auto_allow_gp…
    ├─ jobs/<id>/
    │   ├─ card.json  state.json receipt.txt  patch.diff
    │   ├─ baseline.txt  accept-N.txt  landing.txt
    │   ├─ brief-N.txt           exactly what the worker was told
    │   └─ worker-N.jsonl        the worker's event stream (never shown to Claude)
    ├─ ledger.jsonl              submit / done / apply / undo / dirty / ask events (append-only, flock)
    ├─ pipe.jsonl + THREAD.md    facts; only from applied jobs, you, or Claude constraints
    ├─ applied.json              the LIFO apply stack
    ├─ notes.jsonl               stamps, overrides, constraints
    └─ edits.jsonl               Claude's direct edits (plugin hook) → `rescued`

 ~/.cache/glass-pipe/<repo-hash>/      (mode 0700)
    └─ <job-id>/                 removed on apply / drop
        ├─ a/  b/                temporary worktrees (b is deleted right after the baseline)
        ├─ .a.gitdir  .b.gitdir  admin-dir pointers recorded at creation (not worker-writable)
        └─ server/               that job's OpenCode config, sandbox profile, log (not worker-writable)
```

## 8. The brief

The brief is the entire contract with the worker model, so it is pinned by golden tests (`tests/golden/*.txt`). In order:

```
 GOAL ............................ from the card
 ACCEPTANCE COMMAND .............. "this alone decides success"
 failing output .................. inlined from the baseline (≤30 lines around the assertion)
 TEST TO READ FIRST .............. "open and read the test before changing anything"
 RULES ........................... read anything · write ONLY <scope> · never edit tests · no skip
                                   markers · no git writes · run the acceptance command yourself
 NOTES FROM THE PLANNER .......... card notes / retry hints (never above the rules)
 BINDING DECISIONS ............... facts from applied jobs, you, and Claude constraints (≤12)
 PREVIOUS ATTEMPT FAILED ......... on retry: clean tree + last ≤60 lines of failure
 <<<GP-RESULT {json} ............. required ending; the last marker followed by valid JSON wins
```

## 9. The scorecard

```
 success  =  landed  ∧  gate-accepted  ∧  stayed  ∧  not-rescued  ∧  card-stable      ÷ cards submitted
              │          │                 │          │                │
              │          │                 │          │                └ goal/accept unchanged, test file
              │          │                 │          │                  hash at submit == hash now
              │          │                 │          └ no Edit/Write by Claude on scope files after the receipt
              │          │                 └ no undo; no later commit with lines not in the patch (7 days)
              │          └ status ok (stamped exceptions excluded)
              └ clean apply + green landing check
```

Rates: **offload** = stayed lines ÷ (applied + rescued + fix-up lines); **vacuous** = (vacuous + tests-edited + stamped behavior-unchanged) ÷ cards that finished baseline; **net time** = submit → stayed apply. `gp stats` never prints a savings number without the five counters.

## 10. Design rules we don't break

1. **Receipts, not transcripts.** stdout is one line per job; everything else is on disk.
2. **The worker never runs git writes**; gp freezes, diffs, applies.
3. **No worker is spent on a test that can't discriminate** (vacuous / flaky / env are decided first).
4. **Nothing lands that went red on your tree**; the landing check reverts byte-exact.
5. **Humans stamp exceptions.** Claude may suggest. A TTY check and a hook stop accidental self-stamping; that is a guardrail, not a boundary, and stamped work never counts as delegated.
6. **Zero runtime dependencies**, Python stdlib only; hooks exit 0 no matter what.
7. **Every claim gets a test**: the sandbox test was written first and caught a real fail-open.
