"""The glass pipe: facts that bind future briefs. Facts enter only from applied jobs or a human."""
from __future__ import annotations

from .repo import Repo
from .util import append_jsonl, atomic_write, now, read_json, read_jsonl

BINDING = ("decision", "reject", "constraint")


def ledger(repo: Repo, ev: str, **kw) -> None:
    append_jsonl(repo.glass / "ledger.jsonl", {"ts": now(), "ev": ev, **kw})


def ledger_events(repo: Repo) -> list[dict]:
    return read_jsonl(repo.glass / "ledger.jsonl")


def applied_stack(repo: Repo) -> list[dict]:
    return read_json(repo.glass / "applied.json", [])


def add_facts(repo: Repo, job: str | None, facts: list[dict], source: str) -> None:
    for f in facts:
        append_jsonl(repo.glass / "pipe.jsonl", {"ts": now(), "job": job, "kind": f["kind"],
                                                 "text": f["text"], "source": source})
    render_thread(repo)


def live_facts(repo: Repo) -> list[dict]:
    applied = {e["job"] for e in applied_stack(repo)}
    return [f for f in read_jsonl(repo.glass / "pipe.jsonl")
            if f.get("source") in ("human", "claude") or f.get("job") in applied]


def binding_facts(repo: Repo) -> list[dict]:
    return [f for f in live_facts(repo) if f["kind"] in BINDING]


def render_thread(repo: Repo) -> None:
    facts = live_facts(repo)
    lines = ["# glass-pipe thread", "",
             "Facts from applied jobs and humans only. Binding facts are injected into worker briefs.", "",
             "## Binding"]
    lines += [f"- {f['kind']}: {f['text']}  _({f.get('job') or 'human'})_" for f in facts if f["kind"] in BINDING][-12:] or ["- (none)"]
    lines += ["", "## Next"]
    lines += [f"- {f['text']}  _({f.get('job') or 'human'})_" for f in facts if f["kind"] == "next"][-15:] or ["- (none)"]
    atomic_write(repo.glass / "THREAD.md", "\n".join(lines) + "\n")
