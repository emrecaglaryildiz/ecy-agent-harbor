"""Application civil time is Istanbul; persisted instants remain UTC."""
from datetime import datetime, time, timedelta, timezone

APP_TIMEZONE = "Europe/Istanbul"
try:
    from zoneinfo import ZoneInfo
    ISTANBUL = ZoneInfo(APP_TIMEZONE)
except (ImportError, KeyError):
    ISTANBUL = timezone(timedelta(hours=3), APP_TIMEZONE)


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def local_day(value):
    return instant(value).astimezone(ISTANBUL).date().isoformat()


def day_window(value):
    date = instant(value).astimezone(ISTANBUL).date()
    start = datetime.combine(date, time(), ISTANBUL)
    return date.isoformat(), start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def display_time(value):
    return instant(value).astimezone(ISTANBUL).isoformat(timespec="seconds")
