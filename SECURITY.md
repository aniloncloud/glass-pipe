# Security policy

glass-pipe runs third-party coding agents on your machine and applies their output to your repo. It is a **personal tool**. It gives a worker roughly the trust you give OpenCode when you run it yourself, and adds the guardrails below. Design details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-the-sandboxes).

Before going public, the project had an independent security audit. Every confirmed finding was fixed and has a regression test in `tests/integration/test_security.py` or `tests/unit/test_sandbox.py`.

## What is protected

| Asset | Protection |
|---|---|
| **Your repository** | Workers edit disposable worktrees. Each job's OpenCode server runs in a **write-allowlist sandbox** (`sandbox-exec` on macOS, `bwrap` on Linux): only that job's worktree, OpenCode's session data, and a private tmp are writable. |
| **gp itself** | gp never lets git trust anything inside a worktree. Every git call uses the admin directory recorded when gp created the tree, with hooks and fsmonitor disabled. A worker that rewrites `.git` cannot make gp run code. |
| **Your agent setup** | Each job gets its **own** OpenCode server: empty config, no MCP servers, no plugins, a random password. It cannot write OpenCode's plugin and npm cache (no planting code for your next run), gp's config, or other jobs. `question`, `webfetch`, `websearch`, `execute`, `subagent` and `skill` are denied. |
| **Your secrets** | Common credential locations are **read-denied** in every sandbox: `~/.ssh`, `~/.aws`, `~/.azure`, `~/.config/gcloud`, `~/.config/gh`, `~/.docker`, `~/.kube`, `~/.gnupg`, `~/.codex`, `~/.config/opencode`, `~/.netrc`, `~/.git-credentials`, and OpenCode's MCP auth file. Workers get an **allowlisted environment** (PATH, HOME, locale), so API keys and tokens in your shell never reach them. |
| **Test runs** | Accept commands run sandboxed. They can write only the tree under test and a private TMPDIR, the network is limited to loopback, and only allowlisted runners are accepted (no shell metacharacters). |
| **The landing check** | After apply, the worker's code runs on **your** tree. Inside the repo it may write only inert test debris (`.pytest_cache`, coverage and similar). It cannot write your code, `.git`, `.glass`, `.venv`, `node_modules` or Python bytecode. If it goes red, the apply reverts byte-for-byte. |
| **The gate** | Protected test, conftest, fixture and runner-config paths. Skip and `only` markers, symlinks and mode changes are all flagged as tamper. The diff is frozen before accept runs, and red-before-green is required. |
| **Approvals** | Stamps and overrides (`gp note --harness human`) are refused without an interactive terminal, and a hook denies Claude's attempts. These stop **accidental** self-approval. They are not a boundary: see the known limits. Stamped work never counts as delegated in the scorecard. |
| **Local state** | `.glass/` is owner-only (0700). Worktrees and server homes live under `~/.cache/glass-pipe/` (0700). |

## Known limits (by design, not bugs)

- **Workers can read** other files your user can read, including OpenCode's own provider login, because the server needs it to call the model.
- **The network is open** for the worker server, because the model API needs it. A determined worker could send out what it can read.
- **Workers can write OpenCode's session database** (`~/.local/share/opencode`), where OpenCode stores sessions.
- **Applied code is code you will run.** The gate checks behaviour your tests cover, not intent. Review diffs that matter (`gp diff <id>`).
- **Linux has no regex write rules in `bwrap`,** so there the landing check allows writes to the whole repo except `.git`, and accept runs can't be read-denied as finely as on macOS.
- **Stamps are a guardrail, not a security boundary.** Any process running as you can drive a pseudo-terminal or obfuscate a command, and that includes Claude. A deliberately misbehaving agent could therefore record a stamp. Stamped jobs still have to pass the gate and the landing check, count toward the vacuous rate in `gp stats`, and never count as delegated. Treat `tests-edited`, `behavior-unchanged` and `tamper` receipts as needing your own look.
- With neither `sandbox-exec` nor `bwrap`, gp refuses to run accept commands and workers, unless you set `allow_unsandboxed = true`.

## Reporting a vulnerability

Please **do not open a public issue** for a sandbox escape, a gate bypass, or a credential exposure. Use GitHub's private vulnerability reporting ("Report a vulnerability" on the Security tab), and include a minimal card or repro. We aim to acknowledge reports within 72 hours.
