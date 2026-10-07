"""Hook entry (`gp _hook <event>`) and `gp init`, driven through the CLI like Claude Code would."""
import json
import os
import subprocess
import sys

from tests.toykit import ROOT, card, ids, ok_step


def hook(toy, event, payload):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "GP_CACHE_DIR": str(toy.cache)}
    p = subprocess.run([sys.executable, "-m", "gp", "_hook", event], cwd=toy.root, input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0
    return json.loads(p.stdout) if p.stdout.strip() else None


def test_session_start_silent_without_news_then_reports_ready_job(toy):
    assert hook(toy, "SessionStart", {"cwd": str(toy.root)}) is None
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    out = hook(toy, "SessionStart", {"cwd": str(toy.root)})
    assert job in out["hookSpecificOutput"]["additionalContext"] and "await `gp apply`" in out["hookSpecificOutput"]["additionalContext"]
    toy.gp("apply", job)
    assert hook(toy, "SessionStart", {"cwd": str(toy.root)}) is None


def test_edit_log_feeds_rescued(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    hook(toy, "PostToolUse", {"cwd": str(toy.root), "tool_name": "Edit", "session_id": "s",
                              "tool_input": {"file_path": str(toy.root / "src" / "calc.py"), "old_string": "a", "new_string": "b"}})
    rows = [json.loads(l) for l in (toy.root / ".glass" / "edits.jsonl").read_text().splitlines()]
    assert rows[-1]["path"] == "src/calc.py" and rows[-1]["lines"] == 1
    assert "not-rescued    0/1" in toy.gp("stats")


def test_edit_log_ignores_glass_and_non_edit_tools(toy):
    hook(toy, "PostToolUse", {"cwd": str(toy.root), "tool_name": "Write",
                              "tool_input": {"file_path": str(toy.root / ".glass" / "cards" / "x.jsonl"), "content": "x"}})
    hook(toy, "PostToolUse", {"cwd": str(toy.root), "tool_name": "Read", "tool_input": {"file_path": "src/calc.py"}})
    assert not (toy.root / ".glass" / "edits.jsonl").exists()


def test_self_stamp_denied_even_in_advisory(toy):
    out = hook(toy, "PreToolUse", {"cwd": str(toy.root), "tool_name": "Bash",
                                   "tool_input": {"command": "gp note --job ab --kind stamp --harness human"}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "do not retry" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_auto_allow_is_opt_in(toy):
    payload = {"cwd": str(toy.root), "tool_name": "Bash", "tool_input": {"command": "gp run <<'EOF'\n{}\nEOF"}}
    assert hook(toy, "PreToolUse", payload) is None
    toy.config("auto_allow_gp = true\n")
    out = hook(toy, "PreToolUse", payload)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    payload["tool_input"]["command"] = "gp run <<'EOF'\n{}\nEOF\nrm -rf src"
    assert hook(toy, "PreToolUse", payload) is None


def test_hook_silent_outside_glass_repo(tmp_path):
    p = subprocess.run([sys.executable, "-m", "gp", "_hook", "SessionStart"], cwd=tmp_path, input="{}",
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert p.returncode == 0 and p.stdout == ""


def test_hook_survives_garbage_stdin(toy):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    p = subprocess.run([sys.executable, "-m", "gp", "_hook", "PostToolUse"], cwd=toy.root, input="not json",
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0


def test_init_writes_config_gitignore_and_prints_next_steps(toy):
    (toy.root / ".glass" / "config.toml").unlink()
    out = toy.gp("init")
    assert "wrote .glass/config.toml" in out and "ln -s" in out and "--plugin-dir" in out
    assert ".glass/" in toy.read(".gitignore").splitlines()
    assert 'model = "opencode-go/deepseek-v4.1-flash"' in toy.read(".glass/config.toml")
    assert not (toy.root / ".claude").exists()               # nothing touches Claude settings unasked
    out = toy.gp("init", "--write-settings")
    st = json.loads(toy.read(".claude/settings.local.json"))
    assert "Bash(gp run *)" in st["permissions"]["allow"] and "kept existing" in out
    assert not any("note" in r for r in st["permissions"]["allow"])
