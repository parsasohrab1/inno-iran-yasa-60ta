"""Shift schedule. DEFAULTS ARE AN ASSUMPTION (3 x 8 h, Friday off) until the factory's real hours are confirmed.

Iran uses a fixed UTC+03:30 offset (daylight saving was abolished in 2022), so no tz database is needed.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
FRIDAY = 4  # datetime.weekday(): Monday=0 ... Friday=4


@dataclass(frozen=True)
class Shift:
    key: str
    name_fa: str
    start: time
    end: time  # end <= start means the shift ends after midnight

    def contains(self, t: time) -> bool:
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


@dataclass(frozen=True)
class ShiftInfo:
    key: str
    name_fa: str
    shift_date: str  # ISO date the shift STARTED on (an after-midnight tail belongs to the previous day)
    start: datetime
    end: datetime
    working: bool


DEFAULT_SHIFTS = [
    Shift("morning", "شیفت صبح", time(6, 0), time(14, 0)),
    Shift("afternoon", "شیفت عصر", time(14, 0), time(22, 0)),
    Shift("night", "شیفت شب", time(22, 0), time(6, 0)),
]


class ShiftSchedule:
    def __init__(self, shifts: list[Shift] | None = None, off_weekdays: tuple[int, ...] = (FRIDAY,),
                 assumed: bool = True):
        self.shifts = shifts or DEFAULT_SHIFTS
        self.off_weekdays = off_weekdays
        self.assumed = assumed  # surfaced in the API/UI so nobody mistakes the default for the real schedule

    def at(self, now: datetime) -> ShiftInfo:
        now = now.astimezone(IRAN_TZ)
        for s in self.shifts:
            if s.contains(now.time()):
                overnight = s.start >= s.end
                start_date = now.date() - timedelta(days=1) if (overnight and now.time() < s.end) else now.date()
                start = datetime.combine(start_date, s.start, IRAN_TZ)
                end_date = start_date + timedelta(days=1) if overnight else start_date
                end = datetime.combine(end_date, s.end, IRAN_TZ)
                return ShiftInfo(s.key, s.name_fa, start_date.isoformat(), start, end,
                                 start_date.weekday() not in self.off_weekdays)
        # gap between shifts (a schedule with holes)
        return ShiftInfo("none", "خارج از شیفت", now.date().isoformat(), now, now, False)

    @classmethod
    def from_json(cls, path: str) -> "ShiftSchedule":
        """Load the factory's real schedule, e.g.
        {"off_weekdays": [4], "shifts": [{"key": "day", "name_fa": "شیفت روز", "start": "07:30", "end": "16:00"}]}
        (weekday numbers: Monday=0 ... Friday=4, Saturday=5, Sunday=6). A loaded file is no longer flagged as assumed."""
        import json

        d = json.load(open(path, encoding="utf-8"))

        def t(x: str) -> time:
            h, m = x.split(":")
            return time(int(h), int(m))

        shifts = [Shift(x["key"], x["name_fa"], t(x["start"]), t(x["end"])) for x in d["shifts"]]
        return cls(shifts, tuple(d.get("off_weekdays", [FRIDAY])), assumed=False)

    def describe(self) -> dict:
        return {"assumed": self.assumed, "off_weekdays": list(self.off_weekdays),
                "shifts": [{"key": s.key, "name_fa": s.name_fa, "start": s.start.strftime("%H:%M"),
                            "end": s.end.strftime("%H:%M")} for s in self.shifts]}
