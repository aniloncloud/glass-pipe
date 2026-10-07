"""Precedence table: decided before a worker exists (B1-B4) and after (rows 1-7)."""
import json

from tests.toykit import ACCEPT, BUG, FIX, TEST, card, ids, ok_step


def run1(toy, c, **kw):
    out = toy.run(c, **kw)
    (job,) = ids(out)
    return job, out, toy.state(job)


# --- before any worker -------------------------------------------------------------------

def test_vacuous_when_baseline_green_no_worker_started(toy):
    toy.write("src/calc.py", FIX)
    toy.commit()
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "vacuous" and toy.calls_made() == 0
    assert not toy.job_file(job, "brief-1.txt").exists()


def test_flaky_when_baseline_runs_disagree_no_worker(toy, tmp_path):
    flip = tmp_path / "flip"
    flip.mkdir()
    toy.config(f'accept_writable = ["{flip}"]\n')
    toy.write("tests/test_calc.py", TEST.replace(
        "    def test_add(self):\n",
        f"    def test_add(self):\n        import os\n        m = '{flip}/m'\n"
        "        if os.path.exists(m):\n            os.unlink(m)\n            return\n"
        "        open(m, 'w').close()\n"))
    toy.commit()
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "flaky" and toy.calls_made() == 0


def test_baseline_tree_reset_between_runs_prevents_false_flaky(toy):
    # the test leaves a marker in its tree on the first run; without a reset run 2 would go green
    toy.write("tests/test_calc.py", TEST.replace(
        "    def test_add(self):\n",
        "    def test_add(self):\n        import os\n        if os.path.exists('marker'):\n            return\n"
        "        open('marker', 'w').close()\n"))
    toy.commit()
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["baseline"] == [1, 1] and st["status"] != "flaky" and toy.calls_made() >= 1


def test_env_third_party_missing(toy):
    toy.write("tests/test_dep.py", "import unittest\nimport definitely_not_installed_pkg\n\n\nclass T(unittest.TestCase):\n"
                                   "    def test_x(self):\n        pass\n")
    toy.commit()
    toy.script([ok_step()])
    job, out, st = run1(toy, card(accept="python3 -m unittest tests.test_dep"))
    assert st["status"] == "env" and toy.calls_made() == 0


def test_env_missing_before_any_baseline(toy):
    toy.write("tests/data.local", "secret fixture")   # ignored by *.local, read by the suite
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "env-missing" and "tests/data.local" in st["detail"]
    assert not toy.job_file(job, "baseline.txt").exists() and toy.calls_made() == 0


def test_env_files_copy_satisfies_env_missing(toy):
    toy.write("tests/data.local", "x")
    toy.config('env_files = ["tests/data.local"]\n')
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "ok"


def test_refactor_red_no_worker(toy):
    toy.script([ok_step()])
    job, out, st = run1(toy, card(kind="refactor"))
    assert st["status"] == "refactor-red" and toy.calls_made() == 0


# --- after the worker ---------------------------------------------------------------------

def test_behavior_unchanged_needs_human_stamp(toy):
    toy.write("src/calc.py", FIX)
    toy.commit()
    toy.script([ok_step(fix="def add(a, b):\n    return sum((a, b))\n")])
    job, out, st = run1(toy, card(kind="refactor"))
    assert st["status"] == "behavior-unchanged" and "needs human stamp" in out
    assert "not-applied" in toy.gp("apply", job)
    toy.gp("note", "--job", job, "--kind", "stamp", "--harness", "human")
    assert "applied landed✓" in toy.gp("apply", job)


def test_tamper_on_accept_test_shows_offending_path(toy):
    toy.script([{"write": {"tests/test_calc.py": TEST.replace("5)", "-1)")},
                 "result": {"status": "done", "summary": "made test pass"}}])
    job, out, st = run1(toy, card())
    assert st["status"] == "tamper" and "tests/test_calc.py" in st["detail"]
    assert "touched protected test path tests/test_calc.py" in toy.gp("show", job)
    assert "not-applied" in toy.gp("apply", job)


def test_edit_tests_with_scope_file_is_tests_edited_not_ok(toy):
    toy.script([{"write": {"src/calc.py": FIX, "tests/test_calc.py": TEST + "\n# tightened\n"},
                 "result": {"status": "done", "summary": "x"}}])
    job, out, st = run1(toy, card(scope=["src/calc.py", "tests/test_calc.py"], edit_tests=True))
    assert st["status"] == "tests-edited"
    assert "not-applied" in toy.gp("apply", job)
    toy.gp("note", "--job", job, "--kind", "stamp", "--harness", "human")
    assert "applied" in toy.gp("apply", job)
    sc = toy.gp("stats")
    assert "gate-accepted  0/1" in sc        # stamped exception is never delegated labour


def test_scope_violation(toy):
    toy.script([{"write": {"src/calc.py": FIX, "src/extra.py": "x = 1\n"}, "result": {"status": "done", "summary": "x"}}])
    job, out, st = run1(toy, card())
    assert st["status"] == "scope" and "src/extra.py" in st["detail"]


def test_accept_writing_scope_file_after_freeze_is_tamper(toy):
    toy.write("tests/test_calc.py", TEST.replace(
        "    def test_add(self):\n",
        "    def test_add(self):\n        open('src/calc.py', 'a').write('# touched by accept\\n')\n"))
    toy.commit()
    toy.script([ok_step()])
    job, out, st = run1(toy, card())
    assert st["status"] == "tamper" and "accept wrote the tree" in st["detail"]


def test_skip_marker_in_scope_file_is_tamper(toy):
    toy.script([ok_step(fix=FIX + "\nimport unittest\n@unittest.skip('later')\nclass X(unittest.TestCase):\n    pass\n")])
    job, out, st = run1(toy, card())
    assert st["status"] == "tamper" and "@unittest.skip" in st["detail"]
