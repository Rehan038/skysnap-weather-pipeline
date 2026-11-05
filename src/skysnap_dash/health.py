"""Freshness rules for the pipeline health tiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

EXPECTED_ROWS_PER_DAY = 120
_IST = ZoneInfo("Asia/Kolkata")
# The DAG fires at 00:30 UTC = 06:00 IST and loads the previous day.
PIPELINE_RUN_IST = time(6, 0)


@dataclass(frozen=True)
class Freshness:
    status: Literal["green", "amber", "red"]
    age_days: int | None
    label: str


def ist_today(now: datetime | None = None) -> date:
    moment = now if now is not None else datetime.now(_IST)
    return moment.astimezone(_IST).date()


def pipeline_has_run(now: datetime | None = None) -> bool:
    """True once today's scheduled run (06:00 IST) is due, so yesterday's data should be loaded."""
    moment = now if now is not None else datetime.now(_IST)
    return moment.astimezone(_IST).time() >= PIPELINE_RUN_IST


def freshness(latest: date | None, today: date, run_done: bool = True) -> Freshness:
    """Colour the data by age: 0-1 days green, 2-3 amber, more than 3 red; no data is red.

    The newest day is normally yesterday's, loaded at 06:00 IST. Until that run has happened
    (``run_done`` False) today's load is not due yet, so age is measured from yesterday and
    on-schedule data stays green overnight. ``age_days`` is always the plain age (today minus
    the latest day) that the dashboard displays.
    """
    if latest is None:
        return Freshness("red", None, "No data")
    age = max((today - latest).days, 0)
    expected_today = today if run_done else today - timedelta(days=1)
    lag = max((expected_today - latest).days, 0)
    if lag <= 1:
        return Freshness("green", age, "Up to date")
    if lag <= 3:
        return Freshness("amber", age, "Slightly behind")
    return Freshness("red", age, "Stale")
