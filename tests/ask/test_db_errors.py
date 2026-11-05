"""Database error text is not shown to users (finding S-05)."""

import logging
from datetime import date

import pytest

from skysnap_ask.db import DatabaseUnavailableError, QueryError, scrub_error, user_message
from skysnap_ask.service import AskService

from .fakes import FakeLLM, FakeRetriever

LEAK = "SELECT command denied to user 'skysnap_reader'@'172.22.0.1' for table 'weather_rejects'"


def test_scrub_error_removes_account_and_host_but_keeps_the_sql_problem():
    out = scrub_error(f"(1142, \"{LEAK}\")")
    assert "skysnap_reader" not in out
    assert "172.22.0.1" not in out
    assert "command denied" in out and "weather_rejects" in out


@pytest.mark.parametrize(
    "text",
    [
        "Access denied for user 'root'@'localhost'",
        "Can't connect to MySQL server on '10.0.0.5' (111)",
        "user 'a'@'%' from 192.168.1.20",
    ],
)
def test_scrub_error_removes_accounts_and_ip_addresses(text):
    out = scrub_error(text)
    assert "@" not in out.replace("<account>", "")
    for fragment in ("10.0.0.5", "192.168.1.20", "'root'", "'a'"):
        assert fragment not in out


def test_user_message_is_generic_with_the_error_code():
    msg = user_message(QueryError(LEAK, code=1142))
    assert msg == "The database could not run that query (error 1142)."


def test_user_message_without_a_code():
    assert user_message(QueryError("boom")) == "The database could not run that query."


def test_user_message_for_unavailable_database():
    assert "unavailable" in user_message(DatabaseUnavailableError("x", code=2003)).lower()
    assert "x" not in user_message(DatabaseUnavailableError("secret-host", code=2003))


def _service(runner, llm):
    return AskService(
        retriever=FakeRetriever([]), llm=llm, runner=runner, today=lambda: date(2026, 10, 1)
    )


def test_ask_shows_generic_error_logs_detail_and_scrubs_the_repair_prompt(caplog):
    attempts = []

    def runner(sql):
        attempts.append(sql)
        raise QueryError(LEAK, code=1142)

    llm = FakeLLM(
        json_responses=[
            {"sql": "SELECT name FROM dim_city", "reason": None},
            {"sql": "SELECT name FROM dim_city", "reason": None},
        ]
    )
    with caplog.at_level(logging.WARNING, logger="skysnap_ask"):
        result = _service(runner, llm).ask("q")

    assert result.status == "failed"
    assert result.error == "The database could not run that query (error 1142)."
    assert len(attempts) == 2
    repair_prompt = llm.json_prompts[1]
    assert "command denied" in repair_prompt  # the model still sees the SQL error
    assert "skysnap_reader" not in repair_prompt
    assert "172.22.0.1" not in repair_prompt
    assert "skysnap_reader" in caplog.text  # full detail stays server-side
