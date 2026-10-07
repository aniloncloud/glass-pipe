"""Regression tests for the pre-publication security audit."""
import subprocess

from tests.toykit import FIX, card, ids, ok_step


def test_worker_rewriting_worktree_gitfile_cannot_run_code_in_gp(toy, tmp_path):
    # Audit #1: the worktree's .git file is worker-writable; gp's own git calls must not follow it.
    evil = tmp_path / "evil"
    marker = tmp_path / "PWNED"
    subprocess.run(["git", "init", "-q", str(evil)], check=True)
    subprocess.run(["git", "-C", str(evil), "config", "core.fsmonitor", f"touch {marker}; false"], check=True)
    subprocess.run(["git", "-C", str(evil), "config", "core.hooksPath", str(tmp_path / "hooks")], check=True)
    toy.script([{"write": {"src/calc.py": FIX, ".git": f"gitdir: {evil / '.git'}\n"},
                 "result": {"status": "done", "summary": "fixed, and repointed .git"}}])
    (job,) = ids(toy.run(card()))
    st = toy.state(job)
    assert not marker.exists(), "gp executed worker-controlled git config"
    assert st["status"] == "ok" and [f["path"] for f in st["files"]] == ["src/calc.py"]


def test_symlink_or_mode_change_in_patch_is_tamper(toy):
    # Audit #6: a symlink replacing a scope file, or a +x mode flip, must not pass as ok.
    toy.script([{"write": {"src/calc.py": FIX}, "result": {"status": "done", "summary": "x"},
                 "chmod": {"src/calc.py": 0o755}}])
    (job,) = ids(toy.run(card()))
    assert toy.state(job)["status"] == "tamper" and "mode" in toy.state(job)["detail"]


def test_human_stamp_needs_a_tty(toy):
    import os, sys
    from tests.toykit import ROOT
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("GP_ALLOW_NONTTY_HUMAN", None)
    p = subprocess.run([sys.executable, "-m", "gp", "note", "--job", "ab", "--kind", "stamp", "--harness", "human"],
                       cwd=toy.root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert p.returncode == 1 and "interactive terminal" in p.stdout
    assert not (toy.root / ".glass" / "notes.jsonl").exists()
