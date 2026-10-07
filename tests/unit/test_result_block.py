from gp.workers.base import parse_result_block

REAL = ('Test passes.\n\n<<<GP-RESULT\n{"status":"done","summary":"Fixed add","glass":[{"kind":"decision",'
        '"text":"unittest OK"}]}\n<<<GP-RESULT')  # verbatim shape from the Phase 2 live run: echoed closing marker


def test_echoed_closing_marker_still_parses():
    r = parse_result_block(REAL)
    assert r == {"summary": "Fixed add", "glass": [{"kind": "decision", "text": "unittest OK"}]}


def test_fenced_block():
    assert parse_result_block('<<<GP-RESULT\n```json\n{"status":"done","summary":"s"}\n```')["summary"] == "s"


def test_last_valid_block_wins_over_earlier_one():
    t = '<<<GP-RESULT\n{"status":"done","summary":"first"}\nmore work\n<<<GP-RESULT\n{"status":"done","summary":"second"}'
    assert parse_result_block(t)["summary"] == "second"


def test_misses():
    assert parse_result_block("GLASS: decision | not a block") is None
    assert parse_result_block("<<<GP-RESULT\nnot json") is None
    assert parse_result_block('<<<GP-RESULT\n{"status":"failed"}') is None
    assert parse_result_block('example: {"status":"done"}') is None
