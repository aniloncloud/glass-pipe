import json
import subprocess
import sys
from pathlib import Path

import pytest

from gp.workers import opencode
from gp.workers.opencode import OpenCodeWorker

SPIKE = Path(__file__).resolve().parents[1] / "fixtures" / "opencode-events.jsonl"  # recorded real run


def events(*objs):
    return [json.loads(json.dumps(o)) for o in objs]


def worker():
    return OpenCodeWorker("opencode", {"type": "opencode", "model": "opencode-go/deepseek-v4.1-flash"}, repo=None)


def test_server_config_has_no_mcp_no_plugins_and_denies_risky_tools():
    c = opencode.server_config("m", opencode.DENY_TOOLS)
    assert c["mcp"] == {} and c["plugin"] == [] and c["model"] == "m"
    denied = {p["action"] for p in c["permissions"] if p["effect"] == "deny"}
    assert {"question", "webfetch", "websearch", "execute", "subagent", "skill"} <= denied


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt")
def test_server_profile_denies_nested_writes_to_main_repo(tmp_path):
    main, cache = tmp_path / "main", tmp_path / "cache"
    (main / "src").mkdir(parents=True)
    cache.mkdir()
    target = main / "src" / "calc.py"
    target.write_text("x\n")
    prof = tmp_path / "s.sb"
    prof.write_text(opencode.server_profile([str(cache)]))
    # tools run as children of the server: a nested shell must inherit the deny
    r = subprocess.run(["sandbox-exec", "-f", str(prof), "sh", "-c", f"sh -c 'echo PWNED >> {target}'"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and target.read_text() == "x\n"
    r = subprocess.run(["sandbox-exec", "-f", str(prof), "sh", "-c", f"echo ok > {cache}/probe"])
    assert r.returncode == 0


def test_classify_real_spike_events_without_marker_is_no_result():
    evs = [json.loads(l) for l in SPIKE.read_text().splitlines() if l.strip()]
    r = worker().classify(0, evs)
    assert r.status == "no-result" and r.session.startswith("ses_") and r.usage["input"] > 0 and r.usage["cost"] > 0


def test_classify_done_with_marker_and_usage():
    evs = events({"type": "step_start", "sessionID": "ses_1"},
                 {"type": "text", "sessionID": "ses_1", "part": {"text": 'fixed\n<<<GP-RESULT\n{"status":"done","summary":"s","glass":[{"kind":"decision","text":"d"}]}'}},
                 {"type": "step_finish", "sessionID": "ses_1", "part": {"cost": 0.002, "tokens": {"input": 100, "output": 20}}})
    r = worker().classify(0, evs)
    assert (r.status, r.summary, r.glass) == ("done", "s", [{"kind": "decision", "text": "d"}])
    assert r.usage == {"cost": 0.002, "input": 100, "output": 20}


@pytest.mark.parametrize("err,status", [
    ({"type": "provider.no-route", "message": "Model unavailable: x"}, "unavailable"),
    ({"type": "provider.error", "message": "429 rate limit exceeded"}, "quota"),
    ({"type": "provider.error", "message": "Insufficient credits"}, "quota"),
    ({"type": "provider.error", "message": "401 Unauthorized"}, "auth"),
    ({"type": "weird", "message": "segfault"}, "crash"),
])
def test_classify_errors(err, status):
    assert worker().classify(1, events({"type": "error", "sessionID": "s", "error": err})).status == status


def test_no_events_nonzero_exit_is_unavailable():
    assert worker().classify(1, []).status == "unavailable"


def test_per_job_server_writable_roots_exclude_code_cache_config_and_other_jobs(tmp_path):
    from gp.workers.opencode import OpenCodeServer, minimal_env
    import os as _os
    tree = tmp_path / "cache" / "job1" / "a"
    srv = OpenCodeServer(repo=None, wcfg={}, home=tree.parent / "server", tree=tree)
    roots = [_os.path.realpath(_os.path.expanduser(r)) for r in srv.writable()]
    assert _os.path.realpath(str(tree)) in roots
    assert _os.path.realpath(_os.path.expanduser("~/.cache/opencode")) not in roots      # plugin/npm code
    assert not any(r == _os.path.realpath(str(tree.parent / "server")) for r in roots)   # gp's config/profile
    assert not any(r == _os.path.realpath(str(tmp_path / "cache")) for r in roots)       # other jobs
    ask = OpenCodeServer(repo=None, wcfg={}, home=tmp_path / "ask", mode="ask")
    assert str(tree) not in ask.writable()


def test_minimal_env_drops_secrets(monkeypatch):
    from gp.workers.opencode import minimal_env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    env = minimal_env({"X": "1"})
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env and env["X"] == "1" and "PATH" in env
