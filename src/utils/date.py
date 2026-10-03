import datetime
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_date(value: str | datetime.date) -> str:
    """Coerce to ``YYYY-MM-DD``, rejecting malformed or impossible dates."""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if not DATE_RE.match(value):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"date must be a real calendar date, got {value!r}") from None
    return value
