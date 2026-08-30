"""Conservative English spoken-text normalisation for TTS providers."""

import re
from datetime import datetime
from decimal import Decimal

from num2words import num2words


_NUMBER_TOKEN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CURRENCY_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"

_INITIALISMS = {
    "API": "A. P. I.",
    "CPU": "C. P. U.",
    "GPU": "G. P. U.",
    "RAM": "ram",
    "URL": "U. R. L.",
}
_INITIALISM_PATTERN = re.compile(
    r"(?<![\w./\\:\-?&=#%@])"
    r"(?:API|CPU|GPU|RAM|URL)"
    r"(?![\w/\\:\-?&=#%@]|\.\w)"
)

_CURRENCIES = {
    "\N{POUND SIGN}": ("pound", "pounds", "penny", "pence"),
    "$": ("dollar", "dollars", "cent", "cents"),
}
_CURRENCY_PATTERN = re.compile(
    r"(?<![\w/\\:\-?&=#%@,])"
    r"(?P<symbol>[\N{POUND SIGN}$])"
    rf"(?P<amount>{_CURRENCY_AMOUNT})\b"
)

_UNITS = {
    "GB": ("gigabyte", "gigabytes"),
    "MB": ("megabyte", "megabytes"),
    "GHz": ("gigahertz", "gigahertz"),
    "ms": ("millisecond", "milliseconds"),
}
_MEASUREMENT_PATTERN = re.compile(
    r"(?<![\w./\\:\-?&=#%@,])"
    rf"(?P<value>{_NUMBER_TOKEN})\s+"
    r"(?P<unit>GB|MB|GHz|ms)\b"
)

_PERCENTAGE_PATTERN = re.compile(
    r"(?<![\w./\\:\-?&=#%@,])"
    rf"(?P<value>{_NUMBER_TOKEN})\s*%"
    r"(?!\w)"
)

_TIME_PATTERN = re.compile(
    r"(?<![\w/\\:?&=#%@])"
    r"(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)"
    r"(?![:\d])"
)

_MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
_DATE_PATTERN = re.compile(
    rf"(?<!\w)(?P<day>0?[1-9]|[12]\d|3[01])\s+"
    rf"(?P<month>{_MONTHS})\s+"
    r"(?P<year>\d{4})(?![\w-]|\.\w)",
    re.IGNORECASE,
)

_NUMBER_PATTERN = re.compile(
    r"(?<![\w./\\:\-?&=#%@,])"
    rf"{_NUMBER_TOKEN}"
    r"(?![\w/\\:\-?&=#%@,]|\.\w)"
)


def _normalise_currency(match: re.Match[str]) -> str:
    amount = match.group("amount").replace(",", "")
    whole_text, _, fraction_text = amount.partition(".")
    whole = int(whole_text)
    fraction = int((fraction_text + "00")[:2])

    major_one, major_many, minor_one, minor_many = _CURRENCIES[
        match.group("symbol")
    ]
    parts = []

    if whole:
        major_unit = major_one if whole == 1 else major_many
        parts.append(f"{num2words(whole, lang='en')} {major_unit}")

    if fraction:
        minor_unit = minor_one if fraction == 1 else minor_many
        parts.append(f"{num2words(fraction, lang='en')} {minor_unit}")

    if not parts:
        parts.append(f"zero {major_many}")

    return " and ".join(parts)


def _normalise_measurement(match: re.Match[str]) -> str:
    value = match.group("value").replace(",", "")
    singular, plural = _UNITS[match.group("unit")]
    unit = singular if Decimal(value) == 1 else plural
    return f"{num2words(value, lang='en')} {unit}"


def _normalise_percentage(match: re.Match[str]) -> str:
    value = match.group("value").replace(",", "")
    return f"{num2words(value, lang='en')} percent"


def _normalise_time(match: re.Match[str]) -> str:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))

    if hour == 0 and minute == 0:
        return "midnight"
    if hour == 12 and minute == 0:
        return "noon"

    period = "A. M." if hour < 12 else "P. M."
    spoken_hour = num2words(hour % 12 or 12, lang="en")

    if minute == 0:
        return f"{spoken_hour} {period}"

    spoken_minute = num2words(minute, lang="en")
    if minute < 10:
        spoken_minute = f"oh {spoken_minute}"

    return f"{spoken_hour} {spoken_minute} {period}"


def _normalise_date(match: re.Match[str]) -> str:
    day = int(match.group("day"))
    month = match.group("month").title()
    year = int(match.group("year"))

    try:
        datetime.strptime(f"{day} {month} {year}", "%d %B %Y")
    except ValueError:
        return match.group()

    spoken_day = num2words(day, to="ordinal", lang="en")
    spoken_year = num2words(year, to="year", lang="en")
    return f"the {spoken_day} of {month}, {spoken_year}"


def _normalise_number(match: re.Match[str]) -> str:
    return num2words(match.group().replace(",", ""), lang="en")


def normalise_spoken_text(text: str) -> str:
    """Return an en-GB-oriented TTS copy without modifying the source."""
    spoken_text = _INITIALISM_PATTERN.sub(
        lambda match: _INITIALISMS[match.group()],
        text,
    )
    transformations = (
        (_DATE_PATTERN, _normalise_date),
        (_TIME_PATTERN, _normalise_time),
        (_CURRENCY_PATTERN, _normalise_currency),
        (_PERCENTAGE_PATTERN, _normalise_percentage),
        (_MEASUREMENT_PATTERN, _normalise_measurement),
        (_NUMBER_PATTERN, _normalise_number),
    )

    for pattern, replacement in transformations:
        spoken_text = pattern.sub(replacement, spoken_text)

    return spoken_text
