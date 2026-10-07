"""Repo layout, config, and path normalization."""
from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .util import GpError, git

DEFAULT_CONFIG = {
    "deadline_min": 50,          # Phase 0 spike: observed 1h cache TTL minus margin
    "job_timeout_s": 1800,       # per worker attempt
    "stall_s": 300,              # no worker event for this long -> stalled
    "accept_timeout_s": 600,
    "retries": 2,                # shared by fail and no-result
    "untracked_file_cap": 1_000_000,
    "untracked_total_cap": 20_000_000,
    "env_files": [],
    "accept_allow": [],          # extra argv prefixes, e.g. [["uv", "run", "pytest"]]
    "accept_writable": [],       # extra writable roots for the accept sandbox
    "allow_unsandboxed": False,
    "stale_hours": 24,
    "workers": {
        "opencode": {"type": "opencode", "model": "opencode-go/deepseek-v4.1-flash"},
        "codex": {"type": "codex", "model": "gpt-6-luna"},
    },
    "fallback": {"opencode": "codex", "codex": "opencode"},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "fallback":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Repo:
    root: Path
    config: dict = field(default_factory=dict)

    @classmethod
    def find(cls, cwd: str | Path | None = None) -> "Repo":
        cwd = Path(cwd or os.getcwd())
        out = git(cwd, "rev-parse", "--show-toplevel", check=False).strip()
        if not out:
            raise GpError(f"gp: not inside a git repository: {cwd}")
        root = Path(os.path.realpath(out))
        cfg = DEFAULT_CONFIG
        cfg_path = root / ".glass" / "config.toml"
        if cfg_path.exists():
            cfg = _merge(DEFAULT_CONFIG, tomllib.loads(cfg_path.read_text()))
        return cls(root=root, config=cfg)

    @property
    def glass(self) -> Path:
        return self.root / ".glass"

    @property
    def jobs(self) -> Path:
        return self.glass / "jobs"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    @property
    def cache(self) -> Path:
        base = os.environ.get("GP_CACHE_DIR") or os.path.expanduser("~/.cache/glass-pipe")
        h = hashlib.sha1(str(self.root).encode()).hexdigest()[:10]
        return Path(base) / h

    def norm(self, p: str) -> str:
        """Normalize a user path to a repo-relative posix path (symlinks resolved).

        Raises GpError if it escapes the repo. Trailing slash is dropped; '' means root.
        """
        raw = Path(p)
        full = raw if raw.is_absolute() else self.root / raw
        real = Path(os.path.realpath(full))
        try:
            rel = real.relative_to(self.root)
        except ValueError:
            raise GpError(f"path outside repo: {p}")
        s = rel.as_posix()
        return "" if s == "." else s


def is_under(path: str, entry: str) -> bool:
    """True if repo-relative `path` equals `entry` or lies beneath directory `entry`."""
    if entry == "":
        return True
    pp, ee = PurePosixPath(path).parts, PurePosixPath(entry).parts
    return pp[: len(ee)] == ee


def overlaps(a: str, b: str) -> bool:
    return is_under(a, b) or is_under(b, a)
