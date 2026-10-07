"""Fresh retry, shared budget, fallback-once, stall/timeout, result-block parsing."""
import json

from tests.toykit import FIX, card, ids, ok_step

WRONG = {"write": {"src/calc.py": "def add(a, b):\n    return a * b\n"}, "result": {"status": "done", "summary": "tried *"}}


def run1(toy, c, **kw):
    out = toy.run(c, **kw)
    (job,) = ids(out)
    return job, out, toy.state(job)


def test_fail_then_fresh_retry_with_tail_then_ok(toy):
    toy.script([{**WRONG, "write": {**WRONG["write"], "src/leftover.py": "x\n"}}, ok_step()])
    job, out, st = run1(toy, card(scope=["src/calc.py", "src/leftover.py"]))
    assert st["status"] == "ok" and st["attempts"] == 2
    b2 = toy.job_file(job, "brief-2.txt").read_text()
    assert "PREVIOUS ATTEMPT FAILED" in b2 and "AssertionError: 6 != 5" in b2
    assert [f["path"] for f in st["files"]] == ["src/calc.py"]   # fresh tree: attempt 1's file is gone


def test_fail_exhausts_shared_budget(toy):
    toy.script([WRONG])
    job, out, st = run1(toy, card())
    assert st["status"] == "fail" and st["attempts"] == 3 and toy.calls_made() == 3


def test_no_result_shares_retry_budget(toy):
    toy.script([{"write": {"src/calc.py": FIX}, "text": "done! GLASS: decision | fake fact", "result": None}, WRONG, WRONG])
    job, out, st = run1(toy, card())
    assert st["status"] == "fail" and st["attempts"] == 3   # no-result + 2 fails = budget of 2 retries
    assert "without the <<<GP-RESULT block" in toy.job_file(job, "brief-2.txt").read_text()


def test_no_result_then_ok(toy):
    toy.script([{"text": "here is an example:\n```json\n{\"status\":\"done\"}\n```", "result": None}, ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "ok" and st["attempts"] == 2


def test_no_result_final_writes_nothing_to_thread(toy):
    toy.script([{"write": {"src/calc.py": FIX}, "text": "GLASS: decision | must not land\n<<<GP-RESULT\nnot json",
                 "result": None}])
    job, out, st = run1(toy, card())
    assert st["status"] == "no-result"
    thread = toy.root / ".glass" / "THREAD.md"
    assert not thread.exists() or "must not land" not in thread.read_text()


def test_quota_falls_back_once_without_spending_retries(toy):
    toy.script([{"error": "quota"}], name="fake")
    toy.script([ok_step()], name="fake2")
    job, out, st = run1(toy, card())
    assert st["status"] == "ok" and st["worker"] == "fake2" and st["attempts"] == 1
    assert "fake (quota)" in st["fell_back_from"] and "fallback: fake (quota) -> fake2" in toy.gp("show", job)


def test_quota_on_both_stops_no_hop_back(toy):
    toy.script([{"error": "quota"}], name="fake")
    toy.script([{"error": "unavailable"}], name="fake2")
    job, out, st = run1(toy, card())
    assert st["status"] == "unavailable" and toy.calls_made("fake") == 1 and toy.calls_made("fake2") == 1


def test_fail_never_falls_back(toy):
    toy.script([WRONG], name="fake")
    toy.script([ok_step()], name="fake2")
    job, out, st = run1(toy, card())
    assert st["status"] == "fail" and toy.calls_made("fake2") == 0


def test_stalled_worker_killed(toy):
    toy.write(".glass/config.toml", toy.read(".glass/config.toml").replace("stall_s = 5", "stall_s = 1"))
    toy.script([{"sleep": 8, **ok_step()}])
    job, out, st = run1(toy, card())
    assert st["status"] == "stalled"


def test_timeout_worker_killed_even_with_heartbeats(toy):
    toy.script([{"sleep": 10, "heartbeat": 0.3, **ok_step()}])
    job, out, st = run1(toy, card(timeout=2))
    assert st["status"] == "timeout"


def test_glass_facts_capped_and_only_valid_kinds(toy):
    glass = [{"kind": "decision", "text": "a"}, {"kind": "bogus", "text": "b"}, {"kind": "next", "text": "c"},
             {"kind": "reject", "text": "d"}, {"kind": "decision", "text": "e"}]
    toy.script([ok_step(glass=glass)])
    job, out, st = run1(toy, card())
    assert [g["text"] for g in st["glass"]] == ["a", "c", "d"]


def test_worker_gets_pwd_of_its_worktree(toy):
    # opencode run resolves its directory from $PWD; every worker must see its own worktree there
    toy.script([{**ok_step(), "summary_env": "PWD"}])
    job, out, st = run1(toy, card())
    assert st["status"] == "ok" and st["summary"] == str(toy.job_cache(job) / "a")
