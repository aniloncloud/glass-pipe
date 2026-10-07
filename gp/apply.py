"""Clean-path apply with a landing check; LIFO undo.

apply: every patch file must still match the snapshot (else `dirty`, with the reason) ->
git apply -> accept on the MAIN tree -> red: restore the pre-apply bytes of the patch files
from the snapshot and record landed=false (accept debris is ignored) -> green: push onto the
LIFO stack with post-apply hashes, release worktrees, publish the job's facts.
"""
from __future__ import annotations

import shutil
import subprocess

from . import accept, trees
from .cards import Card
from .job import State
from .pipe import add_facts, applied_stack, ledger, render_thread
from .receipt import NEEDS_OVERRIDE, NEEDS_STAMP, ELIGIBLE, SEMANTIC
from .repo import Repo
from .util import blob_sha, flock, now, read_json, read_jsonl, run, write_json


def human_notes(repo: Repo, job_id: str, kind: str) -> list[dict]:
    return [n for n in read_jsonl(repo.glass / "notes.jsonl")
            if n.get("job") == job_id and n.get("kind") == kind and n.get("harness") == "human"]


def eligible(repo: Repo, job_id: str, st: dict) -> tuple[bool, str]:
    status = st.get("status")
    if status in ELIGIBLE:
        return True, ""
    if status in NEEDS_STAMP:
        return (True, "stamped") if human_notes(repo, job_id, "stamp") else (False, "needs a human stamp")
    if status in NEEDS_OVERRIDE:
        return (True, "overridden") if human_notes(repo, job_id, "override") else (False, "tamper; needs a human override")
    return False, "status is not apply-eligible"


def _restore(repo: Repo, base: str, files: list[str]) -> None:
    for f in files:
        sha = trees.base_sha(repo, base, f)
        target = repo.root / f
        if sha is None:
            if target.exists():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.run(["git", "-C", str(repo.root), "cat-file", "blob", sha],
                                              capture_output=True, check=True).stdout)


def _dirty_reason(repo: Repo, st: dict, mism: list[str]) -> str:
    started = st.get("started", "")
    for f in mism:
        for e in reversed(applied_stack(repo)):
            if e.get("ts", "") >= started and f in e.get("files", {}):
                return f"scope file {f} changed since snapshot (job {e['job']} applied)"
    return f"scope file {mism[0]} changed since snapshot (unrelated edit)"


def apply(repo: Repo, job_id: str) -> str:
    jd = repo.job_dir(job_id)
    state = State(jd)
    st = state.get()
    if not st:
        return f"gp#{job_id} unknown job"
    if not st.get("terminal"):
        return f"gp#{job_id} {st.get('status')} not finished → gp wait {job_id}"
    if any(e["job"] == job_id for e in applied_stack(repo)):
        return f"gp#{job_id} already applied"
    ok, why = eligible(repo, job_id, st)
    if not ok:
        return f"gp#{job_id} not-applied {st.get('status')}: {why}"
    files = [f["path"] for f in st.get("files") or []]
    if not files:
        return f"gp#{job_id} not-applied: empty patch"
    patch = (jd / "patch.diff").read_text()
    card = Card.from_json(read_json(jd / "card.json"))
    with flock(repo.glass / "apply.lock"):
        mism = [f for f in files if trees.base_sha(repo, st["base"], f) != blob_sha(repo.root / f)]
        if mism:
            reason = _dirty_reason(repo, st, mism)
            ledger(repo, "dirty", job=job_id, reason=reason)
            state.update(apply="dirty", apply_detail=reason)
            return f"gp#{job_id} dirty: {reason} · patch kept → gp show {job_id}"
        p = run(["git", "-C", str(repo.root), "apply", "--binary", "-"], input=patch, check=False)
        if p.returncode != 0:
            ledger(repo, "dirty", job=job_id, reason="patch does not apply")
            return f"gp#{job_id} dirty: patch does not apply ({p.stderr.strip()[:120]})"
        # Landing check runs the worker's code on YOUR tree: inside the repo it may write only test
        # debris (never .git or other files); secrets are read-denied (security audit).
        extra = [str(repo.root / p) for p in repo.config.get("landing_writable", [])]
        ar = accept.run_accept(card.argv, repo.root, extra, repo.config, deny_write=[str(repo.root / ".git")],
                               write_regex=accept.debris_regexes(str(repo.root)))
        (jd / "landing.txt").write_text(ar.output)
        if not ar.green:
            _restore(repo, st["base"], files)
            restored = all(trees.base_sha(repo, st["base"], f) == blob_sha(repo.root / f) for f in files)
            ledger(repo, "apply", job=job_id, landed=False, restored=restored)
            state.update(apply="landing-red", landed=False)
            return (f"gp#{job_id} landing-red: accept red on main tree after apply; reverted "
                    f"({'scope files restored' if restored else 'RESTORE INCOMPLETE'}) → .glass/jobs/{job_id}/landing.txt")
        post = {f: blob_sha(repo.root / f) for f in files}
        stack = applied_stack(repo)
        stack.append({"job": job_id, "files": post, "ts": now()})
        write_json(repo.glass / "applied.json", stack)
        lines = sum(f["adds"] + f["dels"] for f in st["files"])
        ledger(repo, "apply", job=job_id, landed=True, lines=lines, status=st["status"], stamp=why or None)
        state.update(apply="applied", landed=True, applied_at=now())
        add_facts(repo, job_id, st.get("glass") or [], "worker")
    trees.remove_tree(repo, repo.cache / job_id / "a")
    shutil.rmtree(repo.cache / job_id, ignore_errors=True)  # worker server home, admin pointer files
    trees.drop_ref(repo, job_id)
    return f"gp#{job_id} applied landed✓ {len(files)}f · {SEMANTIC}"


def undo(repo: Repo, job_id: str) -> str:
    with flock(repo.glass / "apply.lock"):
        stack = applied_stack(repo)
        if not stack or stack[-1]["job"] != job_id:
            later = [e["job"] for e in stack[[e["job"] for e in stack].index(job_id) + 1:]] if any(
                e["job"] == job_id for e in stack) else []
            if later:
                return f"gp#{job_id} undo refused: LIFO; undo later job(s) first: {', '.join(reversed(later))}"
            return f"gp#{job_id} undo refused: not applied"
        entry = stack[-1]
        changed = [f for f, sha in entry["files"].items() if blob_sha(repo.root / f) != sha]
        if changed:
            return f"gp#{job_id} undo refused: {changed[0]} changed after apply"
        patch = (repo.job_dir(job_id) / "patch.diff").read_text()
        p = run(["git", "-C", str(repo.root), "apply", "-R", "--binary", "-"], input=patch, check=False)
        if p.returncode != 0:
            return f"gp#{job_id} undo refused: reverse patch does not apply ({p.stderr.strip()[:120]})"
        stack.pop()
        write_json(repo.glass / "applied.json", stack)
        ledger(repo, "undo", job=job_id)
        State(repo.job_dir(job_id)).update(apply="undone")
        render_thread(repo)
    return f"gp#{job_id} undone"
