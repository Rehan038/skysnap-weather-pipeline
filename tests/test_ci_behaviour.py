"""The db fixture must fail loudly in CI and skip quietly on a laptop."""

import pytest

from tests.conftest import mysql_unreachable


def test_db_unreachable_fails_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="MySQL not reachable"):
        mysql_unreachable("connection refused")


def test_db_unreachable_skips_outside_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception):
        mysql_unreachable("connection refused")
