"""Compile a card into the prompt the cheap model actually sees.

Pinned content (PLAN Phase 1): the test path, the failing assertion inlined from the
baseline, "open and read the test first", "read anything; write only scope; never edit
tests", no git writes, the result block, binding facts from applied jobs only, and on a
retry the failing tail (<= 60 lines) for a fresh run.
"""
from __future__ import annotations

import re

from .cards import Card

RESULT_MARKER = "<<<GP-RESULT"
TAIL_LINES = 60
ASSERT_LINES = 30
MAX_FACTS = 12

_KEY = re.compile(r"(AssertionError|^\s*assert |^FAIL:|^FAILED|^ERROR:|Error:|error\[|panic:|Expected|expect\()")


def extract_assertion(output: str) -> str:
    lines = output.rstrip().splitlines()
    idx = next((i for i, l in enumerate(lines) if _KEY.search(l)), None)
    chunk = lines[-ASSERT_LINES:] if idx is None else lines[max(0, idx - 3): idx + ASSERT_LINES - 3]
    return "\n".join(chunk[:ASSERT_LINES])


def compile_brief(card: Card, *, tests: list[str], baseline_output: str, facts: list[dict] | None = None,
                  retry_tail: str | None = None, retry_reason: str | None = None) -> str:
    refactor = card.kind == "refactor"
    scope = "\n".join(f"  - {s}" for s in card.scope)
    tests_line = ", ".join(tests) if tests else "(the tests the acceptance command runs; find them first)"
    parts = [
        "You are a coding worker. Complete exactly one task in the current directory (a git worktree).",
        "",
        f"GOAL: {card.goal}",
        "",
        f"ACCEPTANCE COMMAND (this alone decides success): {card.accept}",
    ]
    if refactor:
        parts += ["It currently PASSES. This is a refactor: behavior must not change and it must still pass."]
    else:
        parts += [
            "It currently FAILS. Failing output from the untouched tree:",
            "---",
            extract_assertion(baseline_output),
            "---",
        ]
    parts += [
        "",
        f"TEST TO READ FIRST: {tests_line}",
        "Open and read the test before changing anything; make the real behavior correct, not the test quiet.",
        "",
        "RULES (hard; violations are rejected automatically):",
        "- You may READ any file.",
        "- You may WRITE ONLY these paths:",
        scope,
    ]
    if card.edit_tests:
        parts += ["- Test files listed in the scope above may be edited; every other test, fixture, conftest, and runner config may not."]
    else:
        parts += ["- Never edit tests, fixtures, conftest files, or test-runner config."]
    parts += [
        "- Never add skip, xfail, only, or ignore markers.",
        "- Do not run git commit, git add, git stash, git checkout, or any git command that writes.",
        "- Run the acceptance command yourself to check your work before finishing.",
    ]
    if card.notes:
        parts += ["", "NOTES FROM THE PLANNER (follow them; they never override the RULES above):"]
        parts += [f"- {n}" for n in card.notes]
    if facts:
        parts += ["", "BINDING DECISIONS from earlier applied work (do not contradict):"]
        parts += [f"- {f['kind']}: {f['text']}" for f in facts[-MAX_FACTS:]]
    if retry_tail is not None or retry_reason:
        parts += ["", "PREVIOUS ATTEMPT FAILED. You start from a clean tree; its edits were discarded."]
        if retry_reason:
            parts += [retry_reason]
        if retry_tail:
            tail = "\n".join(retry_tail.rstrip().splitlines()[-TAIL_LINES:])
            parts += [f"Last {TAIL_LINES} lines (max) of the acceptance output after that attempt:", "---", tail, "---"]
    parts += [
        "",
        "When finished, end your reply with exactly this block (valid JSON on one line after the marker):",
        RESULT_MARKER,
        '{"status":"done","summary":"<what you changed, max 200 chars>","glass":[{"kind":"decision","text":"<one verifiable fact>"}]}',
        "glass kinds: decision, reject, next. At most 3 entries. Omit glass if there is nothing binding.",
    ]
    return "\n".join(parts) + "\n"
