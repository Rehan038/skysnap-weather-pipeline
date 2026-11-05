"""Audit tests for the ETL pipeline: DAG date window, validation, extract, load.

Tests assert the CORRECT behaviour; known defects are marked xfail(strict) with a FINDING id.
"""

import importlib.util
import sys
import types
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import responses as responses_lib
from hypothesis import given, settings
from hypothesis import strategies as st

from weather_etl import config
from weather_etl.config import API_URL, CITIES
from weather_etl.extract import fetch_hourly
from weather_etl.load import insert_rejects, upsert_weather
from weather_etl.validate import DataQualityError, check_reject_rate, split_rows

DAG_FILE = Path(__file__).resolve().parents[1] / "dags" / "weather_etl.py"

# --------------------------------------------------------------------------------------
# DAG date window (airflow is stubbed, so this runs without airflow installed)
# --------------------------------------------------------------------------------------


class _FakeTask:
    def __init__(self, fn):
        self.function = fn

    def expand(self, **kwargs):
        return ("expanded", self.function.__name__, kwargs)

    def __call__(self, *args, **kwargs):
        return ("called", self.function.__name__)


class _FakeCron:
    def __init__(self, expression, timezone):
        self.expression = expression
        self.timezone = timezone


def _fake_dag(**kwargs):
    def deco(fn):
        def run():
            return fn()

        run.dag_kwargs = kwargs
        return run

    return deco


@pytest.fixture(autouse=True)
def dag_module(monkeypatch):
    sdk = types.ModuleType("airflow.sdk")
    sdk.CronDataIntervalTimetable = _FakeCron
    sdk.dag = _fake_dag
    sdk.task = lambda fn: _FakeTask(fn)
    pkg = types.ModuleType("airflow")
    pkg.sdk = sdk
    monkeypatch.setitem(sys.modules, "airflow", pkg)
    monkeypatch.setitem(sys.modules, "airflow.sdk", sdk)
    spec = importlib.util.spec_from_file_location("audit_dag_under_test", DAG_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schedule(dag_module):
    """Return the timetable the DAG was declared with (captured by the stub)."""
    captured = {}

    def capture(**kw):
        captured.update(kw)
        return lambda fn: fn

    sdk = sys.modules["airflow.sdk"]
    original = sdk.dag
    sdk.dag = capture
    try:
        spec = importlib.util.spec_from_file_location("audit_dag_probe", DAG_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sdk.dag = original
    return captured


def _data_interval_start(fire: datetime) -> datetime:
    """Daily cron '30 0 * * *' UTC: the run firing at `fire` covers [fire-1d, fire)."""
    return fire - timedelta(days=1)


def test_dag_schedule_is_daily_0030_utc(dag_module):
    kw = _schedule(dag_module)
    tt = kw["schedule"]
    assert tt.expression == "30 0 * * *"
    assert tt.timezone == "UTC"
    assert kw["catchup"] is True
    assert kw["max_active_runs"] == 1


@pytest.mark.parametrize(
    ("fire", "expected_day"),
    [
        (datetime(2026, 10, 2, 0, 30, tzinfo=UTC), date(2026, 10, 1)),
        (datetime(2026, 11, 1, 0, 30, tzinfo=UTC), date(2026, 10, 31)),  # month boundary
        (datetime(2027, 1, 1, 0, 30, tzinfo=UTC), date(2026, 12, 31)),  # year boundary
    ],
)
@responses_lib.activate
def test_extract_task_fetches_previous_day(dag_module, fire, expected_day):
    responses_lib.add(responses_lib.GET, API_URL, json={"hourly": {"time": []}}, status=200)
    seen, _ = _capture_tasks()
    seen["extract"].function(CITIES[0], data_interval_start=_data_interval_start(fire))
    qs = responses_lib.calls[0].request.params
    assert qs["start_date"] == expected_day.isoformat()
    assert qs["end_date"] == expected_day.isoformat()


def _capture_tasks():
    seen = {}
    original = _FakeTask.__init__

    def spy(self, fn):
        original(self, fn)
        seen[fn.__name__] = self

    _FakeTask.__init__ = spy
    try:
        spec = importlib.util.spec_from_file_location("audit_dag_spy2", DAG_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        _FakeTask.__init__ = original
    return seen, module


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

DAY = date(2026, 10, 1)


def good_row(city="Bengaluru", hour=0, day=DAY, **overrides):
    base = {
        "city": city,
        "observed_at": f"{day.isoformat()}T{hour:02d}:00",
        "temperature_c": 25.0,
        "humidity_pct": 60.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 10.0,
        "weather_code": 3,
    }
    base.update(overrides)
    return base


def full_day(day=DAY):
    return [good_row(c["name"], h, day) for c in CITIES for h in range(24)]


# --------------------------------------------------------------------------------------
# Validation (hypothesis)
# --------------------------------------------------------------------------------------

finite = {"allow_nan": False, "allow_infinity": False}
temps = st.floats(config.TEMP_MIN, config.TEMP_MAX, **finite)
hums = st.floats(config.HUMIDITY_MIN, config.HUMIDITY_MAX, **finite)
nonneg = st.floats(0.0, 1e4, **finite)


@settings(max_examples=200, deadline=None)
@given(t=temps, h=hums, p=nonneg, w=nonneg, code=st.one_of(st.none(), st.integers(0, 99)))
def test_in_limit_rows_always_accepted(t, h, p, w, code):
    row = good_row(temperature_c=t, humidity_pct=h, precipitation_mm=p, wind_kmh=w,
                   weather_code=code)
    good, rejects = split_rows([row])
    assert good == [row]
    assert rejects == []


@settings(max_examples=200, deadline=None)
@given(t=st.one_of(
    st.floats(max_value=config.TEMP_MIN, exclude_max=True, **finite),
    st.floats(min_value=config.TEMP_MAX, exclude_min=True, **finite),
))
def test_temperature_out_of_range_rejected_with_reason(t):
    good, rejects = split_rows([good_row(temperature_c=t)])
    assert good == []
    assert rejects[0]["reason"] == "temperature_c out of range"


@settings(max_examples=200, deadline=None)
@given(h=st.one_of(
    st.floats(max_value=config.HUMIDITY_MIN, exclude_max=True, **finite),
    st.floats(min_value=config.HUMIDITY_MAX, exclude_min=True, **finite),
))
def test_humidity_out_of_range_rejected_with_reason(h):
    good, rejects = split_rows([good_row(humidity_pct=h)])
    assert good == []
    assert rejects[0]["reason"] == "humidity_pct out of range"


@settings(max_examples=100, deadline=None)
@given(p=st.floats(max_value=0.0, exclude_max=True, **finite))
def test_negative_precipitation_rejected(p):
    _, rejects = split_rows([good_row(precipitation_mm=p)])
    assert rejects[0]["reason"] == "negative precipitation_mm"


@settings(max_examples=100, deadline=None)
@given(w=st.floats(max_value=0.0, exclude_max=True, **finite))
def test_negative_wind_rejected(w):
    _, rejects = split_rows([good_row(wind_kmh=w)])
    assert rejects[0]["reason"] == "negative wind_kmh"


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature_c": config.TEMP_MIN},
        {"temperature_c": config.TEMP_MAX},
        {"humidity_pct": config.HUMIDITY_MIN},
        {"humidity_pct": config.HUMIDITY_MAX},
        {"precipitation_mm": 0.0},
        {"wind_kmh": 0.0},
    ],
)
def test_limits_are_inclusive(overrides):
    good, rejects = split_rows([good_row(**overrides)])
    assert len(good) == 1 and not rejects


@pytest.mark.parametrize("field", ["temperature_c", "humidity_pct", "precipitation_mm", "wind_kmh"])
def test_null_measured_field_rejected(field):
    _, rejects = split_rows([good_row(**{field: None})])
    assert rejects[0]["reason"] == f"null {field}"


@settings(max_examples=50, deadline=None)
@given(k=st.integers(1, 40))
def test_reject_rate_exactly_ten_percent_passes_and_just_above_fails(k):
    total = 10 * k
    check_reject_rate([1] * (total - k), [1] * k)  # exactly 10%: ok
    with pytest.raises(DataQualityError):
        check_reject_rate([1] * (total - k - 1), [1] * (k + 1))  # just above


@pytest.mark.parametrize(("rejects", "ok"), [(12, True), (13, False), (0, True), (120, False)])
def test_reject_rate_on_a_full_day_of_120_rows(rejects, ok):
    rows = full_day()
    for r in rows[:rejects]:
        r["temperature_c"] = 99.0
    good, bad = split_rows(rows)
    assert len(bad) == rejects
    if ok:
        check_reject_rate(good, bad)
    else:
        with pytest.raises(DataQualityError):
            check_reject_rate(good, bad)


def test_empty_input_fails_loudly():
    with pytest.raises(DataQualityError):
        check_reject_rate([], [])


NONFINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("bad", NONFINITE, ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("field", ["temperature_c", "humidity_pct"])
def test_nonfinite_in_ranged_fields_rejected(field, bad):
    """NaN/inf compare False against the range, so these are rejected today."""
    good, rejects = split_rows([good_row(**{field: bad})])
    assert good == []
    assert rejects[0]["reason"] == f"{field} out of range"


@pytest.mark.xfail(strict=True, reason="FINDING A-01")
@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
@pytest.mark.parametrize("field", ["precipitation_mm", "wind_kmh"])
def test_nonfinite_in_precip_and_wind_must_be_rejected(field, bad):
    """`x < 0` is False for NaN/+inf, so they are accepted into the 'good' set."""
    good, rejects = split_rows([good_row(**{field: bad})])
    assert good == [] and len(rejects) == 1


@pytest.mark.parametrize("field", ["precipitation_mm", "wind_kmh"])
def test_negative_inf_in_precip_and_wind_rejected(field):
    good, rejects = split_rows([good_row(**{field: float("-inf")})])
    assert good == [] and len(rejects) == 1


# --------------------------------------------------------------------------------------
# Extract
# --------------------------------------------------------------------------------------

BENGALURU = CITIES[0]
TIMES = [f"{DAY.isoformat()}T{h:02d}:00" for h in range(24)]


def payload(n_time=24, n_temp=24, times=None, **extra):
    times = times if times is not None else TIMES[:n_time]
    hourly = {
        "time": times,
        "temperature_2m": [21.0 + i for i in range(n_temp)],
        "relative_humidity_2m": [50.0] * len(times),
        "precipitation": [0.0] * len(times),
        "wind_speed_10m": [5.0] * len(times),
        "weather_code": [1] * len(times),
    }
    hourly.update(extra)
    return {"hourly": hourly}


def fetch(body):
    with responses_lib.RequestsMock() as rsps:
        rsps.add(responses_lib.GET, API_URL, json=body, status=200)
        return fetch_hourly(BENGALURU, DAY)


def test_extract_happy_path_is_aligned():
    rows = fetch(payload())
    assert len(rows) == 24
    assert [r["temperature_c"] for r in rows] == [21.0 + i for i in range(24)]


def test_extract_short_temperature_array_never_misaligns():
    """time=24, temperature=23: the missing hour must be None (-> reject), not shifted."""
    rows = fetch(payload(n_time=24, n_temp=23))
    assert [r["temperature_c"] for r in rows[:23]] == [21.0 + i for i in range(23)]
    assert rows[23]["temperature_c"] is None
    _good, rejects = split_rows(rows)
    assert len(rejects) == 1 and rejects[0]["reason"] == "null temperature_c"


def test_extract_missing_variable_makes_every_row_rejectable():
    body = payload()
    del body["hourly"]["wind_speed_10m"]
    good, rejects = split_rows(fetch(body))
    assert good == []
    with pytest.raises(DataQualityError):
        check_reject_rate(good, rejects)


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_long_temperature_array_is_an_error():
    """time=24, temperature=25: payload is internally inconsistent; do not silently trim."""
    with pytest.raises((RuntimeError, ValueError)):
        fetch(payload(n_time=24, n_temp=25))


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_fewer_than_24_hours_is_an_error():
    with pytest.raises((RuntimeError, ValueError)):
        fetch(payload(n_time=23, n_temp=23))


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_more_than_24_hours_is_an_error():
    times = [*TIMES, f"{DAY.isoformat()}T24:00"]
    with pytest.raises((RuntimeError, ValueError)):
        fetch(payload(times=times, n_temp=25))


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_duplicate_timestamps_is_an_error():
    times = [*TIMES[:23], TIMES[0]]
    with pytest.raises((RuntimeError, ValueError)):
        fetch(payload(times=times))


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_empty_hourly_is_an_error():
    """A city with no data at all must not silently vanish from the day's load."""
    with pytest.raises((RuntimeError, ValueError)):
        fetch({"hourly": {"time": []}})


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_missing_hourly_key_is_an_error():
    with pytest.raises((RuntimeError, ValueError)):
        fetch({})


@pytest.mark.xfail(strict=True, reason="FINDING A-02")
def test_extract_timestamps_for_wrong_day_is_an_error():
    other = date(2026, 9, 30)
    times = [f"{other.isoformat()}T{h:02d}:00" for h in range(24)]
    with pytest.raises((RuntimeError, ValueError)):
        fetch(payload(times=times))


# --------------------------------------------------------------------------------------
# Load (needs weather_test via the `db` fixture)
# --------------------------------------------------------------------------------------


class _KeepOpen:
    """Connection proxy: lets the DAG load task 'close' without closing the fixture's conn."""

    def __init__(self, conn):
        self._conn = conn
        self.rollbacks = 0

    def close(self):
        pass

    def rollback(self):
        self.rollbacks += 1
        self._conn.rollback()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _count(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def run_load_task(db, monkeypatch, good, rejects, run_date=DAY):
    seen, module = _capture_tasks()
    proxy = _KeepOpen(db)
    monkeypatch.setattr(module, "get_connection", lambda: proxy)
    start = datetime(run_date.year, run_date.month, run_date.day, 0, 30, tzinfo=UTC)
    seen["load"].function({"good": good, "rejects": rejects}, data_interval_start=start)
    return proxy, module


def test_loading_same_day_three_times_is_idempotent(db, monkeypatch):
    rows = full_day()
    rows[5]["temperature_c"] = 99.0  # one reject
    good, rejects = split_rows(rows)
    for i in range(3):
        for r in good:
            r["temperature_c"] = min(r["temperature_c"], 30.0) + (0.5 if i == 2 else 0.0)
        run_load_task(db, monkeypatch, good, rejects)
        assert _count(db, "SELECT COUNT(*) FROM weather_hourly") == 119
        assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 1
    # latest values won
    assert _count(
        db, "SELECT COUNT(*) FROM weather_hourly WHERE temperature_c = 25.5"
    ) == 119


def test_rejects_for_run_date_are_replaced_not_appended(db, monkeypatch):
    rows = full_day()
    for r in rows[:3]:
        r["wind_kmh"] = -1.0
    good, rejects = split_rows(rows)
    assert len(rejects) == 3
    run_load_task(db, monkeypatch, good, rejects)
    run_load_task(db, monkeypatch, good, rejects)
    assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 3
    # a rerun in which fewer rows are bad shrinks the reject set
    rows2 = full_day()
    rows2[0]["wind_kmh"] = -1.0
    good2, rejects2 = split_rows(rows2)
    run_load_task(db, monkeypatch, good2, rejects2)
    assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 1


def test_rejects_of_other_run_dates_are_preserved(db, monkeypatch):
    bad = [{"row": good_row(wind_kmh=-1), "reason": "negative wind_kmh"}]
    run_load_task(db, monkeypatch, [], bad, run_date=date(2026, 10, 1))
    run_load_task(db, monkeypatch, [], bad, run_date=date(2026, 10, 2))
    assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 2


@pytest.mark.xfail(strict=True, reason="FINDING A-03")
def test_rerun_where_hour_becomes_invalid_removes_stale_good_row(db, monkeypatch):
    """Run 1 loads hour 5 as good; run 2 (same day) rejects it. It must not stay in
    weather_hourly as if it were valid."""
    rows = full_day()
    good, rejects = split_rows(rows)
    run_load_task(db, monkeypatch, good, rejects)
    rows2 = full_day()
    rows2[5]["temperature_c"] = 99.0
    good2, rejects2 = split_rows(rows2)
    run_load_task(db, monkeypatch, good2, rejects2)
    assert _count(db, "SELECT COUNT(*) FROM weather_hourly") == 119


def test_failure_after_upsert_rolls_everything_back(db, monkeypatch):
    seen, module = _capture_tasks()
    proxy = _KeepOpen(db)
    monkeypatch.setattr(module, "get_connection", lambda: proxy)

    def boom(*a, **k):
        raise RuntimeError("reject insert failed")

    monkeypatch.setattr(module, "insert_rejects", boom)
    good, rejects = split_rows(full_day())
    start = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    with pytest.raises(RuntimeError):
        seen["load"].function({"good": good, "rejects": rejects}, data_interval_start=start)
    assert proxy.rollbacks == 1
    assert _count(db, "SELECT COUNT(*) FROM weather_hourly") == 0


def test_failure_midway_through_executemany_leaves_nothing(db, monkeypatch):
    """A row MySQL refuses (weather_code overflows SMALLINT) after valid rows."""
    rows = full_day()
    rows[100]["weather_code"] = 10**9
    good, rejects = split_rows(rows)
    with pytest.raises(Exception):  # noqa: B017 - pymysql DataError/OperationalError
        run_load_task(db, monkeypatch, good, rejects)
    assert _count(db, "SELECT COUNT(*) FROM weather_hourly") == 0
    assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 0


def test_unknown_city_midbatch_writes_nothing(db):
    rows = [good_row(hour=0), good_row(hour=1), good_row("Atlantis", 2)]
    with pytest.raises(ValueError, match="unknown city: Atlantis"):
        upsert_weather(db, rows, DAY)
    db.commit()
    assert _count(db, "SELECT COUNT(*) FROM weather_hourly") == 0


@pytest.mark.xfail(strict=True, reason="FINDING A-01")
def test_rejected_nan_row_can_be_stored_in_reject_table(db):
    """A NaN reading is rejected by validation; insert_rejects must then be able to store it
    (json.dumps emits the invalid-JSON token NaN, which the JSON column refuses)."""
    row = good_row(temperature_c=float("nan"))
    _good, rejects = split_rows([row])
    assert len(rejects) == 1
    insert_rejects(db, rejects, DAY)
    db.commit()
    assert _count(db, "SELECT COUNT(*) FROM weather_rejects") == 1


@pytest.mark.xfail(strict=True, reason="FINDING A-04")
@pytest.mark.parametrize("field", ["precipitation_mm", "wind_kmh"])
def test_absurd_precip_or_wind_is_rejected_not_a_load_crash(field):
    """Columns are DECIMAL(6,2) (max 9999.99) but validation has no upper bound: a bad value
    passes validation and then aborts the whole day's load."""
    good, rejects = split_rows([good_row(**{field: 123456.0})])
    assert good == [] and len(rejects) == 1


def test_nan_is_not_silently_stored_as_a_value(db):
    """Whatever validation does with NaN precipitation, the DB must never hold a NaN."""
    good, _ = split_rows([good_row(precipitation_mm=float("nan"))])
    try:
        upsert_weather(db, good, DAY)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    assert _count(db, "SELECT COUNT(*) FROM weather_hourly WHERE precipitation_mm IS NULL") == 0
