from tests.toykit import ACCEPT, BUG, FIX, card, ids, ok_step


def test_green_path_receipt_then_apply(toy):
    toy.script([ok_step(glass=[{"kind": "decision", "text": "add() uses +"}])])
    out = toy.gp("run", input=__import__("json").dumps(card()), check_rc=0)
    (job,) = ids(out)
    assert out.count("\n") == 1                                   # one stdout line per job
    assert " ok fake " in out and "semantic conflicts unchecked" in out and f".glass/jobs/{job}/receipt.txt" in out
    assert toy.read("src/calc.py") == BUG                          # nothing lands until apply
    brief = toy.job_file(job, "brief-1.txt").read_text()
    assert "AssertionError: -1 != 5" in brief and "TEST TO READ FIRST: tests/test_calc.py" in brief
    st = toy.state(job)
    assert st["baseline"] == [1, 1] and st["accept_green"] and st["files"] == [{"path": "src/calc.py", "adds": 1, "dels": 1}]

    assert toy.job_cache(job).joinpath("a").exists()              # worktree kept until apply
    out = toy.gp("apply", job)
    assert "applied landed✓" in out and "semantic conflicts unchecked" in out
    assert toy.read("src/calc.py") == FIX
    assert "add() uses +" in toy.read(".glass/THREAD.md")
    assert not toy.job_cache(job).joinpath("a").exists()          # worktree released on apply
