import json

import pytest

from gp import cards
from gp.protect import Protection
from gp.repo import Repo, DEFAULT_CONFIG


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    for f in ["src/export/csv.py", "src/export/__init__.py", "src/x.py", "src/parser.ts", "src/parser.test.ts",
              "tests/test_export.py", "tests/conftest.py", "tests/fixtures/a.csv", "tests/test_calc.py",
              "conftest.py", "package.json", "pytest.ini"]:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    import subprocess
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["workers"]["fake"] = {"type": "fake"}
    return Repo(root=root.resolve(), config=cfg)


def card(**kw):
    base = {"to": "codex", "goal": "make tests/test_export.py::test_stream pass",
            "accept": "pytest -q tests/test_export.py::test_stream", "scope": ["src/export/csv.py"]}
    base.update(kw)
    return json.dumps(base)


def parse(repo, *lines):
    return cards.parse_batch("\n".join(lines), repo)


# --- README / skill examples must be legal -------------------------------------------

def test_readme_example_cards_are_legal(repo):
    out = parse(repo,
                '{"to":"codex","goal":"make tests/test_export.py::test_stream pass","accept":"pytest -q tests/test_export.py::test_stream","scope":["src/export/csv.py"]}',
                '{"to":"opencode","goal":"make the trailing-comma tests in parser.test.ts pass","accept":"bun test parser","scope":["src/parser.ts"]}')
    assert [c.to for c in out] == ["codex", "opencode"]
    assert out[0].scope == ["src/export/csv.py"]


def test_apostrophe_and_dashes_in_goal(repo):
    (c,) = parse(repo, card(goal="don't buffer\n---\nreally"))
    assert "don't" in c.goal


# --- rejections ----------------------------------------------------------------------

@pytest.mark.parametrize("bad,msg", [
    ({"scope": None}, "scope"),
    ({"scope": []}, "scope"),
    ({"goal": ""}, "goal"),
    ({"to": "nobody"}, "unknown worker"),
    ({"accept": "pytest && curl x"}, "metacharacters"),
    ({"scope": ["../outside.py"]}, "outside repo"),
    ({"kind": "bigrefactor"}, "kind"),
])
def test_rejects(repo, bad, msg):
    d = json.loads(card())
    for k, v in bad.items():
        if v is None:
            d.pop(k)
        else:
            d[k] = v
    with pytest.raises(cards.CardError) as e:
        parse(repo, json.dumps(d))
    assert msg in str(e.value)


def test_bad_json_line(repo):
    with pytest.raises(cards.CardError) as e:
        parse(repo, "{not json")
    assert "card 1" in str(e.value)


def test_empty_batch(repo):
    with pytest.raises(cards.CardError):
        parse(repo, "  \n")


# --- disjoint scopes -----------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    (["src/x.py"], ["src/x.py"]),
    (["src/export/"], ["src/export/csv.py"]),
    (["./src/x.py"], ["src/x.py"]),
    (["src"], ["src/export/csv.py"]),
])
def test_overlapping_scopes_rejected(repo, a, b):
    with pytest.raises(cards.CardError) as e:
        parse(repo, card(scope=a), card(scope=b, accept="pytest -q tests/test_calc.py"))
    assert "overlap" in str(e.value)


def test_disjoint_scopes_ok(repo):
    out = parse(repo, card(scope=["src/export/csv.py"]), card(scope=["src/x.py"], accept="pytest -q tests/test_calc.py"))
    assert len(out) == 2


# --- protected paths -----------------------------------------------------------------

def test_scope_with_accept_test_rejected_without_edit_tests(repo):
    with pytest.raises(cards.CardError) as e:
        parse(repo, card(scope=["src/export/csv.py", "tests/test_export.py"]))
    assert "protected" in str(e.value)


def test_scope_with_accept_test_allowed_with_edit_tests(repo):
    (c,) = parse(repo, card(scope=["src/export/csv.py", "tests/test_export.py"], edit_tests=True))
    assert c.edit_tests


def test_scope_of_only_protected_rejected_even_with_edit_tests(repo):
    with pytest.raises(cards.CardError) as e:
        parse(repo, card(scope=["tests/test_export.py"], edit_tests=True))
    assert "outside the protected" in str(e.value)


def test_conftest_and_fixtures_protected_for_explicit_accept(repo):
    p = Protection(repo.root, ["tests/test_export.py::test_stream"])
    assert p.is_protected("tests/test_export.py")
    assert p.is_protected("tests/conftest.py")
    assert p.is_protected("conftest.py")
    assert p.is_protected("tests/fixtures/a.csv")
    assert p.is_protected("pytest.ini")          # runner config always
    assert p.is_protected("package.json")
    assert not p.is_protected("src/export/csv.py")
    assert not p.is_protected("tests/test_calc.py")  # not named by accept
    assert p.explicit == ["tests/test_export.py"]


def test_unittest_dotted_module_is_explicit(repo):
    p = Protection(repo.root, ["tests.test_calc"])
    assert p.explicit == ["tests/test_calc.py"]
    assert p.is_protected("tests/test_calc.py")


def test_bare_runner_protects_all_test_files(repo):
    p = Protection(repo.root, ["-q"])
    assert p.explicit == []
    for f in ["tests/test_calc.py", "src/parser.test.ts", "pkg/foo_test.go", "web/__tests__/a.js",
              "a/b.spec.ts", "test_thing.py", "tests/fixtures/a.csv"]:
        assert p.is_protected(f), f
    assert not p.is_protected("src/parser.ts")


def test_bare_runner_filter_word_is_not_a_path(repo):
    p = Protection(repo.root, ["parser"])  # `bun test parser`
    assert p.explicit == [] and p.is_protected("src/parser.test.ts")
