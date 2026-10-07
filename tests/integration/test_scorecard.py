"""The five counters; no savings number without them."""
import json

from tests.toykit import FIX, TEST, card, ids, ok_step
from gp.util import now


def test_success_counts_all_five(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    sc = toy.gp("stats")
    assert "cards submitted: 1" in sc and "successful offloads: 1/1" in sc
    for k in ("landed", "gate-accepted", "stayed", "not-rescued", "card-stable"):
        assert f"{k:<14} 1/1" in sc
    assert "savings: not computed" in sc


def test_rescue_from_edit_log_breaks_success(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    with open(toy.root / ".glass" / "edits.jsonl", "a") as fh:
        fh.write(json.dumps({"ts": "2999-01-01T00:00:00Z", "path": "src/calc.py", "lines": 2}) + "\n")
    sc = toy.gp("stats")
    assert "not-rescued    0/1" in sc and "successful offloads: 0/1" in sc


def test_editing_accept_test_after_submit_breaks_card_stable(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.write("tests/test_calc.py", TEST.replace("assertEqual", "assertTrue(True) or self.assertEqual"))
    sc = toy.gp("stats")
    assert "card-stable    0/1" in sc


def test_fixup_commit_breaks_stayed_but_committing_patch_does_not(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    toy.commit("land the patch as-is")
    assert "stayed         1/1" in toy.gp("stats")
    toy.write("src/calc.py", FIX + "\n# fix-up\n")
    toy.commit("fix-up", later_s=10)   # clearly after the apply second, even on a fast CI runner
    assert "stayed         0/1" in toy.gp("stats")


def test_undo_breaks_stayed(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    toy.gp("undo", job)
    assert "stayed         0/1" in toy.gp("stats")


def test_vacuous_rate_counts_early_vacuous(toy):
    toy.write("src/calc.py", FIX)
    toy.commit()
    toy.script([ok_step()])
    toy.run(card())
    sc = toy.gp("stats")
    assert "vacuous rate: 100%  (of 1 cards that finished baseline)" in sc


def test_bare_runner_card_stable_sees_test_edit(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card(accept="python3 -m unittest")))   # discovery: no explicit path
    assert toy.state(job)["status"] == "ok"
    assert "card-stable    1/1" in toy.gp("stats")
    toy.write("tests/test_calc.py", TEST.replace("5)", "5)  # loosened later"))
    assert "card-stable    0/1" in toy.gp("stats")
