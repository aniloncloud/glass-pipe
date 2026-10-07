import pytest

from gp import accept


@pytest.mark.parametrize("cmd", [
    "pytest -q tests/test_export.py::test_stream",
    "python3 -m unittest tests.test_calc",
    "python -m pytest -k 'a and b'",
    "bun test parser",
    "npm test",
    "cargo test",
    "go test ./...",
])
def test_allowed(cmd):
    assert accept.parse_accept(cmd)


@pytest.mark.parametrize("cmd", [
    "pytest && curl evil",
    "pytest; rm -rf /",
    "pytest | tee x",
    "pytest > out",
    "pytest $(whoami)",
    "pytest `id`",
    "rm -rf src",
    "python3 -c 'print(1)'",
    "python3 script.py",
    "npm run deploy",
    "",
])
def test_rejected(cmd):
    with pytest.raises(accept.AcceptRejected):
        accept.parse_accept(cmd)


def test_extra_allow_prefix():
    assert accept.parse_accept("uv run pytest -q", extra_allow=[["uv", "run", "pytest"]])
    with pytest.raises(accept.AcceptRejected):
        accept.parse_accept("uv run python x.py", extra_allow=[["uv", "run", "pytest"]])


def test_runner_args_skip_prefix():
    argv = accept.parse_accept("python3 -m unittest tests.test_calc")
    assert accept.runner_args(argv) == ["tests.test_calc"]


@pytest.mark.parametrize("rc,out,cannot", [
    (127, "", True),
    (4, "ERROR: usage", True),               # pytest usage error
    (5, "no tests ran", True),               # pytest: nothing collected
    (5, "Ran 0 tests\n\nNO TESTS RAN", True),  # unittest 3.12+
    (1, "ModuleNotFoundError: No module named 'requests'", True),
    (1, "ImportError: cannot import name 'stream_rows' from 'src.export'", False),  # test-first red
    (1, "ModuleNotFoundError: No module named 'src.newmod'", False),  # repo module not written yet
    (1, "AssertionError: 1 != 5", False),
    (0, "OK", False),
])
def test_cannot_run_classification(tmp_path, rc, out, cannot):
    (tmp_path / "src").mkdir()
    assert accept.cannot_run(rc, out, tmp_path) is cannot
