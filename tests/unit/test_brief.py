"""Golden-prompt tests: the brief is what the cheap model sees. Set GP_UPDATE_GOLDEN=1 to rewrite."""
import os
from pathlib import Path

import pytest

from gp import brief
from gp.cards import Card

GOLDEN = Path(__file__).resolve().parent.parent / "golden"

BASELINE = """F
======================================================================
FAIL: test_add (tests.test_calc.T.test_add)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "tests/test_calc.py", line 6, in test_add
    self.assertEqual(add(2, 3), 5)
AssertionError: -1 != 5

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (failures=1)
"""


def card(**kw):
    d = dict(to="opencode", goal="make tests/test_calc.py::T::test_add pass", accept="python3 -m unittest tests.test_calc",
             argv=["python3", "-m", "unittest", "tests.test_calc"], scope=["src/calc.py"])
    d.update(kw)
    return Card(**d)


def check(name, text):
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("GP_UPDATE_GOLDEN") or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"golden mismatch: {name} (GP_UPDATE_GOLDEN=1 to accept)"


def test_golden_first_attempt():
    check("brief_first", brief.compile_brief(card(), tests=["tests/test_calc.py"], baseline_output=BASELINE))


def test_golden_retry_with_facts():
    facts = [{"kind": "decision", "text": "add() returns int, never float", "job": "a1"}]
    tail = "\n".join(f"line {i}" for i in range(100))
    check("brief_retry", brief.compile_brief(card(), tests=["tests/test_calc.py"], baseline_output=BASELINE,
                                             facts=facts, retry_tail=tail))


def test_golden_refactor_edit_tests():
    check("brief_refactor", brief.compile_brief(card(kind="refactor", edit_tests=True, scope=["src/calc.py", "tests/test_calc.py"]),
                                                tests=["tests/test_calc.py"], baseline_output="OK"))


def test_pinned_content():
    b = brief.compile_brief(card(), tests=["tests/test_calc.py"], baseline_output=BASELINE)
    assert "AssertionError: -1 != 5" in b                 # assertion inlined
    assert "TEST TO READ FIRST: tests/test_calc.py" in b
    assert "Open and read the test" in b
    assert "WRITE ONLY these paths:\n  - src/calc.py" in b
    assert "Never edit tests" in b
    assert "git commit" in b
    assert b.rstrip().splitlines()[-3] == brief.RESULT_MARKER


def test_retry_tail_capped_at_60_lines():
    tail = "\n".join(f"L{i}" for i in range(200))
    b = brief.compile_brief(card(), tests=[], baseline_output=BASELINE, retry_tail=tail)
    assert "L139" not in b and "L140" in b and "L199" in b


def test_facts_capped_at_12():
    facts = [{"kind": "decision", "text": f"f{i}"} for i in range(20)]
    b = brief.compile_brief(card(), tests=[], baseline_output=BASELINE, facts=facts)
    assert "f7" not in b and "f8" in b and "f19" in b
