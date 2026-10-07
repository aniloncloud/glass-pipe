"""Scripted worker for deterministic tests. Emits OpenCode-shaped JSONL events.

Config: [workers.<name>] type = "fake", script = "<path to JSON>".
Script: {"calls": [step, ...]}; call i uses step i (the last step repeats). A step may set:
  write {path: content}, append {path: text}, delete [paths], sleep s, heartbeat s,
  error "quota"|"auth"|"unavailable", text "free text", result {...} (null -> no marker),
  exit code.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .base import QUOTA_LIKE, Worker, WorkerResult, parse_result_block


class FakeWorker(Worker):
    def argv(self, brief_path, cwd):
        script = Path(self.cfg["script"])
        counter = script.with_name(script.name + f".{self.name}.count")
        pkg_root = str(Path(__file__).resolve().parents[2])
        env = {"PYTHONPATH": pkg_root + os.pathsep + os.environ.get("PYTHONPATH", "")}
        return [sys.executable, "-m", "gp.workers.fake_cli", str(script), str(counter), str(brief_path)], env

    def classify(self, rc, events):
        session = next((e.get("sessionID") for e in events if e.get("sessionID")), None)
        for e in events:
            if e.get("type") == "error":
                kind = (e.get("error") or {}).get("type")
                return WorkerResult(kind if kind in QUOTA_LIKE else "crash", session, detail=str(e.get("error")))
        text = "\n".join((e.get("part") or {}).get("text", "") for e in events if e.get("type") == "text")
        res = parse_result_block(text)
        if res is None:
            return WorkerResult("no-result", session, detail="no <<<GP-RESULT block")
        return WorkerResult("done", session, res["summary"], res["glass"])
