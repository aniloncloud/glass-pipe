import pytest

from gp import tamper
from gp.cards import Card
from gp.protect import Protection


def mkcard(scope, edit_tests=False, kind=None):
    return Card(to="fake", goal="g", accept="pytest -q tests/test_x.py", argv=["pytest", "-q", "tests/test_x.py"],
                scope=scope, kind=kind, edit_tests=edit_tests)


def patch(*files):
    out = []
    for path, added, removed in files:
        out.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@ def fn():\n")
        out += [f"-{l}\n" for l in removed] + [f"+{l}\n" for l in added]
    return "".join(out)


@pytest.fixture
def prot(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("x")
    return Protection(tmp_path, ["-q", "tests/test_x.py"])


def judge(p, card, prot):
    return tamper.judge(tamper.parse_patch(p), card, prot)


def test_clean_patch_in_scope(prot):
    v = judge(patch(("src/a.py", ["return a + b"], ["return a - b"])), mkcard(["src/a.py"]), prot)
    assert v.status is None and not v.tests_edited


def test_out_of_scope(prot):
    v = judge(patch(("src/b.py", ["x"], [])), mkcard(["src/a.py"]), prot)
    assert v.status == "scope" and "src/b.py" in v.detail


@pytest.mark.parametrize("line", [
    "@pytest.mark.skip(reason='later')", "    pytest.xfail('nope')", "@unittest.skip('x')",
    "        self.skipTest('x')", "it.skip('works', () => {})", "describe.only('a', () => {})",
    "#[ignore]", "    t.Skip()",
])
def test_skip_markers_are_tamper_and_show_line(prot, line):
    v = judge(patch(("src/a.py", [line], [])), mkcard(["src/a.py"]), prot)
    assert v.status == "tamper" and line.strip() in v.detail


def test_word_skip_in_comment_is_not_tamper(prot):
    v = judge(patch(("src/a.py", ["# we skip empty rows here", "skip = True"], [])), mkcard(["src/a.py"]), prot)
    assert v.status is None


def test_touching_accept_test_is_tamper(prot):
    v = judge(patch(("src/a.py", ["y"], []), ("tests/test_x.py", ["assert True"], ["assert add(2,3)==5"])),
              mkcard(["src/a.py"]), prot)
    assert v.status == "tamper" and "tests/test_x.py" in v.detail


def test_edit_tests_override_gives_tests_edited_even_with_scope_files(prot):
    v = judge(patch(("src/a.py", ["y"], []), ("tests/test_x.py", ["assert add(2,3)==5 # tightened"], [])),
              mkcard(["src/a.py", "tests/test_x.py"], edit_tests=True), prot)
    assert v.status is None and v.tests_edited


def test_skip_still_wins_over_edit_tests(prot):
    v = judge(patch(("tests/test_x.py", ["@pytest.mark.skip"], [])),
              mkcard(["src/a.py", "tests/test_x.py"], edit_tests=True), prot)
    assert v.status == "tamper"


def test_runner_config_never_overridable(prot):
    v = judge(patch(("pytest.ini", ["addopts = -k nothing"], [])),
              mkcard(["src/a.py", "pytest.ini"], edit_tests=True), prot)
    assert v.status == "tamper"


def test_non_overridden_protected_file_is_tamper(prot, tmp_path):
    (tmp_path / "tests" / "conftest.py").write_text("")
    v = judge(patch(("tests/conftest.py", ["x"], [])), mkcard(["src/a.py", "tests/test_x.py"], edit_tests=True), prot)
    assert v.status == "tamper"


def test_pyproject_pytest_section_is_tamper(prot):
    v = judge(patch(("pyproject.toml", ['addopts = "-p no:cacheprovider -k nope"'], ["[tool.pytest.ini_options]"])),
              mkcard(["src/a.py", "pyproject.toml"]), prot)
    assert v.status == "tamper"


def test_symbol_hints_from_hunk_headers():
    p = patch(("src/a.py", ["x"], ["y"]))
    assert tamper.symbol_hints(p) == {"src/a.py": ["def fn():"]}


def test_numstat_counts():
    changes = tamper.parse_patch(patch(("src/a.py", ["a", "b"], ["c"])))
    assert (changes[0].adds, changes[0].dels) == (2, 1)


def test_symlink_and_mode_change_are_tamper(prot):
    sym = "diff --git a/src/a.py b/src/a.py\nnew file mode 120000\n--- /dev/null\n+++ b/src/a.py\n@@ -0,0 +1 @@\n+/etc/passwd\n"
    v = tamper.judge(tamper.parse_patch(sym), mkcard(["src/a.py"]), prot)
    assert v.status == "tamper" and "symlink" in v.detail
    mode = "diff --git a/src/a.py b/src/a.py\nold mode 100644\nnew mode 100755\n"
    v = tamper.judge(tamper.parse_patch(mode), mkcard(["src/a.py"]), prot)
    assert v.status == "tamper" and "mode" in v.detail
    normal_new = "diff --git a/src/b.py b/src/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/src/b.py\n@@ -0,0 +1 @@\n+x\n"
    assert tamper.judge(tamper.parse_patch(normal_new), mkcard(["src/b.py"]), prot).status is None
