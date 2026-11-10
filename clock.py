"""A simulated, injectable clock. The demo and tests install one so the lifecycle (reminders, stale,
timestamps) is deterministic. Everything times off database.now()/now_dt(), which read this when installed.
"""
import datetime as dt
import database as db


def _aware(when):
    if isinstance(when, str):
        when = dt.datetime.fromisoformat(when)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when


class SimClock:
    def __init__(self, start):
        self.t = _aware(start)

    def __call__(self):
        return self.t

    def set(self, when):
        self.t = _aware(when)
        return self.t

    def advance(self, days=0, hours=0, minutes=0, seconds=0):
        self.t += dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return self.t


def install(clock):
    db.set_clock(clock)
    return clock


def uninstall():
    db.set_clock(None)


def at(when):
    """Install and return a SimClock set to `when`."""
    return install(SimClock(when))
