"""Fixtures for the integration suite; helpers live in tests/toykit.py (no `from conftest import`)."""
import pytest

from tests.toykit import make_toy


@pytest.fixture
def toy(tmp_path):
    t = make_toy(tmp_path)
    t.config()
    return t
