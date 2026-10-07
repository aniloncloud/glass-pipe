"""gp init: set up glass-pipe in a repo. Writes only .glass/config.toml and a .gitignore line;
prints everything that touches the user's environment (PATH, Claude settings) for them to approve.
"""
from __future__ import annotations

import json
from pathlib import Path

from .repo import Repo
from .util import read_json, write_json

PKG_ROOT = Path(__file__).resolve().parent.parent

CONFIG = """# glass-pipe config (see gp/repo.py DEFAULT_CONFIG for every key)
# deadline_min = 50          # one wake inside the prompt-cache TTL (Phase 0 spike)
# env_files = [".env.test"]  # ignored files the accept command needs, copied into worktrees
# auto_allow_gp = false      # PreToolUse hook auto-allows plain `gp` commands (incl. `gp run <<'EOF'`)

[workers.opencode]
type = "opencode"
model = "opencode-go/deepseek-v4.1-flash"

[fallback]
"""

ALLOW = ["Bash(gp run *)", "Bash(gp retry *)", "Bash(gp wait *)", "Bash(gp show *)", "Bash(gp diff *)", "Bash(gp apply *)",
         "Bash(gp status)", "Bash(gp stats *)", "Bash(gp ask *)"]

CLAUDE_MD = """## glass-pipe
Implementation sub-tasks with a testable outcome go to cheap workers via `/gp:opencode` (see the glass-pipe skill).
Write the failing test yourself; the worker writes the implementation. Never stamp or override gp jobs yourself.
"""


def init(repo: Repo, write_settings: bool = False) -> str:
    out = []
    cfg = repo.glass / "config.toml"
    if cfg.exists():
        out.append(f"kept existing {cfg.relative_to(repo.root)}")
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(CONFIG)
        out.append(f"wrote {cfg.relative_to(repo.root)}")
    gi = repo.root / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if ".glass/" not in text.splitlines():
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".glass/\n")
        out.append("added .glass/ to .gitignore")
    if write_settings:
        sp = repo.root / ".claude" / "settings.local.json"
        st = read_json(sp, {}) or {}
        allow = st.setdefault("permissions", {}).setdefault("allow", [])
        added = [r for r in ALLOW if r not in allow]
        allow += added
        write_json(sp, st)
        out.append(f"added {len(added)} allow rule(s) to .claude/settings.local.json")
    out += [
        "",
        "Next steps (not done for you):",
        "  1. Claude Code plugin:  /plugin marketplace add aniloncloud/glass-pipe  then  /plugin install gp@glass-pipe",
        f"     (from this checkout: claude --plugin-dir {PKG_ROOT})",
        f"  2. gp in your terminal: ln -s {PKG_ROOT / 'bin' / 'gp'} ~/.local/bin/gp",
        "  3. allow rules" + (" (written above)" if write_settings else " (or rerun with --write-settings):"),
    ]
    if not write_settings:
        out.append("     " + json.dumps({"permissions": {"allow": ALLOW}}))
    out += ["  4. optional CLAUDE.md snippet:", *("     " + l for l in CLAUDE_MD.strip().splitlines())]
    return "\n".join(out)
