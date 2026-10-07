"""Worker protocol and the one execution path every adapter shares.

A worker is a subprocess: stdin=/dev/null, own process group, stdout = JSONL events.
gp watches it for stalls (no event for stall_s) and an overall timeout, and kills the
whole group on either (Phase 0 spike / prior art).
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..brief import RESULT_MARKER

QUOTA_LIKE = ("quota", "auth", "unavailable")


@dataclass
class WorkerResult:
    status: str  # done | quota | auth | unavailable | no-result | stalled | timeout | crash
    session: str | None = None
    summary: str = ""
    glass: list = field(default_factory=list)
    detail: str = ""
    usage: dict = field(default_factory=dict)  # cost / input / output tokens, when the worker reports them


def parse_result_block(text: str) -> dict | None:
    """The last `<<<GP-RESULT` marker that is followed by a JSON object with status "done".

    Models sometimes echo the marker again as a closing tag (live Phase 2 run), so walk markers
    from the end and take the first one that parses; free text and decoys never count.
    """
    pos = len(text)
    while True:
        i = text.rfind(RESULT_MARKER, 0, pos)
        if i < 0:
            return None
        pos = i
        rest = text[i + len(RESULT_MARKER):].strip()
        if rest.startswith("```"):
            rest = rest.split("\n", 1)[1] if "\n" in rest else ""
        try:
            obj, _ = json.JSONDecoder().raw_decode(rest)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("status") != "done":
            continue
        glass = [g for g in obj.get("glass") or [] if isinstance(g, dict)
                 and g.get("kind") in ("decision", "reject", "next") and isinstance(g.get("text"), str)][:3]
        return {"summary": str(obj.get("summary", ""))[:200],
                "glass": [{"kind": g["kind"], "text": g["text"][:500]} for g in glass]}


class Worker:
    def __init__(self, name: str, cfg: dict, repo):
        self.name, self.cfg, self.repo = name, cfg, repo

    @property
    def label(self) -> str:
        m = self.cfg.get("model")
        return f"{self.name}/{m}" if m else self.name

    def argv(self, brief_path: Path, cwd: Path) -> tuple[list[str], dict]:
        raise NotImplementedError

    def classify(self, rc: int, events: list[dict]) -> WorkerResult:
        raise NotImplementedError


class UnavailableWorker(Worker):
    """Real adapters arrive in Phase 2; until then a card routed here falls back or stops."""

    def argv(self, brief_path, cwd):
        return None, {}

    def classify(self, rc, events):
        return WorkerResult("unavailable", detail=f"{self.cfg.get('type')} adapter arrives in Phase 2")


def make_worker(name: str, repo) -> Worker:
    from .fake import FakeWorker
    from .opencode import OpenCodeWorker
    cfg = repo.config.get("workers", {}).get(name)
    if cfg is None:
        return UnavailableWorker(name, {"type": "missing"}, repo)
    kind = cfg.get("type")
    if kind == "fake":
        return FakeWorker(name, cfg, repo)
    if kind == "opencode":
        return OpenCodeWorker(name, cfg, repo)
    return UnavailableWorker(name, cfg, repo)


def execute(worker: Worker, brief_path: Path, cwd: Path, log_path: Path, stall_s: float, timeout_s: float) -> WorkerResult:
    try:
        argv, env = worker.argv(brief_path, cwd)
    except RuntimeError as e:  # e.g. the worker's server failed to start: let fallback handle it
        return WorkerResult("unavailable", detail=str(e)[:300])
    if argv is None:
        return worker.classify(1, [])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    with open(log_path, "w") as log, open(str(log_path) + ".stderr", "w") as err:
        try:
            # PWD too: `opencode run` takes its directory from $PWD, not the process cwd (Phase 2 live finding)
            proc = subprocess.Popen(argv, cwd=cwd, env={**os.environ, **env, "PWD": str(cwd)}, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        except FileNotFoundError as e:
            return WorkerResult("unavailable", detail=str(e))
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        start = last = time.monotonic()
        buf = b""
        killed = None
        while True:
            now = time.monotonic()
            if now - start > timeout_s:
                killed = "timeout"
            elif now - last > stall_s:
                killed = "stalled"
            if killed:
                _kill_group(proc)
                break
            if not sel.select(timeout=0.2):
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            last = time.monotonic()
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace")
                log.write(text + "\n")
                log.flush()
                try:
                    ev = json.loads(text)
                    if isinstance(ev, dict):
                        events.append(ev)
                except json.JSONDecodeError:
                    pass
        sel.close()
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            rc = proc.wait()
    if killed:
        return WorkerResult(killed, detail=f"worker {killed} after {int(time.monotonic() - start)}s")
    return worker.classify(rc, events)


def _kill_group(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue
