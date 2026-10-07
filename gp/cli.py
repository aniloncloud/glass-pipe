"""gp: hand a subtask to a cheap agent; get a receipt, not a transcript."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import apply as applymod
from . import trees
from .cards import CardError, parse_batch
from .job import State, TERMINAL, run_job
from .pipe import add_facts
from .receipt import why
from .repo import Repo
from .runner import deadline_s, submit, wait
from .stats import render, scorecard
from .util import GpError, append_jsonl, now, parse_ts, read_json, tail


def cmd_run(repo, a):
    text = open(a.cards).read() if a.cards else sys.stdin.read()
    try:
        cards = parse_batch(text, repo)
    except CardError as e:
        print(f"gp! rejected, no jobs started: {e}")
        return 2
    ids = submit(repo, cards)
    print("\n".join(wait(repo, ids, a.deadline_s if a.deadline_s is not None else deadline_s(repo))))
    return 0


def cmd_wait(repo, a):
    print("\n".join(wait(repo, a.ids, a.deadline_s if a.deadline_s is not None else deadline_s(repo))))
    return 0


def cmd_show(repo, a):
    jd = repo.job_dir(a.id)
    st = read_json(jd / "state.json")
    if st is None:
        print(f"gp#{a.id} unknown job")
        return 1
    if a.why:
        accepts = sorted(jd.glob("accept-*.txt"))
        last = accepts[-1].read_text() if accepts else (jd / "baseline.txt").read_text() if (jd / "baseline.txt").exists() else ""
        print(why(a.id, st, last), end="")
    else:
        r = jd / "receipt.txt"
        print(r.read_text() if r.exists() else f"gp#{a.id} {st.get('status')}", end="")
    return 0


def cmd_diff(repo, a):
    p = repo.job_dir(a.id) / "patch.diff"
    print(p.read_text() if p.exists() else f"gp#{a.id} has no patch", end="")
    return 0


def cmd_apply(repo, a):
    print(applymod.apply(repo, a.id))
    return 0


def cmd_undo(repo, a):
    print(applymod.undo(repo, a.id))
    return 0


def _drop(repo, job_id):
    trees.remove_tree(repo, repo.cache / job_id / "a")
    trees.remove_tree(repo, repo.cache / job_id / "b")
    import shutil
    shutil.rmtree(repo.cache / job_id, ignore_errors=True)
    trees.drop_ref(repo, job_id)
    st = State(repo.job_dir(job_id))
    if st.get():
        st.update(dropped=True)


def _stale(repo):
    hours = repo.config["stale_hours"]
    out = []
    if not repo.cache.exists():
        return out
    for d in sorted(repo.cache.iterdir()):
        st = read_json(repo.job_dir(d.name) / "state.json", {})
        if st.get("status") in ("running", "queued"):
            continue
        age_h = (time.time() - d.stat().st_mtime) / 3600
        if age_h > hours:
            out.append((d.name, age_h, st.get("status", "?")))
    return out


def cmd_drop(repo, a):
    if a.stale:
        stale = _stale(repo)
        for job_id, _, _ in stale:
            _drop(repo, job_id)
        print(f"gp dropped {len(stale)} stale job tree(s)")
    elif a.id:
        _drop(repo, a.id)
        print(f"gp#{a.id} dropped")
    return 0


def cmd_status(repo, a):
    if repo.jobs.exists():
        for jd in sorted(repo.jobs.iterdir(), key=lambda p: p.stat().st_mtime):
            st = read_json(jd / "state.json", {})
            print(f"gp#{jd.name} {st.get('status')}{' applied' if st.get('apply') == 'applied' else ''}")
    for job_id, age, status in _stale(repo):
        print(f"stale tree: gp#{job_id} {status} {age:.0f}h old → gp drop --stale")
    return 0


def cmd_stats(repo, a):
    print(render(scorecard(repo, a.window_days), a.window_days))
    return 0


def cmd_note(repo, a):
    if a.kind in ("stamp", "override") and a.harness != "human":
        print("gp: stamps and overrides are recorded only with --harness human (the user's decision)")
        return 1
    if a.harness == "human" and not (sys.stdin.isatty() or os.environ.get("GP_ALLOW_NONTTY_HUMAN") == "1"):
        # Guardrail against accidental self-approval (Claude's Bash tool has no TTY). Not a boundary: a
        # process can allocate a pty; see SECURITY.md "Stamps are a guardrail".
        print("gp: --harness human needs an interactive terminal; run this in your own shell")
        return 1
    append_jsonl(repo.glass / "notes.jsonl", {"ts": now(), "job": a.job, "kind": a.kind, "harness": a.harness,
                                               "text": a.text or ""})
    if a.kind in ("decision", "reject", "constraint", "next") and a.text:
        add_facts(repo, a.job, [{"kind": a.kind, "text": a.text}], a.harness if a.harness in ("human", "claude") else "other")
    print(f"gp note {a.kind} recorded" + (f" for gp#{a.job}" if a.job else ""))
    return 0


def cmd_retry(repo, a):
    """Re-run a finished job's card as a new job with a hint for the worker (fresh run, same goal/accept)."""
    import json as _json
    from .util import read_json as _rj
    old = _rj(repo.job_dir(a.id) / "card.json")
    if old is None:
        print(f"gp#{a.id} unknown job")
        return 1
    raw = dict(old.get("raw") or {})
    raw["notes"] = (list(raw.get("notes") or []) + [f"Hint after a previous attempt: {a.hint}"])[-5:]
    raw["revises"] = a.id
    try:
        cards = parse_batch(_json.dumps(raw), repo)
    except CardError as e:
        print(f"gp! retry rejected: {e}")
        return 2
    ids = submit(repo, cards)
    print("\n".join(wait(repo, ids, a.deadline_s if a.deadline_s is not None else deadline_s(repo))))
    return 0


def cmd_ask(repo, a):
    from .ask import ask
    print(ask(repo, " ".join(a.question), to=a.to, long=a.long))
    return 0


def cmd_init(repo, a):
    from .init import init
    print(init(repo, write_settings=a.write_settings))
    return 0


def _hook(event: str) -> int:
    """Claude Code hook entry: JSON on stdin, optional JSON on stdout, always exit 0."""
    import json
    from .hooks import handle
    try:
        out = handle(event, json.loads(sys.stdin.read() or "{}"))
        if out:
            print(json.dumps(out))
    except Exception:
        pass  # a hook must never break the session
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_job"]:
        run_job(Repo.find(argv[1]), argv[2])
        return 0
    if argv[:1] == ["_hook"]:
        return _hook(argv[1] if len(argv) > 1 else "")
    ap = argparse.ArgumentParser(prog="gp", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="start cards (JSONL on stdin or --cards FILE) and wait")
    p.add_argument("--cards")
    p.add_argument("--deadline-s", type=float)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("wait")
    p.add_argument("ids", nargs="+")
    p.add_argument("--deadline-s", type=float)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("show")
    p.add_argument("id")
    p.add_argument("--why", action="store_true")
    p.set_defaults(fn=cmd_show)
    for name, fn in (("diff", cmd_diff), ("apply", cmd_apply), ("undo", cmd_undo)):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.set_defaults(fn=fn)
    p = sub.add_parser("drop")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("id", nargs="?")
    g.add_argument("--stale", action="store_true")
    p.set_defaults(fn=cmd_drop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("retry", help="re-run a job's card with a hint for the worker")
    p.add_argument("id")
    p.add_argument("--hint", required=True)
    p.add_argument("--deadline-s", type=float)
    p.set_defaults(fn=cmd_retry)
    p = sub.add_parser("ask", help="read-only question to the cheap model; capped answer")
    p.add_argument("question", nargs="+")
    p.add_argument("--to")
    p.add_argument("--long", action="store_true")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("init", help="set up glass-pipe in this repo")
    p.add_argument("--write-settings", action="store_true", help="add gp allow rules to .claude/settings.local.json")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("stats")
    p.add_argument("--window-days", type=float, default=7)
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("note")
    p.add_argument("--job")
    p.add_argument("--kind", required=True, choices=["stamp", "override", "decision", "reject", "constraint", "next"])
    p.add_argument("--harness", default="unknown")
    p.add_argument("--text")
    p.set_defaults(fn=cmd_note)
    a = ap.parse_args(argv)
    try:
        return a.fn(Repo.find(), a)
    except GpError as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
