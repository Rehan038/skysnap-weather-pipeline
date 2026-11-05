from datetime import UTC, date, datetime, timedelta

import pytest

from skysnap_dash.health import EXPECTED_ROWS_PER_DAY, Freshness, freshness, ist_today

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
