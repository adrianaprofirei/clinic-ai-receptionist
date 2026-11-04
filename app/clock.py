"""Time source for the whole app.

Bookings are stored as naive *clinic-local* wall-clock datetimes (opening hours
are defined in local time, and Romania switches between EET and EEST), while
audit timestamps such as `created_at` are naive UTC. Injecting the clock keeps
"now" testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class Clock:
    def __init__(self, tz_name: str = "Europe/Bucharest") -> None:
        self.tz = ZoneInfo(tz_name)

    def now_utc(self) -> datetime:
        """Timezone-aware current UTC time."""
        return datetime.now(timezone.utc)

    def utc_naive(self) -> datetime:
        """Current UTC time without tzinfo, as stored in the database."""
        return self.now_utc().replace(tzinfo=None)

    def now_local(self) -> datetime:
        """Current clinic-local wall-clock time, without tzinfo."""
        return self.now_utc().astimezone(self.tz).replace(tzinfo=None)

    def local_to_utc_naive(self, local: datetime) -> datetime:
        return local.replace(tzinfo=self.tz).astimezone(timezone.utc).replace(tzinfo=None)

    def utc_naive_to_local(self, utc: datetime) -> datetime:
        return utc.replace(tzinfo=timezone.utc).astimezone(self.tz).replace(tzinfo=None)


class FixedClock(Clock):
    """A clock frozen at a given moment (used by tests and screenshots)."""

    def __init__(self, local: datetime, tz_name: str = "Europe/Bucharest") -> None:
        super().__init__(tz_name)
        self._utc = local.replace(tzinfo=self.tz).astimezone(timezone.utc)

    def now_utc(self) -> datetime:
        return self._utc

    def set_local(self, local: datetime) -> None:
        self._utc = local.replace(tzinfo=self.tz).astimezone(timezone.utc)

    def advance(self, **delta: float) -> None:
        self._utc = self._utc + timedelta(**delta)
