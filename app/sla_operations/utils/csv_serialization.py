from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def serialize_rows(rows: Iterable[Sequence[Any]]) -> str:
    """Serialize an iterable of row sequences into a CSV string.

    Uses the standard :mod:`csv` module so that values containing commas,
    quotes, or newlines are properly quoted/escaped. Returns an empty string
    when there are no rows to serialize.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    wrote_any = False
    for row in rows:
        writer.writerow(row)
        wrote_any = True
    if not wrote_any:
        return ""
    return buffer.getvalue()


def serialize_dict_rows(
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str] | None = None,
) -> str:
    """Serialize an iterable of mapping rows into a CSV string.

    The header is derived from ``fieldnames`` when provided, otherwise from the
    keys of the first row encountered. Values are written using the standard
    :mod:`csv` module for proper quoting/escaping. Returns an empty string when
    there are no rows to serialize.
    """
    materialized = list(rows)
    if not materialized:
        return ""

    if fieldnames is None:
        fieldnames = list(materialized[0].keys())

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fieldnames))
    writer.writeheader()
    for row in materialized:
        writer.writerow({name: row.get(name, "") for name in fieldnames})
    return buffer.getvalue()
</br>