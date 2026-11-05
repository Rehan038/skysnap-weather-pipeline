"""Freshness rules for the pipeline health tiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

EXPECTED_ROWS_PER_DAY = 120
_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Freshness:
    status: Literal["green", "amber", "red"]
    age_days: int | None
    label: str


def ist_today(now: datetime | None = None) -> date:
    moment = now if now is not None else datetime.now(_IST)
    return moment.astimezone(_IST).date()


def freshness(latest: date | None, today: date) -> Freshness:
    if latest is None:
        return Freshness("red", None, "No data")
    age = max((today - latest).days, 0)
    # The DAG fires at 00:30 UTC (06:00 IST) and loads the previous day, so the newest day is
    # normally yesterday, and the day before until that morning's run. Judge freshness by how
    # far behind that schedule the data is (age measured against yesterday), so on-schedule
    # data is green all day. `age_days` stays the plain age shown on the dashboard.
    behind = max(age - 1, 0)
    if behind <= 1:
        return Freshness("green", age, "Up to date")
    if behind <= 3:
        return Freshness("amber", age, "Slightly behind")
    return Freshness("red", age, "Stale")
