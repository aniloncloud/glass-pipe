"""Claude Code hook handlers. Fast, silent unless there is something to say, never fatal.

- SessionStart: one line, only when there is news (jobs awaiting apply, running, needing a stamp).
- PostToolUse (Edit/Write/MultiEdit/NotebookEdit): append to .glass/edits.jsonl (feeds `rescued`).
- PreToolUse (Bash): deny Claude stamping its own exceptions (`gp note … --harness human`);
  optionally auto-allow plain `gp` commands (auto_allow_gp = true in .glass/config.toml), which
  sidesteps the undocumented heredoc allow-rule matching (Phase 0 spike).
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from .receipt import NEEDS_STAMP
from .repo import Repo
from .util import append_jsonl, now, read_json

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
AUTO_SUBS = ("run", "retry", "wait", "show", "diff", "apply", "undo", "status", "stats", "ask", "drop")
_META = set(";&|`$<>()\\")


def _lines(s) -> int:
    return len(str(s or "").splitlines()) or (1 if s else 0)


def edit_lines(tool: str, ti: dict) -> int:
    if tool == "Write":
        return _lines(ti.get("content"))
    if tool == "Edit":
        return max(_lines(ti.get("old_string")), _lines(ti.get("new_string")))
    if tool == "MultiEdit":
        return sum(max(_lines(e.get("old_string")), _lines(e.get("new_string"))) for e in ti.get("edits") or [])
    if tool == "NotebookEdit":
        return _lines(ti.get("new_source"))
    return 0


def news(repo: Repo) -> str | None:
    if not repo.jobs.exists():
        return None
    ready, running, stamp = [], [], []
    for jd in sorted(repo.jobs.iterdir(), key=lambda p: p.stat().st_mtime):
        st = read_json(jd / "state.json", {})
        if st.get("dropped") or st.get("apply") in ("applied", "undone", "landing-red"):
            continue
        status = st.get("status")
        if status == "ok" and st.get("terminal"):
            ready.append(jd.name)
        elif status in ("running", "queued"):
            running.append(jd.name)
        elif status in NEEDS_STAMP:
            stamp.append(jd.name)
    parts = []
    if ready:
        parts.append(f"{len(ready)} ok job(s) await `gp apply` ({' '.join(ready[-5:])})")
    if running:
        parts.append(f"{len(running)} running (`gp wait {' '.join(running[-5:])}`)")
    if stamp:
        parts.append(f"{len(stamp)} need the user's stamp ({' '.join(stamp[-5:])}); do not stamp them yourself")
    return ("glass-pipe: " + "; ".join(parts) + ".") if parts else None


def is_self_stamp(cmd: str) -> bool:
    """Any mention of `gp … note … human` anywhere in the command (any line, any wrapper such as
    `bash -c`, env prefixes, subshells). Deliberately over-broad: a false deny only costs Claude a
    sentence asking the user. The hard gate is in `gp note` itself (needs a TTY)."""
    return bool(re.search(r"(^|[^\w-])gp\b.*\bnote\b", cmd, re.S)) and "human" in cmd


def is_plain_gp(cmd: str) -> bool:
    """`gp <safe-sub> args…` on one line, or `gp run <<'EOF'` … `EOF` with a quoted delimiter.

    The delimiter may appear only on the LAST line: bash ends a heredoc at the first match, so a
    second `EOF` would smuggle commands after it (security audit finding #3)."""
    if any(c in cmd for c in "\r\x00") or any(ord(c) < 32 and c not in "\n\t" for c in cmd):
        return False
    lines = cmd.strip("\n").split("\n")
    first = lines[0]
    heredoc = None
    if "<<" in first:
        head, _, delim = first.partition("<<")
        delim = delim.strip()
        if not (len(delim) >= 3 and delim[0] == delim[-1] and delim[0] in "'\"" and delim[1:-1].isidentifier()):
            return False  # only quoted delimiters: no expansion inside the body
        heredoc = delim[1:-1]
        first = head
        if len(lines) < 2 or lines[-1] != heredoc:
            return False
        if any(l.strip() == heredoc for l in lines[1:-1]):
            return False
    elif len(lines) != 1:
        return False
    if any(c in _META for c in first):
        return False
    try:
        argv = shlex.split(first)
    except ValueError:
        return False
    if len(argv) < 2 or os.path.basename(argv[0]) != "gp" or argv[1] not in AUTO_SUBS:
        return False
    return heredoc is None or argv[1] == "run"


def handle(event: str, payload: dict) -> dict | None:
    cwd = payload.get("cwd") or os.getcwd()
    try:
        repo = Repo.find(cwd)
    except Exception:
        return None
    if not repo.glass.exists():
        return None  # glass-pipe not initialised in this repo: stay silent
    if event == "SessionStart":
        msg = news(repo)
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}} if msg else None
    if event == "PostToolUse":
        tool = payload.get("tool_name", "")
        if tool not in EDIT_TOOLS:
            return None
        ti = payload.get("tool_input") or {}
        path = ti.get("file_path") or ti.get("notebook_path")
        if not path:
            return None
        try:
            rel = repo.norm(path)
        except Exception:
            return None
        if rel.startswith(".glass/"):
            return None
        append_jsonl(repo.glass / "edits.jsonl", {"ts": now(), "path": rel, "lines": edit_lines(tool, ti),
                                                  "tool": tool, "session": payload.get("session_id")})
        return None
    if event == "PreToolUse" and payload.get("tool_name") == "Bash":
        cmd = (payload.get("tool_input") or {}).get("command", "")
        if is_self_stamp(cmd):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": "glass-pipe: stamps and overrides are the user's decision. "
                    "Ask the user to run this `gp note` in their own terminal; do not retry it another way."}}
        if repo.config.get("auto_allow_gp") and is_plain_gp(cmd):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                    "permissionDecisionReason": "glass-pipe command"}}
    return None
