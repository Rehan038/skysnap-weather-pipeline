"""DAG integrity tests — requires airflow to be installed."""

import pytest

airflow = pytest.importorskip("airflow")

from airflow.models import DagBag


def test_dag_loads_without_errors():
    bag = DagBag(dag_folder="dags", include_examples=False)
    assert bag.import_errors == {}, f"DAG import errors: {bag.import_errors}"
    dag = bag.get_dag("weather_etl")
    assert dag is not None, "DAG 'weather_etl' not found"
    assert {t.task_id for t in dag.tasks} == {"extract", "validate", "load"}
    assert dag.catchup is True
    assert dag.max_active_runs == 1
