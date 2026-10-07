"""Three ways to steer a worker: card notes, `gp retry --hint`, and binding constraints."""
from tests.toykit import FIX, card, ids, ok_step

WRONG = {"write": {"src/calc.py": "def add(a, b):\n    return a * b\n"}, "result": {"status": "done", "summary": "x"}}


def test_card_notes_reach_the_brief(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card(notes=["Keep add() pure; no logging.", "Match the style of src/__init__.py."])))
    brief = toy.job_file(job, "brief-1.txt").read_text()
    assert "NOTES FROM THE PLANNER" in brief and "Keep add() pure; no logging." in brief
    assert brief.index("RULES") < brief.index("NOTES FROM THE PLANNER")   # notes never outrank the rules


def test_notes_are_bounded(toy):
    out = toy.run(card(notes=["x" * 501]))
    assert "rejected" in out and "notes" in out


def test_retry_with_hint_runs_fresh_and_keeps_card_stable(toy):
    toy.config("retries = 0\n")
    toy.write(".glass/config.toml", toy.read(".glass/config.toml").replace("retries = 2", ""))
    toy.script([WRONG, ok_step()])
    (j1,) = ids(toy.run(card()))
    assert toy.state(j1)["status"] == "fail"
    out = toy.gp("retry", j1, "--hint", "add means a + b, not a * b")
    (j2,) = ids(out)
    assert j2 != j1 and toy.state(j2)["status"] == "ok"
    brief = toy.job_file(j2, "brief-1.txt").read_text()
    assert "Hint after a previous attempt: add means a + b, not a * b" in brief
    assert '"revises": "' + j1 in (toy.root / ".glass" / "ledger.jsonl").read_text()
    toy.gp("apply", j2)
    sc = toy.gp("stats")
    assert "cards submitted: 2" in sc and "card-stable    2/2" in sc   # a hint is not a goal change


def test_claude_constraint_binds_future_briefs_but_cannot_stamp(toy):
    out = toy.gp("note", "--kind", "constraint", "--harness", "claude", "--text", "Never import numpy in src/")
    assert "recorded" in out
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    assert "constraint: Never import numpy in src/" in toy.job_file(job, "brief-1.txt").read_text()
    assert "only with --harness human" in toy.gp("note", "--job", job, "--kind", "stamp", "--harness", "claude")
