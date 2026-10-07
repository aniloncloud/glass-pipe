# Changelog

## 0.1.0 (unreleased)

- **Engine** (Phase 1):
  - Cards in, one-line receipts out.
  - Baseline-first red-before-green gate with `vacuous`, `flaky` and `env` stops.
  - Sandboxed accept, tamper and scope checks, a frozen diff, fresh retries, and quota fallback (once).
  - Clean-path apply with a landing check and byte-exact auto-revert. LIFO undo.
  - Five-counter scorecard.
- **OpenCode worker** (Phase 2):
  - gp-owned, password-protected, sandboxed `opencode serve` with no MCP or plugins.
  - `$PWD`-correct worker directory.
  - Tolerant `<<<GP-RESULT` parsing.
  - Usage and cost recorded per job.
- **Claude Code plugin** (Phase 3):
  - `/gp:opencode`, `/gp:ask` and `/gp:apply`, plus the `glass-pipe` skill.
  - Hooks for the session news line, the edit log, the self-stamp guard and optional auto-allow.
  - `gp init`.
- **Steering:** card `notes`, `gp retry --hint`, and Claude-written binding constraints.
- **`gp ask`:** read-only and capped, on a separate read-only server.
