"""gp ask: a read-only question answered by the cheap model, capped so it stays cheap for Claude.

The answer lands in Claude's context, so it is a standing cost (PLAN): capped at ~1600 chars
(~400 tokens) unless --long, logged to the ledger so Phase 4 can count ask tokens separately.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from .pipe import ledger
from .repo import Repo
from .util import GpError, new_id, write_json
from .workers.opencode import OpenCodeServer

CAP, LONG_CAP = 1600, 6000

TEMPLATE = """Answer a question about the code in the current directory. You can only read files; do not try to change anything.
QUESTION: {question}

Reply with no preamble, in this shape:
<answer in at most {words} words>
REFS: <up to 8 file:line references that support the answer>
READ_NEXT: <up to 5 files someone should read before changing this>
"""


def _worker_cfg(repo: Repo, to: str | None) -> tuple[str, dict]:
    workers = repo.config.get("workers", {})
    if to:
        if workers.get(to, {}).get("type") != "opencode":
            raise GpError(f"gp ask: {to!r} is not an opencode worker (gp ask uses the read-only OpenCode server)")
        return to, workers[to]
    for name, w in workers.items():
        if w.get("type") == "opencode":
            return name, w
    raise GpError("gp ask: no opencode worker configured")


def ask(repo: Repo, question: str, to: str | None = None, long: bool = False) -> str:
    if not question.strip():
        raise GpError("gp ask: empty question")
    name, wcfg = _worker_cfg(repo, to)
    ask_id = new_id()
    home = repo.cache / f"ask-{ask_id}"
    srv = OpenCodeServer(repo, wcfg, home=home, mode="ask")  # read-only: no edit/write/shell
    try:
        srv.start()
        prompt = TEMPLATE.format(question=question.strip(), words=900 if long else 250)
        p = subprocess.run(["opencode", "run", "--server", f"http://127.0.0.1:{srv.port}", "--format", "json",
                            "-m", srv.model, "--title", "gp ask", "--", prompt],
                           cwd=repo.root, env=srv.client_env(repo.root), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=repo.config.get("ask_timeout_s", 300))
    finally:
        srv.stop()
        shutil.rmtree(home, ignore_errors=True)
    model = srv.model
    texts, usage, err = [], {"cost": 0.0, "input": 0, "output": 0}, None
    for line in p.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = e.get("part") or {}
        if e.get("type") == "text":
            texts.append(part.get("text", ""))
        elif e.get("type") == "step_finish":
            usage["cost"] += float(part.get("cost") or 0)
            usage["input"] += int((part.get("tokens") or {}).get("input") or 0)
            usage["output"] += int((part.get("tokens") or {}).get("output") or 0)
        elif e.get("type") == "error":
            err = e.get("error")
    answer = (texts[-1] if texts else "").strip()
    write_json(repo.glass / "asks" / f"{ask_id}.json",
               {"question": question, "answer": answer, "usage": usage, "error": err, "model": model})
    cap = LONG_CAP if long else CAP
    shown = answer if len(answer) <= cap else answer[:cap].rstrip() + f"\n[… truncated at {cap} chars; gp ask --long]"
    ledger(repo, "ask", ask=ask_id, chars=len(shown), usage=usage, error=bool(err))
    if err or not answer:
        return f"gp ask {ask_id} failed: {str(err or 'no answer')[:200]}"
    return f"{shown}\n(gp ask {ask_id}, {name}, ${usage['cost']:.4f})"
