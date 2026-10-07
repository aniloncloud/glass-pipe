"""One card, end to end, in a detached process. Order (PLAN "Baseline"):

snapshot -> env-missing -> accept x2 on pristine tree B (reset between) -> env | flaky | vacuous |
refactor-red stop here -> brief with inlined assertion -> worker on tree A -> freeze -> accept on A ->
accept-wrote-tree -> env -> tamper -> scope -> fail (fresh retry) -> tests-edited / behavior-unchanged / ok.
"""
from __future__ import annotations

import os
import traceback

from . import accept, trees
from .brief import compile_brief
from .cards import Card
from .pipe import binding_facts, ledger
from .receipt import full
from .repo import Repo, is_under
from .tamper import judge, parse_patch, symbol_hints
from .util import atomic_write, now, read_json, tail, write_json
from .workers.base import QUOTA_LIKE, execute, make_worker

TERMINAL = {"ok", "vacuous", "flaky", "env", "env-missing", "refactor-red", "tamper", "scope", "fail",
            "tests-edited", "behavior-unchanged", "no-result", "stalled", "timeout", "crash", "unsandboxed",
            "quota", "auth", "unavailable"}


class State:
    def __init__(self, jd):
        self.path = jd / "state.json"

    def get(self) -> dict:
        return read_json(self.path, {})

    def update(self, **kw) -> dict:
        st = {**self.get(), **kw}
        write_json(self.path, st)
        return st


class _Done(Exception):
    pass


def run_job(repo: Repo, job_id: str) -> None:
    jd = repo.job_dir(job_id)
    card = Card.from_json(read_json(jd / "card.json"))
    state = State(jd)
    state.update(status="running", pid=os.getpid(), started=now())
    try:
        _pipeline(repo, job_id, card, state, jd)
    except _Done:
        pass
    except accept.Unsandboxed as e:
        _finish(repo, job_id, state, jd, "unsandboxed", detail=str(e))
    except Exception:
        (jd / "crash.txt").write_text(traceback.format_exc())
        _finish(repo, job_id, state, jd, "crash", detail=tail(traceback.format_exc(), 3))


def _finish(repo, job_id, state, jd, status, **kw):
    st = state.update(status=status, finished=now(), **kw)
    atomic_write(jd / "receipt.txt", full(job_id, st))   # receipt before terminal is visible to waiters
    st = state.update(terminal=True)
    ledger(repo, "done", job=job_id, status=status, worker=st.get("worker"), attempts=st.get("attempts"),
           files=[f["path"] for f in st.get("files") or []],
           lines=sum(f["adds"] + f["dels"] for f in st.get("files") or []))
    raise _Done


def _pipeline(repo: Repo, job_id: str, card: Card, state: State, jd):
    cfg = repo.config
    fin = lambda status, **kw: _finish(repo, job_id, state, jd, status, **kw)
    prot = card.protection(repo)

    missing = trees.env_missing(repo, prot)
    if missing:
        fin("env-missing", detail=f"ignored files under accept paths not in env_files: {', '.join(missing[:5])}")

    snap = trees.build_snapshot(repo, job_id)
    state.update(base=snap.base, skipped_untracked=snap.skipped)
    tree_a, tree_b = repo.cache / job_id / "a", repo.cache / job_id / "b"

    # --- baseline: twice on pristine B, reset between; no worker unless red (or green refactor)
    trees.make_tree(repo, snap.base, tree_b)
    try:
        r1 = accept.run_accept(card.argv, tree_b, [tree_b], cfg)
        trees.reset_tree(repo, snap.base, tree_b)
        r2 = accept.run_accept(card.argv, tree_b, [tree_b], cfg)
    finally:
        trees.remove_tree(repo, tree_b)
    (jd / "baseline.txt").write_text(f"--- run 1 rc={r1.rc}\n{r1.output}\n--- run 2 rc={r2.rc}\n{r2.output}\n")
    state.update(baseline=[r1.rc, r2.rc], sandbox=r1.sandbox)
    if r1.cannot_run or r2.cannot_run:
        fin("env", detail="accept could not run on the untouched tree: " + tail((r1 if r1.cannot_run else r2).output, 2))
    state.update(baseline_done=True)
    if r1.green != r2.green:
        fin("flaky", detail="the two baseline runs disagree (one red, one green); fix the test")
    green = r1.green
    refactor = card.kind == "refactor"
    if green and not refactor:
        fin("vacuous", detail="accept is already green on the untouched tree; it does not test this change")
    if not green and refactor:
        fin("refactor-red", detail="refactor cards need a green baseline; drop kind to make the red test pass")

    # --- worker loop
    trees.make_tree(repo, snap.base, tree_a)
    facts = binding_facts(repo)
    worker_name, fell_back = card.to, None
    retries = cfg["retries"]
    attempt, retry_tail, retry_reason = 0, None, None
    timeout = card.timeout or cfg["job_timeout_s"]
    while True:
        attempt += 1
        if attempt > 1:
            trees.reset_tree(repo, snap.base, tree_a)
        text = compile_brief(card, tests=prot.explicit, baseline_output=r1.output, facts=facts,
                             retry_tail=retry_tail, retry_reason=retry_reason)
        brief_path = jd / f"brief-{attempt}.txt"
        brief_path.write_text(text)
        worker = make_worker(worker_name, repo)
        state.update(worker=worker.label, attempts=attempt)
        wr = execute(worker, brief_path, tree_a, jd / f"worker-{attempt}.jsonl", cfg["stall_s"], timeout)
        usage = state.get().get("usage") or {"cost": 0.0, "input": 0, "output": 0}
        for k in ("cost", "input", "output"):
            usage[k] = usage.get(k, 0) + (wr.usage or {}).get(k, 0)
        state.update(session=wr.session, usage=usage)
        if wr.status in QUOTA_LIKE:
            fb = cfg.get("fallback", {}).get(worker_name)
            if fell_back is None and fb and fb in cfg.get("workers", {}):
                fell_back, worker_name = worker_name, fb
                state.update(fell_back_from=f"{fell_back} ({wr.status})")
                attempt -= 1  # fallback does not spend the retry budget
                continue
            fin(wr.status, detail=wr.detail)
        if wr.status in ("stalled", "timeout", "crash"):
            fin(wr.status, detail=wr.detail)
        if wr.status == "no-result":
            if retries:
                retries -= 1
                retry_tail, retry_reason = None, "Your previous run ended without the <<<GP-RESULT block. End with it this time."
                continue
            fin("no-result", detail=wr.detail)

        frozen, patch = trees.freeze(tree_a, snap.base)
        (jd / "patch.diff").write_text(patch)
        changes = parse_patch(patch)
        files = [{"path": c.path, "adds": c.adds, "dels": c.dels} for c in changes]
        state.update(files=files, symbols=symbol_hints(patch), summary=wr.summary, glass=wr.glass)

        ar = accept.run_accept(card.argv, tree_a, [tree_a], cfg)
        (jd / f"accept-{attempt}.txt").write_text(ar.output)
        state.update(accept_green=ar.green, accept_rc=ar.rc)
        changed = {c.path for c in changes}
        wrote = [p for p in trees.changed_since(tree_a, frozen)
                 if p in changed or prot.is_protected(p) or any(is_under(p, s) for s in card.scope)]
        if ar.cannot_run:
            fin("env", detail="accept could not run after the worker: " + tail(ar.output, 2))
        if wrote:
            fin("tamper", detail=f"accept wrote the tree after freeze: {', '.join(wrote[:5])}")
        verdict = judge(changes, card, prot)
        if verdict.status:
            fin(verdict.status, detail=verdict.detail)
        if not ar.green:
            if retries:
                retries -= 1
                retry_tail = ar.output
                retry_reason = None if changes else "You made no changes."
                continue
            fin("fail", detail=tail(ar.output, 3))
        if not changes:
            fin("flaky", detail="accept went green with no changes after a red baseline")
        if verdict.tests_edited:
            fin("tests-edited", detail=verdict.detail)
        if refactor:
            fin("behavior-unchanged")
        fin("ok")
