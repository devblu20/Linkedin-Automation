from datetime import datetime, timedelta, timezone

import pytest

from linkedin_automation.infrastructure.browser.conversations import displayed_event_time

NOW = datetime(2026, 10, 31, 11, 20, tzinfo=timezone(timedelta(hours=5, minutes=30)))


class Times:
    def __init__(self, count):
        self.size = count

    def count(self):
        return self.size

    def inner_text(self):
        return "10:45 PM"


class Event:
    def __init__(self, label):
        self.label = label

    def locator(self, selector):
        return Times(0 if selector == "time[datetime]" else 1)

    def evaluate(self, script):
        return self.label


@pytest.mark.parametrize(
    "heading,day",
    [
        ("TODAY", 31),
        ("Yesterday", 30),
        ("FRIDAY", 30),
        ("Monday", 26),
        ("October 30, 2026", 30),
        ("Oct 30, 2026", 30),
        ("2026-10-30", 30),
    ],
)
def test_displayed_relative_weekday_and_full_dates_keep_exact_local_minute(heading, day):
    result = displayed_event_time(Event(heading), NOW)
    assert result == datetime(2026, 10, day, 22, 45, tzinfo=NOW.tzinfo)
    assert result.second == 0


@pytest.mark.parametrize("heading", ["Saturday", "October 30", "last week", "", None, "2026-11-01"])
def test_missing_ambiguous_or_future_dates_are_not_assigned_now(heading):
    with pytest.raises(ValueError):
        displayed_event_time(Event(heading), NOW)
