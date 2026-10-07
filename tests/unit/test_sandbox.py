"""First Phase 1 test: the accept sandbox must not fail open (Phase 0 spike)."""
import os
import subprocess
import sys

import pytest

from gp import accept
from gp.util import GpError

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt profile is macOS-only")


@pytest.fixture
def tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    target = repo / "src" / "calc.py"
    target.write_text("x = 1\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    return repo, target, wt


def _try(code, cwd, writable):
    p = accept.sandboxed([sys.executable, "-c", code], cwd=cwd, writable=writable, cfg={})
    return p.returncode


@darwin
@pytest.mark.parametrize("how", ["relative", "dotdot", "realpath", "tmp_symlink", "new_file"])
def test_writes_outside_writable_roots_are_denied(tree, how, tmp_path):
    repo, target, wt = tree
    before = target.read_bytes()
    link = None
    if how == "relative":
        code, cwd = "open('src/calc.py','a').write('#x')", repo
    elif how == "dotdot":
        code, cwd = f"open('../{repo.name}/src/calc.py','a').write('#x')", wt
    elif how == "realpath":
        code, cwd = f"open({os.path.realpath(target)!r},'a').write('#x')", wt
    elif how == "tmp_symlink":
        link = f"/tmp/gp-test-alias-{os.getpid()}.py"
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(target, link)
        code, cwd = f"open({link!r},'a').write('#x')", wt
    else:
        code, cwd = "open('src/new.py','w').write('#x')", repo
    try:
        rc = _try(code, cwd, writable=[wt])
    finally:
        if link:
            os.unlink(link)
    assert rc != 0
    assert target.read_bytes() == before
    assert not (repo / "src" / "new.py").exists()


@darwin
def test_node_and_shell_redirect_denied(tree):
    repo, target, wt = tree
    before = target.read_bytes()
    for argv in (["sh", "-c", "echo x > src/calc.py"],) + (
        (["node", "-e", "require('fs').appendFileSync('src/calc.py','#x')"],) if _has("node") else ()
    ):
        p = accept.sandboxed(argv, cwd=repo, writable=[wt], cfg={})
        assert p.returncode != 0
    assert target.read_bytes() == before


@darwin
def test_writable_root_and_tmp_allowed(tree):
    repo, target, wt = tree
    assert _try("open('ok.txt','w').write('1')", wt, writable=[wt]) == 0
    assert (wt / "ok.txt").exists()
    assert _try("import tempfile;open(tempfile.gettempdir()+'/gp-ok','w').write('1')", wt, writable=[wt]) == 0


@darwin
def test_loopback_allowed_external_denied(tree):
    _, _, wt = tree
    loop = ("import socket;s=socket.socket();s.bind(('127.0.0.1',0));s.listen();"
            "socket.create_connection(('127.0.0.1',s.getsockname()[1]),timeout=2)")
    assert _try(loop, wt, writable=[wt]) == 0
    ext = "import socket;socket.create_connection(('1.1.1.1',443),timeout=3)"
    assert _try(ext, wt, writable=[wt]) != 0


def test_unsandboxed_is_refused_not_quiet(monkeypatch, tree):
    _, _, wt = tree
    monkeypatch.setattr(accept, "_platform", lambda: "linux")
    monkeypatch.setattr(accept, "_which", lambda name: None)
    with pytest.raises(accept.Unsandboxed):
        accept.sandboxed(["true"], cwd=wt, writable=[wt], cfg={})
    p = accept.sandboxed(["true"], cwd=wt, writable=[wt], cfg={"allow_unsandboxed": True})
    assert p.returncode == 0 and p.sandbox == "none"


def _has(name):
    return subprocess.run(["which", name], capture_output=True).returncode == 0


@darwin
def test_landing_profile_writes_only_inert_debris(tree):
    repo, target, wt = tree
    (repo / ".git").mkdir()
    (repo / ".glass").mkdir()
    rx = accept.debris_regexes(str(repo))
    run = lambda code: accept.sandboxed([sys.executable, "-c", code], cwd=repo, writable=[], cfg={},
                                        deny_write=[str(repo / ".git")], write_regex=rx).returncode
    assert run("import os;os.makedirs('.pytest_cache/v',exist_ok=True);open('.pytest_cache/v/x','w').write('1')") == 0
    assert run("open('.coverage','w').write('1')") == 0
    for code in ["open('src/calc.py','a').write('#x')",            # the code under test
                 "open('.git/config','a').write('x')",             # git config / hooks
                 "open('.glass/notes.jsonl','a').write('stamp')",  # forging a human stamp
                 "import os;os.makedirs('.venv/bin',exist_ok=True);open('.venv/bin/python','w')",
                 "import os;os.makedirs('src/__pycache__',exist_ok=True);open('src/__pycache__/calc.pyc','w')"]:
        assert run(code) != 0, code
    assert target.read_text() == "x = 1\n"


@darwin
def test_secret_dirs_read_denied(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE")
    monkeypatch.setenv("HOME", str(home))
    p = accept.sandboxed(["cat", str(home / ".ssh" / "id_ed25519")], cwd=tmp_path, writable=[tmp_path], cfg={})
    assert p.returncode != 0 and "PRIVATE" not in p.output
