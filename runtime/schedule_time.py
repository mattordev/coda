"""Convert explicit local schedule times to UTC without guessing at DST."""

from datetime import UTC, datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleTimeError(ValueError):
    """A schedule time error whose message can be shown to the user."""


def schedule_timestamp(local_time: str, timezone: str) -> int:
    """Resolve a local date and time, using an explicit offset to disambiguate."""
    if not isinstance(local_time, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:[0-9]{2})?",
        local_time,
    ):
        raise ScheduleTimeError("Use an ISO date and time such as 2027-01-15T09:00.")
    if not isinstance(timezone, str) or not 1 <= len(timezone) <= 128:
        raise ScheduleTimeError("Supply an IANA timezone such as Europe/London.")
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError, OSError) as exc:
        raise ScheduleTimeError("That IANA timezone is unavailable.") from exc

    try:
        supplied = datetime.fromisoformat(local_time)
    except ValueError as exc:
        raise ScheduleTimeError("Check the calendar date, local time, and UTC offset.") from exc
    wall_time = supplied.replace(tzinfo=None)
    instants = set()
    try:
        for fold in (0, 1):
            candidate = wall_time.replace(tzinfo=zone, fold=fold)
            instant = candidate.astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) != wall_time:
                continue
            if supplied.tzinfo is None or supplied.utcoffset() == candidate.utcoffset():
                instants.add(instant)
    except OverflowError as exc:
        raise ScheduleTimeError("Schedule date is out of range.") from exc
    if not instants:
        raise ScheduleTimeError("That local time or UTC offset does not occur in the named timezone.")
    if len(instants) > 1:
        raise ScheduleTimeError("That local time occurs twice. Specify its UTC offset.")
    timestamp = int(instants.pop().timestamp())
    if timestamp < 0:
        raise ScheduleTimeError("Schedule date is out of range.")
    return timestamp
