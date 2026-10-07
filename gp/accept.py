"""Accept commands: allowlist parsing, sandboxed execution, and red/green/cannot-run.

The sandbox is an allowlist of writable roots (Phase 0 spike): deny all writes, then allow
the resolved worktree, tmp, and /dev. Aliases (symlinks, '..') cannot reach the repo
because only already-writable roots are ever allowed. Network: loopback only.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .util import GpError

META = set(";&|`$<>()\n\\")

DEFAULT_ALLOW = [
    ["pytest"],
    ["python", "-m", "pytest"], ["python3", "-m", "pytest"],
    ["python", "-m", "unittest"], ["python3", "-m", "unittest"],
    ["npm", "test"], ["bun", "test"], ["pnpm", "test"], ["yarn", "test"],
    ["npx", "vitest"], ["npx", "jest"], ["npx", "tsc"], ["npx", "eslint"],
    ["cargo", "test"], ["go", "test"],
    ["tsc"], ["eslint"], ["ruff"], ["mypy"],
]

OUTPUT_CAP = 64_000

# Read-denied in every sandbox (defense in depth; workers and test code can still read other
# user-readable files, see SECURITY.md). Missing paths are harmless in seatbelt rules.
SECRET_DIRS = ["~/.ssh", "~/.aws", "~/.azure", "~/.config/gcloud", "~/.config/gh", "~/.docker", "~/.kube",
               "~/.gnupg", "~/.codex", "~/.config/opencode"]
SECRET_FILES = ["~/.netrc", "~/.git-credentials", "~/.local/share/opencode/mcp-auth.json"]


def secret_read_denies() -> str:
    dirs = " ".join(f"(subpath {_sb_str(os.path.realpath(os.path.expanduser(d)))})" for d in SECRET_DIRS)
    files = " ".join(f"(literal {_sb_str(os.path.realpath(os.path.expanduser(f)))})" for f in SECRET_FILES)
    return f"(deny file-read* {dirs} {files})\n"


class AcceptRejected(GpError):
    pass


class Unsandboxed(GpError):
    pass


def _prefixes(extra_allow) -> list[list[str]]:
    return DEFAULT_ALLOW + [list(p) for p in (extra_allow or [])]


def _match(argv: list[str], extra_allow=None) -> int:
    best = 0
    for pre in _prefixes(extra_allow):
        if argv[: len(pre)] == pre and len(pre) > best:
            best = len(pre)
    return best


def parse_accept(cmd: str, extra_allow=None) -> list[str]:
    if not cmd or not cmd.strip():
        raise AcceptRejected("accept command is empty")
    bad = sorted({c for c in cmd if c in META})
    if bad:
        raise AcceptRejected(f"accept command has shell metacharacters {bad!r}; give a single runner command")
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        raise AcceptRejected(f"accept command does not parse: {e}")
    if not _match(argv, extra_allow):
        raise AcceptRejected(
            f"accept command {argv[0]!r} is not an allowlisted test runner "
            "(pytest, python -m pytest|unittest, npm|bun|pnpm|yarn test, cargo test, go test, tsc, eslint, ruff, mypy; "
            "extend with accept_allow in .glass/config.toml)")
    return argv


def runner_args(argv: list[str], extra_allow=None) -> list[str]:
    return argv[_match(argv, extra_allow):]


_MOD_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def cannot_run(rc: int, output: str, root: Path) -> bool:
    """True when the accept command could not run at all (env), as opposed to running red."""
    if rc in (126, 127):
        return True
    if rc == 0:
        return False
    if "cannot import name" in output:
        return False  # test-first: the name the card asks for does not exist yet -> red
    m = _MOD_RE.search(output)
    if m:
        top = m.group(1).split(".")[0]
        in_repo = (Path(root) / top).exists() or (Path(root) / f"{top}.py").exists()
        return not in_repo
    if "Ran 0 tests" in output or "NO TESTS RAN" in output:
        return True
    if rc in (3, 4, 5):  # pytest internal error / usage error / nothing collected
        return True
    return False


# --- sandbox ---------------------------------------------------------------

def _platform() -> str:
    return sys.platform


def _which(name):
    return shutil.which(name)


def _sb_str(p: str) -> str:
    return '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _roots(cwd, writable, cfg, private_tmp) -> list[str]:
    # Only the private per-run TMPDIR is writable, never the shared system tmp: a repo that
    # happens to live under /tmp or /var/folders must not become writable (found by test_sandbox).
    roots = [os.path.realpath(w) for w in writable] + [os.path.realpath(private_tmp)]
    roots += [os.path.realpath(os.path.expanduser(w)) for w in cfg.get("accept_writable", [])]
    seen, out = set(), []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# Only inert cache/report data. NOT .glass (stamps live there), NOT .venv/node_modules/*.pyc
# (code that runs later). Python bytecode is disabled for landing runs instead.
LANDING_DEBRIS = [".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis", "coverage", ".nyc_output", "htmlcov"]


def debris_regexes(root: str) -> list[str]:
    """Seatbelt regexes for test debris under `root` (the only repo writes a landing check may make)."""
    import re as _re
    r = _re.escape(os.path.realpath(root))
    dirs = "|".join(_re.escape(d) for d in LANDING_DEBRIS)
    return [f"^{r}/(.*/)?({dirs})(/.*)?$", f"^{r}/(.*/)?\\.coverage[^/]*$"]


def seatbelt_profile(roots: list[str], deny_write: list[str] = (), write_regex: list[str] = ()) -> str:
    allow = " ".join(f"(subpath {_sb_str(r)})" for r in roots)
    allow += "".join(f' (regex #"{rx}")' for rx in write_regex)
    out = (
        "(version 1)\n(allow default)\n"
        "(deny network*)\n"
        '(allow network* (remote ip "localhost:*"))\n'
        '(allow network-bind (local ip "localhost:*"))\n'
        '(allow network-inbound (local ip "localhost:*"))\n'
        "(deny file-write*)\n"
        f'(allow file-write* {allow} (subpath "/dev"))\n'
    )
    if deny_write:  # later rules win in seatbelt: carve these out of the writable roots
        out += "(deny file-write* " + " ".join(f"(subpath {_sb_str(os.path.realpath(d))})" for d in deny_write) + ")\n"
    return out + secret_read_denies()


@dataclass
class SandboxResult:
    returncode: int
    output: str
    sandbox: str


def sandboxed(argv, cwd, writable, cfg, timeout=None, deny_write=(), write_regex=()) -> SandboxResult:
    """Run argv with writes confined to `writable` roots (+tmp). Raises Unsandboxed if no sandbox."""
    private_tmp = tempfile.mkdtemp(prefix="gp-tmp-")
    env = {**os.environ, "TMPDIR": private_tmp, "TMP": private_tmp, "TEMP": private_tmp}
    if write_regex:  # landing check: no bytecode written into the user's tree
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    roots = _roots(cwd, writable, cfg, private_tmp)
    plat = _platform()
    profile_file = None
    if plat == "darwin" and _which("sandbox-exec"):
        fd, profile_file = tempfile.mkstemp(prefix="gp-sb-", suffix=".sb")
        with os.fdopen(fd, "w") as fh:
            fh.write(seatbelt_profile(roots, deny_write, write_regex))
        full, kind = ["sandbox-exec", "-f", profile_file, *argv], "seatbelt"
    elif plat.startswith("linux") and _which("bwrap"):
        if write_regex:  # bwrap has no regex binds: fall back to the whole root writable minus deny_write
            roots = roots + [os.path.realpath(str(cwd))]
        full = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--unshare-net", "--die-with-parent"]
        for r in roots:
            if os.path.isdir(r):
                full += ["--bind", r, r]
        for d in deny_write:
            if os.path.isdir(d):
                full += ["--ro-bind", d, d]
        for d in SECRET_DIRS:
            d = os.path.realpath(os.path.expanduser(d))
            if os.path.isdir(d):
                full += ["--tmpfs", d]
        full += ["--chdir", str(cwd), *argv]
        kind = "bwrap"
    else:
        if not cfg.get("allow_unsandboxed"):
            shutil.rmtree(private_tmp, ignore_errors=True)
            raise Unsandboxed("no sandbox available (need sandbox-exec or bwrap); "
                              "set allow_unsandboxed = true to run accept commands unconfined")
        full, kind = list(argv), "none"
    try:
        p = subprocess.run(full, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=timeout)
        rc, out = p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        rc, out = 124, out + "\n[gp: accept command timed out]"
    except FileNotFoundError as e:
        rc, out = 127, f"[gp: {e}]"
    finally:
        if profile_file:
            os.unlink(profile_file)
        shutil.rmtree(private_tmp, ignore_errors=True)
    return SandboxResult(rc, out[-OUTPUT_CAP:], kind)


@dataclass
class AcceptRun:
    rc: int
    output: str
    cannot_run: bool
    green: bool
    sandbox: str


def run_accept(argv, cwd, writable, cfg, deny_write=(), write_regex=()) -> AcceptRun:
    r = sandboxed(argv, cwd=cwd, writable=writable, cfg=cfg, timeout=cfg.get("accept_timeout_s", 600),
                  deny_write=deny_write, write_regex=write_regex)
    cr = cannot_run(r.returncode, r.output, Path(cwd))
    return AcceptRun(r.returncode, r.output, cr, r.returncode == 0 and not cr, r.sandbox)
