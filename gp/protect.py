"""Which paths a worker may never touch: the accept test, its conftest/fixtures, runner config.

Definition (PLAN "What counts as a file accept names"):
- Explicit paths in the accept argv (`::node` ids and dotted unittest modules resolved) are
  protected, plus conftest.py in their dir and parents, plus sibling fixture dirs.
- A bare runner (no explicit path) protects every test file by pattern.
- Runner config is always protected.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath

from .repo import is_under

FIXTURE_DIRS = ("fixtures", "__snapshots__", "testdata")
TEST_DIRS = ("tests", "test", "__tests__")
TEST_GLOBS = ("*_test.*", "*.test.*", "*.spec.*", "test_*.py")
RUNNER_CONFIG = ("pytest.ini", "tox.ini", "setup.cfg", "package.json", "bunfig.toml", ".mocharc.*",
                 "jest.config.*", "vitest.config.*", "karma.conf.*", "conftest.py")


def is_runner_config(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch.fnmatch(name, g) for g in RUNNER_CONFIG)


class Protection:
    def __init__(self, root: Path, runner_args: list[str]):
        self.root = Path(root)
        self.explicit: list[str] = []
        for tok in runner_args:
            if tok.startswith("-"):
                continue
            path = tok.split("::", 1)[0]
            cands = [path]
            if "/" not in path and "." in path and not path.endswith(".py"):
                cands += [path.replace(".", "/") + ".py", path.replace(".", "/")]
            for c in cands:
                c = c.rstrip("/")
                if c and (self.root / c).exists():
                    if c not in self.explicit:
                        self.explicit.append(c)
                    break
        self.bare = not self.explicit

    def is_protected(self, path: str) -> bool:
        if is_runner_config(path):
            return True
        parts = PurePosixPath(path).parts
        if self.bare:
            if any(p in TEST_DIRS or p in FIXTURE_DIRS for p in parts[:-1]):
                return True
            return any(fnmatch.fnmatch(parts[-1], g) for g in TEST_GLOBS)
        for e in self.explicit:
            if is_under(path, e):
                return True
            edir = PurePosixPath(e).parent if not (self.root / e).is_dir() else PurePosixPath(e)
            for fx in FIXTURE_DIRS:
                if is_under(path, str(edir / fx)):
                    return True
        return False

    def protected_files(self) -> list[str]:
        """Concrete protected files that exist now (for card-stable test hashing)."""
        if self.bare:
            from .util import git
            tracked = git(self.root, "ls-files", "-z", check=False).split("\0")
            return sorted(f for f in tracked if f and self.is_protected(f) and (self.root / f).is_file())
        out = []
        for e in self.explicit:
            p = self.root / e
            if p.is_dir():
                out += [str(f.relative_to(self.root)) for f in sorted(p.rglob("*")) if f.is_file()]
            else:
                out.append(e)
            d = PurePosixPath(e).parent
            while True:
                c = d / "conftest.py"
                if (self.root / c).is_file() and str(c) not in out:
                    out.append(str(c))
                if str(d) in ("", "."):
                    break
                d = d.parent
        return out
