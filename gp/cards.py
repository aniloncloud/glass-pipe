"""Cards: what Claude writes. JSONL, one card per line; validated before any job starts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from . import accept
from .protect import Protection
from .repo import Repo, overlaps
from .util import GpError

KINDS = (None, "refactor")
MAX_NOTES, MAX_NOTE_CHARS = 5, 500


class CardError(GpError):
    pass


@dataclass
class Card:
    to: str
    goal: str
    accept: str
    argv: list[str]
    scope: list[str]
    kind: str | None = None
    edit_tests: bool = False
    timeout: int | None = None
    revises: str | None = None
    notes: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Card":
        return cls(**d)

    def protection(self, repo: Repo) -> Protection:
        return Protection(repo.root, accept.runner_args(self.argv, repo.config.get("accept_allow")))


def _one(n: int, line: str, repo: Repo) -> Card:
    def err(msg):
        return CardError(f"card {n}: {msg}")

    try:
        d = json.loads(line)
    except json.JSONDecodeError as e:
        raise err(f"not valid JSON ({e.msg})")
    if not isinstance(d, dict):
        raise err("must be a JSON object")
    unknown = set(d) - {"to", "goal", "accept", "scope", "kind", "edit_tests", "timeout", "revises", "notes"}
    if unknown:
        raise err(f"unknown fields {sorted(unknown)}")
    to = d.get("to")
    if to not in repo.config.get("workers", {}):
        raise err(f"unknown worker {to!r}; configured: {sorted(repo.config.get('workers', {}))}")
    goal = d.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise err("goal is required")
    try:
        argv = accept.parse_accept(d.get("accept", ""), repo.config.get("accept_allow"))
    except accept.AcceptRejected as e:
        raise err(str(e))
    scope_in = d.get("scope")
    if not isinstance(scope_in, list) or not scope_in or not all(isinstance(s, str) and s.strip() for s in scope_in):
        raise err("scope is required: a non-empty list of implementation files or dirs (omitted scope never means the whole repo)")
    try:
        scope = [repo.norm(s) for s in scope_in]
    except GpError as e:
        raise err(str(e))
    if "" in scope:
        raise err("scope may not be the repo root")
    kind = d.get("kind")
    if kind not in KINDS:
        raise err(f"kind must be omitted or 'refactor', got {kind!r}")
    edit_tests = bool(d.get("edit_tests", False))
    notes = d.get("notes", [])
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list) or len(notes) > MAX_NOTES or not all(
            isinstance(n, str) and 0 < len(n) <= MAX_NOTE_CHARS for n in notes):
        raise err(f"notes must be up to {MAX_NOTES} strings of at most {MAX_NOTE_CHARS} chars")
    c = Card(to=to, goal=goal, accept=d["accept"], argv=argv, scope=scope, kind=kind, edit_tests=edit_tests,
             timeout=d.get("timeout"), revises=d.get("revises"), notes=notes, raw=d)
    prot = c.protection(repo)
    protected = [s for s in scope if prot.is_protected(s)]
    if protected and not edit_tests:
        raise err(f"scope includes protected test/config paths {protected}; the worker may not edit tests "
                  "(set \"edit_tests\": true to override; the result then needs a human stamp)")
    if len(protected) == len(scope):
        raise err("scope must name at least one path outside the protected test/config set")
    return c


def parse_batch(text: str, repo: Repo) -> list[Card]:
    lines = [(i, l) for i, l in enumerate(text.splitlines(), 1) if l.strip()]
    if not lines:
        raise CardError("no cards given")
    out = [_one(n, l, repo) for n, (_, l) in enumerate(lines, 1)]
    for i, a in enumerate(out):
        for j in range(i + 1, len(out)):
            for sa in a.scope:
                for sb in out[j].scope:
                    if overlaps(sa, sb):
                        raise CardError(f"cards {i + 1} and {j + 1}: scopes overlap ({sa!r} vs {sb!r}); "
                                        "cards in one batch must touch disjoint files")
    return out
