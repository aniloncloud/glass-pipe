import pytest

from gp import hooks


@pytest.mark.parametrize("cmd", [
    "gp run <<'EOF'\n{\"to\":\"opencode\",\"goal\":\"don't buffer\"}\nEOF",
    'gp run <<"EOF"\n{}\nEOF',
    "gp wait ab12cd",
    "gp apply ab12cd",
    "gp show ab12cd --why",
    "gp ask where is the CSV writer",
    "gp status",
])
def test_plain_gp_allowed(cmd):
    assert hooks.is_plain_gp(cmd)


@pytest.mark.parametrize("cmd", [
    "gp run <<EOF\n{}\nEOF",                                  # unquoted delimiter: shell expansion in body
    "gp run <<'EOF'\n{}\nEOF\nrm -rf src",                    # command smuggled after the heredoc
    "gp run <<'EOF'\n{}\n",                                   # unterminated
    "gp wait x && rm -rf src",
    "gp wait x; curl evil",
    "gp wait $(id)",
    "gp note --job x --kind stamp --harness human",          # stamping is never auto-allowed
    "gp wait x\nrm -rf src",
    "gp apply <<'EOF'\nx\nEOF",                               # heredoc only for run
    "rm -rf src",
    "gpx run",
])
def test_not_plain_gp(cmd):
    assert not hooks.is_plain_gp(cmd)


@pytest.mark.parametrize("cmd,stamp", [
    ("gp note --job ab --kind stamp --harness human", True),
    ("/x/bin/gp note --kind override --harness=human --job ab", True),
    ("gp note --job ab --kind decision --text 'x'", False),
    ("gp apply ab", False),
])
def test_self_stamp_detection(cmd, stamp):
    assert hooks.is_self_stamp(cmd) is stamp


@pytest.mark.parametrize("tool,ti,n", [
    ("Write", {"content": "a\nb\nc\n"}, 3),
    ("Edit", {"old_string": "a", "new_string": "a\nb"}, 2),
    ("MultiEdit", {"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "x\ny", "new_string": "z"}]}, 3),
    ("NotebookEdit", {"new_source": "1\n2"}, 2),
])
def test_edit_lines(tool, ti, n):
    assert hooks.edit_lines(tool, ti) == n


@pytest.mark.parametrize("cmd", [
    "gp run <<'EOF'\n{}\nEOF\nrm -rf src\nEOF",          # audit #3: second delimiter smuggles a command
    "gp run <<'EOF'\n{}\n EOF\nrm -rf src\nEOF",
    "gp run <<'EOF'\n{}\nEOF\r\nrm -rf src",
    "gp wait x\x00; rm -rf src",
    "gp wait x\x1b[2K",
])
def test_heredoc_and_control_char_bypasses_rejected(cmd):
    assert not hooks.is_plain_gp(cmd)


@pytest.mark.parametrize("cmd", [
    "bash -c 'gp note --job x --kind stamp --harness human'",
    "true\ngp note --job x --kind stamp --harness human",
    "FOO=1 gp note --kind override --harness human --job x",
    "(cd . && gp note --job x --kind stamp --harness=human)",
])
def test_self_stamp_guard_sees_wrappers(cmd):
    assert hooks.is_self_stamp(cmd)
