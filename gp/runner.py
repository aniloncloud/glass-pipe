"""gp run: validate the batch, start one detached process per card, wait until done or the deadline.

stdout is receipts only: one line per job, in card order. A job still running at the deadline
prints `→ gp wait <id>` so Claude has an id to pass on.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from .cards import Card
from .pipe import ledger
from .receipt import line
from .repo import Repo
from .util import atomic_write, new_id, now, read_json, write_json


def test_hash(repo: Repo, card: Card) -> str:
    h = hashlib.sha1()
    for f in card.protection(repo).protected_files():
        p = repo.root / f
        h.update(f.encode() + b"\0" + (p.read_bytes() if p.is_file() else b"<missing>") + b"\0")
    return h.hexdigest()


def submit(repo: Repo, cards: list[Card]) -> list[str]:
    ids = []
    for card in cards:
        job_id = new_id()
        while repo.job_dir(job_id).exists():
            job_id = new_id()
        jd = repo.job_dir(job_id)
        jd.mkdir(parents=True)
        os.chmod(repo.glass, 0o700)  # briefs, worker output, stamps: owner-only (security audit #7)
        write_json(jd / "card.json", card.to_json())
        write_json(jd / "state.json", {"status": "queued", "submitted": now()})
        ledger(repo, "submit", job=job_id, to=card.to, goal=card.goal, accept=card.accept, scope=card.scope,
               edit_tests=card.edit_tests, kind=card.kind, revises=card.revises, test_hash=test_hash(repo, card))
        pid = spawn(repo, job_id)
        (jd / "spawn.pid").write_text(str(pid))  # separate file: never races the job's own state writes
        ids.append(job_id)
    return ids


def spawn(repo: Repo, job_id: str) -> None:
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": pkg_root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    log = open(repo.job_dir(job_id) / "job.log", "w")
    p = subprocess.Popen([sys.executable, "-m", "gp", "_job", str(repo.root), job_id], cwd=repo.root, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return p.pid


def deadline_s(repo: Repo) -> float:
    env = os.environ.get("GP_DEADLINE_S")
    return float(env) if env else float(repo.config["deadline_min"]) * 60


def _alive(pid) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except TypeError:
        return True   # no pid recorded yet
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _reap() -> None:
    """Collect exited job children so a crashed job is not mistaken for a live zombie."""
    try:
        while os.waitpid(-1, os.WNOHANG)[0]:
            pass
    except ChildProcessError:
        pass


def _pid(repo: Repo, job_id: str, st: dict):
    if st.get("pid"):
        return st["pid"]
    try:
        return int((repo.job_dir(job_id) / "spawn.pid").read_text())
    except (FileNotFoundError, ValueError):
        return None  # not spawned yet: treated as alive


def mark_dead(repo: Repo, job_id: str) -> None:
    """A job process died without finishing: still write the receipt and the ledger event."""
    from .receipt import full
    jd = repo.job_dir(job_id)
    st = {**read_json(jd / "state.json", {}), "status": "crash", "detail": "job process died", "finished": now()}
    write_json(jd / "state.json", st)
    atomic_write(jd / "receipt.txt", full(job_id, st))
    write_json(jd / "state.json", {**st, "terminal": True})
    ledger(repo, "done", job=job_id, status="crash", worker=st.get("worker"), attempts=st.get("attempts"),
           files=[], lines=0)


def wait(repo: Repo, ids: list[str], deadline: float) -> list[str]:
    end = time.monotonic() + deadline
    pending = set(ids)
    while pending and time.monotonic() < end:
        _reap()
        for j in list(pending):
            st = read_json(repo.job_dir(j) / "state.json", {})
            if st.get("terminal"):
                pending.discard(j)
            elif st.get("status") in ("running", "queued") and not _alive(_pid(repo, j, st)):
                time.sleep(0.2)  # the job may have just finished; re-read before declaring it dead
                if not read_json(repo.job_dir(j) / "state.json", {}).get("terminal"):
                    mark_dead(repo, j)
                pending.discard(j)
        if pending:
            time.sleep(0.25)
    return [line(j, read_json(repo.job_dir(j) / "state.json", {})) for j in ids]
