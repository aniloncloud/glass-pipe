"""Snapshots and temporary worktrees. gp owns every git write; workers never commit (Phase 0 spike).

Base commit = main tree at submit: `git stash create` (tracked edits, or HEAD) plus untracked,
non-ignored files (capped), built in a private index and pinned at refs/glass/<job>.
Both trees of a pair start from it, so untracked copies never appear in the worker's diff.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .protect import Protection, TEST_DIRS
from .repo import Repo
from .util import GIT_ID, GpError, flock, git

# Accept/runtime debris: never part of a patch, never "accept wrote the tree", never env-missing.
JUNK = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv",
        ".coverage", "coverage", ".nyc_output", "*.pyc", ".DS_Store", ".glass", ".opencode")
JUNK_PATHSPEC = [f":(exclude,glob)**/{j}" for j in JUNK] + [f":(exclude,glob)**/{j}/**" for j in JUNK]


def is_junk(path: str) -> bool:
    return any(fnmatch.fnmatch(part, j) for part in PurePosixPath(path).parts for j in JUNK)


@dataclass
class Snapshot:
    base: str
    untracked: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _lock(repo: Repo):
    return flock(repo.glass / "git.lock")


def build_snapshot(repo: Repo, job_id: str) -> Snapshot:
    cfg = repo.config
    with _lock(repo):
        if not git(repo.root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
            raise GpError("gp: the repo needs at least one commit")
        start = git(repo.root, "stash", "create").strip() or git(repo.root, "rev-parse", "HEAD").strip()
        listed = [p for p in git(repo.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
        keep, skipped, total = [], [], 0
        for p in listed:
            if is_junk(p):
                continue
            size = (repo.root / p).stat().st_size if (repo.root / p).is_file() else 0
            if size > cfg["untracked_file_cap"] or total + size > cfg["untracked_total_cap"]:
                skipped.append(p)
                continue
            total += size
            keep.append(p)
        fd, idx = tempfile.mkstemp(prefix="gp-index-")
        os.close(fd)
        os.unlink(idx)
        env = {"GIT_INDEX_FILE": idx}
        try:
            git(repo.root, "read-tree", start, env=env)
            for p in keep:
                sha = git(repo.root, "hash-object", "-w", "--", p).strip()
                mode = "100755" if os.access(repo.root / p, os.X_OK) else "100644"
                git(repo.root, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{p}", env=env)
            tree = git(repo.root, "write-tree", env=env).strip()
        finally:
            if os.path.exists(idx):
                os.unlink(idx)
        base = git(repo.root, *GIT_ID, "commit-tree", tree, "-p", start, "-m", f"glass-pipe base {job_id}").strip()
        git(repo.root, "update-ref", f"refs/glass/{job_id}", base)
    return Snapshot(base, keep, skipped)


def env_missing(repo: Repo, prot: Protection) -> list[str]:
    """Ignored files the accept command likely reads that will not be copied into the worktree."""
    env_files = set(repo.config.get("env_files", []))
    dirs = {str(PurePosixPath(e).parent) if not (repo.root / e).is_dir() else e for e in prot.explicit}
    if prot.bare:
        dirs |= {d for d in TEST_DIRS if (repo.root / d).is_dir()}
    out = []
    for d in sorted(dirs):
        d = "" if d == "." else d
        args = ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"] + ([d] if d else [])
        for p in git(repo.root, *args).split("\0"):
            if p and not is_junk(p) and p not in env_files and (d or "/" not in p):
                out.append(p)
    return sorted(set(out))


def copy_env_files(repo: Repo, tree: Path) -> None:
    for rel in repo.config.get("env_files", []):
        src = repo.root / rel
        if src.is_file():
            dst = tree / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


# Git config that must never come from a worker: these make git run commands or load code.
SAFE_GIT = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false",
            "-c", "core.sshCommand=false", "-c", "diff.external=", "-c", "core.pager=cat"]


def _admin_file(path: Path) -> Path:
    # Sibling of the tree, outside every worker-writable root (the worker may only write inside `path`).
    return path.parent / f".{path.name}.gitdir"


def wt_git(path: Path, *args, check=True) -> str:
    """git on a worktree WITHOUT trusting anything inside it.

    The worktree's `.git` file is worker-writable; git discovery through it would let a worker point
    git at a repo/config it controls (core.fsmonitor etc. -> code execution in gp's unsandboxed
    process; security audit finding #1). Use the admin dir recorded at creation instead.
    """
    admin = _admin_file(path).read_text().strip()
    return git(path, *SAFE_GIT, f"--git-dir={admin}", f"--work-tree={path}", *args, check=check)


def make_tree(repo: Repo, base: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with _lock(repo):
        git(repo.root, *SAFE_GIT, "worktree", "add", "-q", "--detach", str(path), base)
    # Trusted moment: gp just created the gitfile. Record the admin dir before any worker runs.
    gitfile = (path / ".git").read_text().strip()
    if not gitfile.startswith("gitdir: "):
        raise GpError(f"unexpected worktree gitfile in {path}")
    admin = Path(gitfile[len("gitdir: "):])
    if not admin.is_absolute():
        admin = (path / admin).resolve()
    _admin_file(path).write_text(str(admin) + "\n")
    copy_env_files(repo, path)


def reset_tree(repo: Repo, base: str, path: Path) -> None:
    wt_git(path, "reset", "-q", "--hard", base)
    wt_git(path, "clean", "-ffdxq", "-e", ".git")
    copy_env_files(repo, path)


def remove_tree(repo: Repo, path: Path) -> None:
    # Delete the files ourselves, then prune: never let git run inside a worker-touched tree.
    if path.exists() or path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    _admin_file(path).unlink(missing_ok=True)
    with _lock(repo):
        git(repo.root, *SAFE_GIT, "worktree", "prune", check=False)


def freeze(path: Path, base: str) -> tuple[str, str]:
    """Commit the worker's tree (gp does this, outside any sandbox) and return (commit, patch)."""
    wt_git(path, "add", "-A", "--", ".", *JUNK_PATHSPEC)
    wt_git(path, *GIT_ID, "commit", "-q", "--allow-empty", "--no-verify", "-m", "glass-pipe frozen", check=False)
    head = wt_git(path, "rev-parse", "HEAD").strip()
    patch = wt_git(path, "diff", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv", base, head)
    return head, patch


def changed_since(path: Path, commit: str) -> list[str]:
    """Paths that differ from `commit` in the working tree (tracked or untracked, not ignored, not junk)."""
    out = []
    for line in wt_git(path, "status", "--porcelain=v1", "-uall", "-z").split("\0"):
        if len(line) > 3:
            p = line[3:]
            if not is_junk(p):
                out.append(p)
    return out


def base_sha(repo: Repo, base: str, rel: str) -> str | None:
    out = git(repo.root, "rev-parse", "-q", "--verify", f"{base}:{rel}", check=False).strip()
    return out or None


def drop_ref(repo: Repo, job_id: str) -> None:
    git(repo.root, "update-ref", "-d", f"refs/glass/{job_id}", check=False)
