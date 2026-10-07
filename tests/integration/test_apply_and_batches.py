"""Apply clean path, dirty reasons, landing-check auto revert, LIFO undo, batches, durability."""
import json
import os
import signal
import subprocess
import sys
import time

from tests.toykit import ACCEPT, BUG, FIX, ROOT, TEST, card, ids, ok_step


def add_module(toy, name):
    toy.write(f"src/{name}.py", f"def {name}():\n    return 0\n")
    toy.write(f"tests/test_{name}.py", f"import unittest\nfrom src.{name} import {name}\n\n\nclass T(unittest.TestCase):\n"
                                       f"    def test_{name}(self):\n        self.assertEqual({name}(), 1)\n")


def fixed(name):
    return f"def {name}():\n    return 1\n"


def mcard(name):
    return card(goal=f"make tests/test_{name}.py pass", accept=f"python3 -m unittest tests.test_{name}",
                scope=[f"src/{name}.py"])


def test_dirty_on_unrelated_edit_keeps_patch(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.write("src/calc.py", BUG + "# my edit\n")
    out = toy.gp("apply", job)
    assert "dirty" in out and "src/calc.py changed since snapshot (unrelated edit)" in out
    assert toy.read("src/calc.py") == BUG + "# my edit\n"


def test_dirty_names_the_job_that_applied_first(toy):
    toy.script([ok_step()])
    (j1,) = ids(toy.run(card()))
    (j2,) = ids(toy.run(card()))
    assert "applied" in toy.gp("apply", j1)
    out = toy.gp("apply", j2)
    assert f"(job {j1} applied)" in out


def test_landing_red_auto_reverts_scope_files_ignoring_debris(toy):
    # main has an ignored root file the worktree never sees: green in the worktree, red on main
    toy.write("tests/test_calc.py", TEST.replace("self.assertEqual(add(2, 3), 5)",
                                                 "import os\n        os.makedirs('.pytest_cache', exist_ok=True)\n"
                                                 "        open('.pytest_cache/debris', 'w').write('x')\n"
                                                 "        self.assertEqual(add(2, 3), 6 if os.path.exists('mode.local') else 5)"))
    toy.commit()
    toy.write("mode.local", "1")
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    assert toy.state(job)["status"] == "ok"
    before = (toy.root / "src" / "calc.py").read_bytes()
    out = toy.gp("apply", job)
    assert "landing-red" in out and "scope files restored" in out
    assert (toy.root / "src" / "calc.py").read_bytes() == before
    assert (toy.root / ".pytest_cache" / "debris").exists()       # accept left debris on main...
    assert toy.git("status", "--porcelain", "src/calc.py") == ""      # ...yet the scope file is restored exactly
    sc = toy.gp("stats")
    assert "landed         0/1" in sc


def test_lifo_undo(toy):
    add_module(toy, "two")
    toy.commit()
    toy.script([ok_step()], name="fake")
    toy.script([ok_step(fix=fixed("two"), path="src/two.py")], name="fake2")
    out = toy.run(card(), {**mcard("two"), "to": "fake2"})
    j1, j2 = ids(out)
    assert out.count("\n") == 2 and out.count(" ok ") == 2         # one call, one line per job
    assert "applied" in toy.gp("apply", j1) and "applied" in toy.gp("apply", j2)
    assert f"undo later job(s) first: {j2}" in toy.gp("undo", j1)
    assert "undone" in toy.gp("undo", j2) and "undone" in toy.gp("undo", j1)
    assert toy.read("src/calc.py") == BUG and toy.read("src/two.py") == "def two():\n    return 0\n"


def test_undo_refused_after_later_edit(toy):
    toy.script([ok_step()])
    (job,) = ids(toy.run(card()))
    toy.gp("apply", job)
    toy.write("src/calc.py", FIX + "# later\n")
    assert "changed after apply" in toy.gp("undo", job)


def test_three_card_batch_one_call_each_isolated(toy):
    for n in ("aa", "bb"):
        add_module(toy, n)
    toy.commit()
    toy.config(tables=f'[workers.fake3]\ntype = "fake"\nscript = "{toy.scripts / "fake3.json"}"\n')
    toy.script([ok_step()], name="fake")
    toy.script([ok_step(fix=fixed("aa"), path="src/aa.py")], name="fake2")
    toy.script([{"write": {"src/bb.py": fixed("bb"), "src/calc.py": FIX}, "result": {"status": "done", "summary": "x"}}],
               name="fake3")
    out = toy.run(card(), {**mcard("aa"), "to": "fake2"}, {**mcard("bb"), "to": "fake3"})
    j = ids(out)
    assert len(j) == 3 and out.count("\n") == 3
    assert [toy.state(x)["status"] for x in j] == ["ok", "ok", "scope"]   # per-job scope isolation


def test_overlapping_batch_rejected_no_jobs(toy):
    out = toy.run(card(), card(scope=["src/"]))
    assert out.startswith("gp! rejected, no jobs started") and "overlap" in out
    assert not (toy.root / ".glass" / "jobs").exists()


def test_killed_waiter_keeps_receipts(toy):
    toy.script([{"sleep": 2, "heartbeat": 0.3, **ok_step()}])
    env = {**os.environ, "PYTHONPATH": str(ROOT), "GP_CACHE_DIR": str(toy.cache), "GP_DEADLINE_S": "60"}
    p = subprocess.Popen([sys.executable, "-m", "gp", "run"], cwd=toy.root, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)
    p.stdin.write(json.dumps(card()))
    p.stdin.close()
    time.sleep(1.0)
    p.kill()
    p.wait()
    (job,) = [d.name for d in (toy.root / ".glass" / "jobs").iterdir()]
    out = toy.gp("wait", job)
    assert f"gp#{job} ok" in out and toy.job_file(job, "receipt.txt").exists()


def test_deadline_prints_running_line(toy):
    toy.script([{"sleep": 4, "heartbeat": 0.3, **ok_step()}])
    out = toy.run(card(), deadline=1)
    (job,) = ids(out)
    assert f"running" in out and f"→ gp wait {job}" in out
    assert " ok " in toy.gp("wait", job)


def test_stale_trees_listed_and_dropped(toy):
    toy.script([{**ok_step(), "write": {"src/calc.py": FIX, "src/x.py": "1"}}])
    (job,) = ids(toy.run(card()))
    d = toy.job_cache(job)
    old = time.time() - 3 * 86400
    os.utime(d, (old, old))
    assert f"stale tree: gp#{job}" in toy.gp("status")
    assert "dropped 1" in toy.gp("drop", "--stale")
    assert not (d / "a").exists()


def test_status_and_show_why(toy):
    toy.script([{"write": {"src/calc.py": "def add(a, b):\n    return a * b\n"}, "result": {"status": "done", "summary": "x"}}])
    (job,) = ids(toy.run(card()))
    assert f"gp#{job} fail" in toy.gp("status")
    assert "AssertionError: 6 != 5" in toy.gp("show", job, "--why")


def test_dead_job_still_gets_receipt_and_ledger(toy):
    toy.script([{"sleep": 30, "heartbeat": 0.3, **ok_step()}])
    out = toy.run(card(), deadline=1)
    (job,) = ids(out)
    pid = toy.state(job)["pid"]
    os.killpg(pid, signal.SIGKILL)
    out = toy.gp("wait", job)
    assert f"gp#{job} crash" in out and toy.job_file(job, "receipt.txt").exists()
    assert '"status": "crash"' in (toy.root / ".glass" / "ledger.jsonl").read_text()
