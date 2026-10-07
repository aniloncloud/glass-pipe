"""Shared test kit: temp git repos + FakeWorker, driving the real `python -m gp` CLI."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUG = "def add(a, b):\n    return a - b\n"
FIX = "def add(a, b):\n    return a + b\n"
TEST = ("import unittest\nfrom src.calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
ACCEPT = "python3 -m unittest tests.test_calc"


class Toy:
    def __init__(self, root: Path, cache: Path):
        self.root, self.cache = root, cache
        self.scripts = root.parent / "scripts"
        self.scripts.mkdir(exist_ok=True)

    # --- files / git
    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def read(self, rel):
        return (self.root / rel).read_text()

    def git(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True,
                              env={**os.environ, **(env or {})}).stdout

    def commit(self, msg="c", later_s=0):
        """later_s: date the commit that many seconds in the future (time-sensitive scorecard tests)."""
        env = None
        if later_s:
            import time
            stamp = f"{int(time.time()) + later_s} +0000"
            env = {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg, env=env)

    # --- config / workers
    def config(self, extra="", tables=""):
        """`extra` = top-level keys (must precede tables in TOML); `tables` = extra [sections]."""
        lines = [extra.rstrip(),
            "stall_s = 5", "job_timeout_s = 30", "retries = 2",
            "[workers.fake]", 'type = "fake"', f'script = "{self.scripts / "fake.json"}"',
            "[workers.fake2]", 'type = "fake"', f'script = "{self.scripts / "fake2.json"}"',
            "[fallback]", 'fake = "fake2"', 'fake2 = "fake"',
        ]
        self.write(".glass/config.toml", "\n".join(lines) + "\n" + tables)

    def script(self, calls, name="fake"):
        (self.scripts / f"{name}.json").write_text(json.dumps({"calls": calls}))

    def calls_made(self, name="fake"):
        c = self.scripts / f"{name}.json.{name}.count"
        return int(c.read_text()) if c.exists() else 0

    # --- gp
    def gp(self, *args, input=None, deadline=60, check_rc=None):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "GP_CACHE_DIR": str(self.cache), "GP_DEADLINE_S": str(deadline),
               "GP_ALLOW_NONTTY_HUMAN": "1"}  # tests play the human; real `gp note --harness human` needs a TTY
        p = subprocess.run([sys.executable, "-m", "gp", *args], cwd=self.root, input=input, text=True,
                           capture_output=True, env=env, timeout=max(180, deadline + 120))
        if check_rc is not None:
            assert p.returncode == check_rc, p.stdout + p.stderr
        assert not p.stderr.strip() or "Traceback" not in p.stderr, p.stderr
        return p.stdout

    def run(self, *cards, **kw):
        text = "\n".join(json.dumps(c) for c in cards)
        return self.gp("run", input=text, **kw)

    def job_cache(self, job):
        """The job's tree dir: <GP_CACHE_DIR>/<repohash>/<job>."""
        (hashdir,) = [d for d in self.cache.iterdir() if d.is_dir()]
        return hashdir / job

    def state(self, job):
        return json.loads((self.root / ".glass" / "jobs" / job / "state.json").read_text())

    def job_file(self, job, name):
        return (self.root / ".glass" / "jobs" / job / name)


def card(**kw):
    d = {"to": "fake", "goal": "make tests/test_calc.py::T::test_add pass", "accept": ACCEPT, "scope": ["src/calc.py"]}
    d.update(kw)
    return d


def ids(out):
    return re.findall(r"gp#([0-9a-f]{6})", out)


def ok_step(fix=FIX, glass=None, path="src/calc.py"):
    return {"write": {path: fix}, "result": {"status": "done", "summary": "fixed add", "glass": glass or []}}


def make_toy(tmp_path) -> "Toy":
    root = tmp_path / "toy"
    root.mkdir()
    t = Toy(root, tmp_path / "cache")
    t.git("init", "-q")
    t.write(".gitignore", "__pycache__/\n*.local\n")
    t.write("src/__init__.py", "")
    t.write("src/calc.py", BUG)
    t.write("tests/__init__.py", "")
    t.write("tests/test_calc.py", TEST)
    t.commit("init")
    return t
