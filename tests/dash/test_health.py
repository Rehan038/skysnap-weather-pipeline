from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from skysnap_dash.health import EXPECTED_ROWS_PER_DAY, Freshness, freshness, ist_today

LATEST = date(2026, 11, 2)


@pytest.mark.parametrize(
    "age,status,label",
    [
        (0, "green", "Up to date"),
        (1, "green", "Up to date"),
        (2, "green", "Up to date"),  # the run that loads yesterday has not happened yet
        (3, "amber", "Slightly behind"),
        (4, "amber", "Slightly behind"),
        (5, "red", "Stale"),
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


@pytest.mark.parametrize(
    ("moment", "latest", "status"),
    [
        # The DAG fires 00:30 UTC = 06:00 IST and loads the previous day.
        (datetime(2026, 11, 5, 3, 0, tzinfo=IST), date(2026, 11, 3), "green"),  # before the run
        (datetime(2026, 11, 5, 5, 59, tzinfo=IST), date(2026, 11, 3), "green"),
        (datetime(2026, 11, 5, 6, 30, tzinfo=IST), date(2026, 11, 4), "green"),  # after the run
        (datetime(2026, 11, 5, 23, 59, tzinfo=IST), date(2026, 11, 4), "green"),
        (datetime(2026, 11, 5, 3, 0, tzinfo=IST), date(2026, 11, 2), "amber"),  # a run was missed
        (datetime(2026, 11, 5, 12, 0, tzinfo=IST), date(2026, 11, 2), "amber"),
        (datetime(2026, 11, 5, 12, 0, tzinfo=IST), date(2026, 10, 31), "red"),
    ],
)
def test_on_schedule_data_is_green_all_day(moment, latest, status):
    assert freshness(latest, ist_today(moment)).status == status
