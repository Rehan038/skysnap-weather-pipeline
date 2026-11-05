from datetime import date

from skysnap_dash.filters import city_error, default_range, parse_range, range_error


def test_default_range_is_30_days_inclusive():
    assert default_range(date(2026, 1, 1), date(2026, 11, 2)) == (
        date(2026, 10, 4),
        date(2026, 11, 2),
    )


def test_default_range_clamps_to_min_day():
    assert default_range(date(2026, 10, 29), date(2026, 11, 2)) == (
        date(2026, 10, 29),
        date(2026, 11, 2),
    )


def test_parse_range_two_dates():
    a, b = date(2026, 10, 1), date(2026, 10, 5)
    assert parse_range((a, b)) == (a, b)
    assert parse_range([a, b]) == (a, b)


def test_parse_range_single_date_is_none():
    d = date(2026, 10, 1)
    assert parse_range(d) is None
    assert parse_range((d,)) is None


def test_parse_range_empty_is_none():
    assert parse_range(()) is None
    assert parse_range([]) is None


def test_city_error():
    assert city_error([]) == "Select at least one city"
    assert city_error(["Delhi"]) is None


def test_range_error_only_when_start_after_end():
    a, b = date(2026, 10, 1), date(2026, 10, 5)
    assert range_error(b, a) == "Start date must be on or before end date"
    assert range_error(a, b) is None
    assert range_error(a, a) is None
