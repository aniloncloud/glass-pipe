"""The scorecard. A card is a successful offload only if all five counters pass (PLAN
"Proving the offload worked"). No savings number is ever printed without these five.
"""
from __future__ import annotations

import datetime as dt
import statistics

from .pipe import ledger_events
from .repo import Repo
from .runner import test_hash
from .cards import Card
from .tamper import parse_patch
from .util import git, parse_ts, read_json, read_jsonl


def _fixup_lines(repo: Repo, job_id: str, files: list[str], since: str, window_days: float) -> int:
    """Lines in later commits on the job's files that are not lines of the job's own patch."""
    patch = (repo.job_dir(job_id) / "patch.diff")
    own = set()
    if patch.exists():
        for c in parse_patch(patch.read_text()):
            own |= {("+", l) for l in c.added} | {("-", l) for l in c.removed}
    start = parse_ts(since)
    lo, hi = int(start.timestamp()), (start + dt.timedelta(days=window_days)).timestamp()
    # strictly after the apply second: git --since is inclusive at 1s resolution, so a commit made in
    # the same second as the apply (e.g. the repo's own setup) must not count as a fix-up (found by CI)
    log = git(repo.root, "log", "--format=%H %ct", "--", *files, check=False)
    shas = [h for h, ct in (l.split() for l in log.splitlines() if l.strip()) if lo < int(ct) <= hi]
    out = "".join(git(repo.root, "show", "--format=", "-p", sha, "--", *files, check=False) for sha in shas)
    n = 0
    for c in parse_patch(out):
        n += sum(1 for l in c.added if ("+", l) not in own) + sum(1 for l in c.removed if ("-", l) not in own)
    return n


def scorecard(repo: Repo, window_days: float = 7) -> dict:
    events = ledger_events(repo)
    edits = read_jsonl(repo.glass / "edits.jsonl")  # Phase 3 hook writes this; Phase 1 uses a stub log
    submits = {e["job"]: e for e in events if e["ev"] == "submit"}
    rows = []
    for job_id, sub in submits.items():
        jd = repo.job_dir(job_id)
        st = read_json(jd / "state.json", {})
        card_json = read_json(jd / "card.json")
        applies = [e for e in events if e["ev"] == "apply" and e["job"] == job_id]
        landed_ev = next((e for e in applies if e.get("landed")), None)
        landed = bool(landed_ev) and st.get("status") == "ok"
        gate = st.get("status") == "ok"
        undone = any(e["ev"] == "undo" and e["job"] == job_id for e in events)
        files = [f["path"] for f in st.get("files") or []]
        fix = _fixup_lines(repo, job_id, files, landed_ev["ts"], window_days) if landed_ev and files else 0
        stayed = landed and not undone and fix == 0
        done_ts = st.get("finished", sub["ts"])
        rescue = [e for e in edits if parse_ts(e.get("ts", "1970-01-01T00:00:00Z")) > parse_ts(done_ts) and any(
            e.get("path") == f or e.get("path", "").startswith(s.rstrip("/") + "/") or e.get("path") == s
            for f in files for s in (card_json or {}).get("scope", []))]
        revised = [s for s in submits.values() if s.get("revises") == job_id and (
            s["goal"] != sub["goal"] or s["accept"] != sub["accept"] or (s.get("edit_tests") and not sub.get("edit_tests")))]
        hash_now = test_hash(repo, Card.from_json(card_json)) if card_json else None
        stable = not revised and hash_now == sub.get("test_hash")
        lines = landed_ev.get("lines", 0) if landed_ev else 0
        rows.append(dict(job=job_id, status=st.get("status"), landed=landed, gate=gate, stayed=stayed,
                         rescued=bool(rescue), stable=stable, lines=lines, fix=fix,
                         rescue_lines=sum(e.get("lines", 1) for e in rescue),
                         baseline_done=bool(st.get("baseline_done")),
                         stamped=landed_ev.get("stamp") == "stamped" if landed_ev else False,
                         minutes=((parse_ts(landed_ev["ts"]) - parse_ts(sub["ts"])).total_seconds() / 60) if landed_ev else None))
    n = len(rows)
    ok = [r for r in rows if r["landed"] and r["gate"] and r["stayed"] and not r["rescued"] and r["stable"]]
    applied = sum(r["lines"] for r in rows if r["landed"])
    stayed_lines = sum(r["lines"] for r in rows if r["stayed"] and not r["rescued"])
    denom = applied + sum(r["rescue_lines"] for r in rows) + sum(r["fix"] for r in rows)
    base = [r for r in rows if r["baseline_done"]]
    vac = [r for r in base if r["status"] in ("vacuous", "tests-edited") or (r["status"] == "behavior-unchanged" and r["stamped"])]
    times = [r["minutes"] for r in rows if r["stayed"] and r["minutes"] is not None]
    return dict(
        submitted=n, success=len(ok),
        counters={k: sum(1 for r in rows if (not r["rescued"] if k == "not-rescued" else r[{"landed": "landed", "gate-accepted": "gate", "stayed": "stayed", "card-stable": "stable"}[k]]))
                  for k in ("landed", "gate-accepted", "stayed", "not-rescued", "card-stable")},
        offload_rate=(stayed_lines / denom) if denom else None,
        vacuous_rate=(len(vac) / len(base)) if base else None, vacuous_denominator=len(base),
        median_minutes_to_stayed=statistics.median(times) if times else None,
        rows=rows)


def render(sc: dict, window_days: float) -> str:
    n = sc["submitted"]
    out = [f"cards submitted: {n}", f"successful offloads: {sc['success']}/{n}  (landed ∧ gate-accepted ∧ stayed ∧ not-rescued ∧ card-stable; window {window_days:g}d)"]
    for k, v in sc["counters"].items():
        out.append(f"  {k:<14} {v}/{n}")
    fmt = lambda x: "n/a" if x is None else f"{x:.0%}"
    out.append(f"offload rate: {fmt(sc['offload_rate'])}  (stayed lines / applied + rescued + fix-up lines)")
    out.append(f"vacuous rate: {fmt(sc['vacuous_rate'])}  (of {sc['vacuous_denominator']} cards that finished baseline)")
    m = sc["median_minutes_to_stayed"]
    out.append(f"net time: median {'n/a' if m is None else f'{m:.1f} min'} submit→stayed apply (compare with Claude editing directly in Phase 4)")
    out.append("rescued counts come from .glass/edits.jsonl (stub until the Phase 3 edit-log hook); do not quote an offload rate before Phase 3")
    out.append("savings: not computed here; token savings are measured in Phase 4 and are only reported next to these five counters")
    return "\n".join(out)
