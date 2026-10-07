"""Read a frozen worker patch: what changed, and whether it cheats or strays out of scope."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cards import Card
from .protect import Protection, is_runner_config
from .repo import is_under

SKIP_PATTERNS = [
    r"@pytest\.mark\.(skip|skipif|xfail)\b",
    r"\bpytest\.(skip|xfail)\s*\(",
    r"@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b",
    r"\bunittest\.skip\s*\(",
    r"\bself\.skipTest\s*\(",
    r"\b(it|test|describe|context)\.(skip|only|todo)\s*\(",
    r"\bx(it|describe|test)\s*\(",
    r"#\[ignore\]",
    r"\bt\.Skip(Now|f)?\s*\(",
]
_SKIP = re.compile("|".join(SKIP_PATTERNS))


@dataclass
class FileChange:
    path: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)  # git header lines: "old mode", "new mode", "new file mode 120000"…

    @property
    def adds(self):
        return len(self.added)

    @property
    def dels(self):
        return len(self.removed)


def parse_patch(text: str) -> list[FileChange]:
    out: list[FileChange] = []
    cur = None
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = FileChange(m.group(2) if m else line.split()[-1][2:])
            out.append(cur)
            in_hunk = False
        elif cur is None:
            continue
        elif not in_hunk and (line.startswith(("old mode ", "new mode ")) or
                              (line.startswith(("new file mode ", "deleted file mode ")) and line.endswith("120000"))):
            cur.modes.append(line)
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            cur.added.append(line[1:])
        elif in_hunk and line.startswith("-"):
            cur.removed.append(line[1:])
    return out


def symbol_hints(text: str) -> dict[str, list[str]]:
    """Enclosing-symbol hints from hunk headers. Hints only: git names the preceding symbol."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = m.group(2) if m else None
        elif line.startswith("@@") and cur:
            ctx = line.split("@@", 2)[-1].strip()
            if ctx and ctx not in out.setdefault(cur, []):
                out[cur].append(ctx)
    return out


@dataclass
class Verdict:
    status: str | None  # None, "tamper", "scope"
    detail: str = ""
    tests_edited: bool = False


def judge(changes: list[FileChange], card: Card, prot: Protection) -> Verdict:
    # 2a: tamper always wins
    for ch in changes:
        if ch.modes:  # symlinks can point outside the repo; mode flips (+x) are not implementation work
            return Verdict("tamper", f"patch changes file mode or adds a symlink in {ch.path}: {'; '.join(ch.modes)}")
    for ch in changes:
        for l in ch.added:
            if _SKIP.search(l):
                return Verdict("tamper", f"added skip/only marker in {ch.path}: {l.strip()}")
    for ch in changes:
        if is_runner_config(ch.path):
            return Verdict("tamper", f"touched runner config {ch.path}")
        if ch.path.endswith("pyproject.toml") and any("pytest" in l for l in ch.added + ch.removed):
            return Verdict("tamper", f"touched pytest config in {ch.path}")
    overridable = lambda p: card.edit_tests and any(is_under(p, s) for s in card.scope)
    for ch in changes:
        if prot.is_protected(ch.path) and not overridable(ch.path):
            return Verdict("tamper", f"touched protected test path {ch.path}")
    # 3: scope
    outside = [ch.path for ch in changes if not any(is_under(ch.path, s) for s in card.scope)]
    if outside:
        return Verdict("scope", f"changed paths outside scope: {', '.join(outside)}")
    edited = [ch.path for ch in changes if prot.is_protected(ch.path)]
    return Verdict(None, tests_edited=bool(edited), detail=(f"tests edited: {', '.join(edited)}" if edited else ""))
