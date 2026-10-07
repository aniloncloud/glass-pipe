"""OpenCode worker: a private, password-protected, sandboxed `opencode serve` per job.

Why (Phase 0/2 spikes + pre-publication security audit):
- The user's global OpenCode config carries plugins and MCP servers with live credentials;
  gp's server gets its own config dir (XDG_CONFIG_HOME): no MCP, no plugins, model pinned.
- Tools run inside the server process, so the server runs under a write-allowlist sandbox. Writable:
  this job's worktree, OpenCode's session data/state dirs (sessions + db, needed to resolve models),
  a private tmp, /dev. NOT writable: the main repo, other jobs, gp's server config/profile, and
  ~/.cache/opencode (plugin/npm code that would run on the next start). Secret dirs are read-denied.
- One server per job (not per repo): a worker cannot tamper with another job or a shared server.
- The environment is an allowlist: API keys and tokens in the user's env never reach the worker.
- Interactive/web/runtime tools are denied: question, webfetch, websearch, execute, subagent, skill.
"""
from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from .. import accept
from .base import Worker, WorkerResult, parse_result_block

DENY_TOOLS = ["question", "webfetch", "websearch", "execute", "subagent", "skill"]
ASK_DENY = ["edit", "write", "shell", "bash", "patch", "multiedit"]
# OpenCode keeps sessions/db (and the provider state it needs to resolve models) here.
WRITABLE_DATA = ["~/.local/share/opencode", "~/.local/state/opencode"]
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ")
QUOTA_WORDS = ("quota", "rate limit", "rate_limit", "ratelimit", "429", "insufficient", "credit", "usage limit",
               "exceeded", "billing")
AUTH_WORDS = ("auth", "unauthorized", "401", "403", "api key", "apikey", "forbidden", "login")


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def server_config(model: str, deny: list[str]) -> dict:
    return {"$schema": "https://opencode.ai/config.schema.json", "model": model, "small_model": model,
            "plugin": [], "mcp": {},
            "permissions": [{"action": t, "resource": "*", "effect": "deny"} for t in deny]}


def server_profile(writable: list[str]) -> str:
    allow = " ".join(f"(subpath {accept._sb_str(_real(w))})" for w in writable)
    return ("(version 1)\n(allow default)\n(deny file-write*)\n"
            f'(allow file-write* {allow} (subpath "/dev"))\n' + accept.secret_read_denies())


def minimal_env(extra: dict) -> dict:
    return {**{k: os.environ[k] for k in ENV_ALLOW if k in os.environ}, **extra}


_RUNNING: list["OpenCodeServer"] = []


@atexit.register
def _stop_all():
    for srv in list(_RUNNING):
        srv.stop()


class OpenCodeServer:
    """A private server for one job (mode "work", writable worktree) or one `gp ask` ("ask", read-only).

    Its home (config, sandbox profile, log) sits next to the worktree, outside every writable root.
    """

    def __init__(self, repo, wcfg: dict, home: Path, tree: Path | None = None, mode: str = "work"):
        self.repo, self.wcfg, self.home, self.tree, self.mode = repo, wcfg, Path(home), tree, mode
        self.port = self.password = self.proc = None

    @property
    def model(self) -> str:
        return self.wcfg.get("model", "opencode-go/deepseek-v4.1-flash")

    def _config(self) -> dict:
        deny = list(self.wcfg.get("deny_tools", DENY_TOOLS))
        if self.mode == "ask":
            deny += [t for t in ASK_DENY if t not in deny]
        return server_config(self.model, deny)

    def writable(self) -> list[str]:
        roots = ([str(self.tree)] if self.tree and self.mode == "work" else []) + WRITABLE_DATA + [str(self.home / "tmp")]
        return roots + list(self.wcfg.get("server_writable", []))

    def start(self) -> "OpenCodeServer":
        if self.proc and self.proc.poll() is None:
            return self
        cfg_dir = self.home / "config" / "opencode"
        if (self.home / "config").exists():
            shutil.rmtree(self.home / "config")  # always start from gp's config, never a leftover
        cfg_dir.mkdir(parents=True)
        (self.home / "tmp").mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        cfg_file = cfg_dir / "opencode.json"
        cfg_file.write_text(json.dumps(self._config(), indent=1))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.password = secrets.token_hex(16)
        env = minimal_env({"XDG_CONFIG_HOME": str(self.home / "config"), "OPENCODE_CONFIG": str(cfg_file),
                           "OPENCODE_SERVER_PASSWORD": self.password, "TMPDIR": _real(self.home / "tmp"),
                           "PWD": str(self.home)})
        argv = ["opencode", "serve", "--port", str(self.port), "--hostname", "127.0.0.1"]
        plat = accept._platform()
        if plat == "darwin" and accept._which("sandbox-exec"):
            prof = self.home / "server.sb"
            prof.write_text(server_profile(self.writable()))
            argv = ["sandbox-exec", "-f", str(prof), *argv]
        elif plat.startswith("linux") and accept._which("bwrap"):
            full = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--die-with-parent"]
            for w in self.writable():
                Path(_real(w)).mkdir(parents=True, exist_ok=True)
                full += ["--bind", _real(w), _real(w)]
            for d in accept.SECRET_DIRS:
                if os.path.isdir(_real(d)):
                    full += ["--tmpfs", _real(d)]
            argv = full + argv
        elif not self.repo.config.get("allow_unsandboxed"):
            raise accept.Unsandboxed("no sandbox for the OpenCode worker server (need sandbox-exec or bwrap); "
                                     "set allow_unsandboxed = true to run it unconfined")
        log = open(self.home / "server.log", "a")
        self.proc = subprocess.Popen(argv, cwd=self.home, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        _RUNNING.append(self)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"opencode serve exited early (rc={self.proc.returncode}); see {self.home / 'server.log'}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return self
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("opencode serve did not start listening within 30s")

    def client_env(self, cwd: Path) -> dict:
        return minimal_env({"OPENCODE_PASSWORD": self.password, "PWD": str(cwd)})

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 15)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, 9)
                except ProcessLookupError:
                    pass
        if self in _RUNNING:
            _RUNNING.remove(self)


class OpenCodeWorker(Worker):
    _servers: dict = {}

    def argv(self, brief_path, cwd):
        cwd = Path(cwd)
        srv = OpenCodeWorker._servers.get(str(cwd))
        if srv is None:  # one server per job, reused across that job's attempts, stopped at exit
            srv = OpenCodeServer(self.repo, self.cfg, home=cwd.parent / "server", tree=cwd)
            OpenCodeWorker._servers[str(cwd)] = srv
        srv.start()
        brief = Path(brief_path).read_text()
        argv = ["opencode", "run", "--server", f"http://127.0.0.1:{srv.port}", "--format", "json", "--auto",
                "-m", srv.model, "--title", f"gp {Path(brief_path).parent.name}", "--", brief]
        return argv, srv.client_env(cwd)

    def classify(self, rc, events):
        session = next((e.get("sessionID") for e in events if e.get("sessionID")), None)
        usage = {"cost": 0.0, "input": 0, "output": 0}
        for e in events:
            if e.get("type") == "step_finish":
                p = e.get("part") or {}
                usage["cost"] += float(p.get("cost") or 0)
                t = p.get("tokens") or {}
                usage["input"] += int(t.get("input") or 0)
                usage["output"] += int(t.get("output") or 0)
        for e in events:
            if e.get("type") == "error":
                err = e.get("error") or {}
                kind = str(err.get("type", "")).lower()
                msg = f"{kind} {err.get('message', '')} {json.dumps(err)}".lower()
                if kind == "provider.no-route" or "no-route" in msg or "model unavailable" in msg:
                    status = "unavailable"
                elif any(w in msg for w in QUOTA_WORDS):
                    status = "quota"
                elif any(w in msg for w in AUTH_WORDS):
                    status = "auth"
                else:
                    status = "crash"
                return WorkerResult(status, session, detail=str(err)[:300], usage=usage)
        if rc != 0 and not events:
            return WorkerResult("unavailable", session, detail=f"opencode run exited {rc} with no events", usage=usage)
        text = "\n".join((e.get("part") or {}).get("text", "") for e in events if e.get("type") == "text")
        res = parse_result_block(text)
        if res is None:
            return WorkerResult("no-result", session, detail="no <<<GP-RESULT block", usage=usage)
        return WorkerResult("done", session, res["summary"], res["glass"], usage=usage)
