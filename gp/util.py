"""Small shared helpers: subprocess, git, hashing, atomic files, locks."""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path


class GpError(Exception):
    """User-facing error; message is printed as-is."""


def now() -> str:
    # microseconds: ordering events within the same second matters (e.g. an edit right after a receipt)
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(ts: str) -> _dt.datetime:
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in ts else "%Y-%m-%dT%H:%M:%SZ"
    return _dt.datetime.strptime(ts, fmt).replace(tzinfo=_dt.timezone.utc)


def new_id() -> str:
    return secrets.token_hex(3)


def run(args, cwd=None, check=True, env=None, input=None, timeout=None) -> subprocess.CompletedProcess:
    p = subprocess.run(
        args, cwd=cwd, env=env, input=input, text=True, capture_output=True, timeout=timeout,
        stdin=None if input is not None else subprocess.DEVNULL,
    )
    if check and p.returncode != 0:
        raise GpError(f"command failed ({p.returncode}): {' '.join(map(str, args))}\n{p.stderr.strip()}")
    return p


def git(repo, *args, check=True, env=None, input=None) -> str:
    full_env = None
    if env:
        full_env = {**os.environ, **env}
    return run(["git", "-C", str(repo), *args], check=check, env=full_env, input=input).stdout


GIT_ID = ["-c", "user.name=glass-pipe", "-c", "user.email=gp@localhost", "-c", "commit.gpgsign=false"]


def blob_sha(path: Path) -> str | None:
    """Git blob sha of a file's bytes (no filters), or None if absent."""
    try:
        data = Path(path).read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def flock(path: Path, shared: bool = False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def append_jsonl(path: Path, obj) -> None:
    path = Path(path)
    with flock(path.with_name(path.name + ".lock")):
        with open(path, "a") as fh:
            fh.write(json.dumps(obj, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a bad line is skipped, never fatal
    except FileNotFoundError:
        pass
    return out


def tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])
