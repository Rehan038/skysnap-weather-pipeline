from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from skysnap_dash.health import (
    EXPECTED_ROWS_PER_DAY,
    Freshness,
    freshness,
    ist_today,
    pipeline_has_run,
)

LATEST = date(2026, 11, 2)


@pytest.mark.parametrize(
    "age,status,label",
    [
        (0, "green", "Up to date"),
        (1, "green", "Up to date"),
        (2, "amber", "Slightly behind"),
        (3, "amber", "Slightly behind"),
        (4, "red", "Stale"),
        (10, "red", "Stale"),
    ],
)
def test_freshness_by_age(age, status, label):
    result = freshness(LATEST, LATEST + timedelta(days=age))
    assert result == Freshness(status, age, label)


def test_future_date_is_age_zero_green():
    result = freshness(LATEST + timedelta(days=2), LATEST)
    assert result.age_days == 0
    assert result.status == "green"


def test_none_is_red_no_data():
    result = freshness(None, LATEST)
    assert result == Freshness("red", None, "No data")


def test_ist_today_rolls_over_before_utc():
    now = datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
    assert ist_today(now) == date(2026, 11, 4)


def test_ist_today_defaults_to_current_time():
    assert isinstance(ist_today(), date)


def test_expected_rows_is_120():
    assert EXPECTED_ROWS_PER_DAY == 120


IST = ZoneInfo("Asia/Kolkata")


def at(h, m=0, day=5):
    return datetime(2026, 11, day, h, m, tzinfo=IST)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(0, 0), False),
        (at(5, 59), False),
        (at(6, 0), True),  # the DAG fires at 00:30 UTC = 06:00 IST
        (at(6, 1), True),
        (at(23, 59), True),
    ],
)
def test_pipeline_has_run_switches_at_0600_ist(moment, expected):
    assert pipeline_has_run(moment) is expected


def test_pipeline_has_run_converts_other_timezones_to_ist():
    assert pipeline_has_run(datetime(2026, 11, 5, 0, 29, tzinfo=UTC)) is False  # 05:59 IST
    assert pipeline_has_run(datetime(2026, 11, 5, 0, 30, tzinfo=UTC)) is True  # 06:00 IST


@pytest.mark.parametrize(
    ("moment", "latest", "status"),
    [
        # After the 06:00 IST run: the approved bands apply to the plain age.
        (at(7), date(2026, 11, 4), "green"),  # age 1: loaded this morning
        (at(23, 59), date(2026, 11, 4), "green"),
        (at(7), date(2026, 11, 3), "amber"),  # age 2: this morning's run did not load
        (at(7), date(2026, 11, 2), "amber"),  # age 3
        (at(7), date(2026, 11, 1), "red"),  # age 4
        # Before the run: today's load has not happened yet, so measure from yesterday.
        (at(3), date(2026, 11, 3), "green"),  # on schedule (loaded yesterday morning)
        (at(5, 59), date(2026, 11, 3), "green"),
        (at(3), date(2026, 11, 2), "amber"),  # one run missed
        (at(3), date(2026, 11, 1), "amber"),
        (at(3), date(2026, 10, 31), "red"),
    ],
)
def test_on_schedule_data_is_green_all_day(moment, latest, status):
    result = freshness(latest, ist_today(moment), run_done=pipeline_has_run(moment))
    assert result.status == status


def test_the_displayed_age_is_always_the_plain_age():
    """The grace only affects the colour; the tile still shows today minus the latest day."""
    result = freshness(date(2026, 11, 3), date(2026, 11, 5), run_done=False)
    assert (result.status, result.age_days) == ("green", 2)
