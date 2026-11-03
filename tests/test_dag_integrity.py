"""DAG integrity tests — requires airflow to be installed."""

import pytest

airflow = pytest.importorskip("airflow")

from airflow.dag_processing.dagbag import DagBag


def test_dag_loads_without_errors():
    bag = DagBag(dag_folder="dags")
    assert bag.import_errors == {}, f"DAG import errors: {bag.import_errors}"
    dag = bag.dags.get("weather_etl")
    assert dag is not None, "DAG 'weather_etl' not found"
    assert {t.task_id for t in dag.tasks} == {"extract", "validate", "load"}
    assert dag.catchup is True
    assert dag.max_active_runs == 1


def test_timetable_run_firing_on_oct_2_fetches_oct_1():
    """Verify that the run firing at 2026-10-02 00:30 UTC has data_interval_start = 2026-10-01."""
    from airflow.timetables.base import TimeRestriction
    from airflow.timetables.interval import CronDataIntervalTimetable as ServerTimetable
    from pendulum import datetime

    bag = DagBag(dag_folder="dags")
    dag = bag.dags.get("weather_etl")
    assert dag is not None

    client_tt = dag.timetable
    server_tt = ServerTimetable(client_tt.expression, client_tt.timezone)

    restriction = TimeRestriction(
        earliest=datetime(2026, 10, 1, 0, 30, tz="UTC"),
        latest=None,
        catchup=True,
    )

    info1 = server_tt.next_dagrun_info(
        last_automated_data_interval=None,
        restriction=restriction,
    )
    assert info1 is not None
    # Fires at 2026-10-02 00:30 UTC (data_interval_end is 2026-10-02 00:30 UTC)
    assert info1.run_after == datetime(2026, 10, 2, 0, 30, tz="UTC")
    assert info1.data_interval.end == datetime(2026, 10, 2, 0, 30, tz="UTC")
    # data_interval_start.date() is 2026-10-01 (yesterday's date)
    assert info1.data_interval.start.date() == datetime(2026, 10, 1).date()


