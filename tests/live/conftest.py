"""Live tests against the real OpenCode worker. Opt-in: GP_LIVE=1 (spends a few cents of OpenCode Go)."""
import os
from pathlib import Path

import pytest

from tests import toykit

MODEL = os.environ.get("GP_LIVE_MODEL", "opencode-go/deepseek-v4.1-flash")

collect_ignore_glob = [] if os.environ.get("GP_LIVE") == "1" else ["test_*.py"]


@pytest.fixture
def live(tmp_path):
    t = toykit.make_toy(tmp_path)
    t.write(".glass/config.toml", "\n".join([
        "stall_s = 180", "job_timeout_s = 420", "retries = 1",
        "[workers.opencode]", 'type = "opencode"', f'model = "{MODEL}"',
        "[fallback]",
    ]) + "\n")
    yield t  # per-job OpenCode servers stop with their job


def card(**kw):
    d = toykit.card(to="opencode")
    d.update(kw)
    return d
