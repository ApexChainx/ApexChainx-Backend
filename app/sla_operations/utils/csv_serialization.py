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


def serialize_value(value: Any, missing_value: str = "") -> str:
    """Convert a single cell value into a safe CSV string.

    ``None`` is mapped to ``missing_value`` (default ``""``); all other values
    are converted via :func:`str`. The returned string is not yet quoted --
    quoting/escaping is handled by the stdlib :mod:`csv` module when the value
    is written to a row, so commas, quotes, and newlines are preserved safely.
    """
    if value is None:
        return missing_value
    return str(value)


def serialize_records(
    records: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str] | None = None,
    *,
    include_header: bool = True,
    missing_value: str = "",
) -> str:
    """Serialize an iterable of mapping records into a CSV string.

    Parameters
    ----------
    records:
        Iterable of mapping rows. It is materialized once so generators are
        supported and the first record can be inspected for field derivation.
    fieldnames:
        Ordered column names to emit. When ``None`` (default) the fieldnames
        are derived from the keys of the first record, preserving its order.
    include_header:
        When ``True`` (default) a header row is written; when ``False`` only
        data rows are emitted.
    missing_value:
        Value substituted for keys absent from a record (and for ``None``
        cell values via :func:`serialize_value`).

    Returns
    -------
    str
        The serialized CSV text, or ``""`` when ``records`` is empty.

    Edge behavior
    -------------
    - Empty input yields ``""`` regardless of ``include_header``.
    - Values containing commas, quotes, or newlines are quoted/escaped by the
      stdlib :mod:`csv` module.
    - Extra keys not present in ``fieldnames`` are ignored.
    """
    materialized = list(records)
    if not materialized:
        return ""

    if fieldnames is None:
        fieldnames = list(materialized[0].keys())
    columns = list(fieldnames)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if include_header:
        writer.writerow(columns)
    for record in materialized:
        writer.writerow(
            [
                serialize_value(record.get(name), missing_value=missing_value)
                for name in columns
            ]
        )
    return buffer.getvalue()


def records_to_csv(
    records: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str] | None = None,
    *,
    include_header: bool = True,
    missing_value: str = "",
) -> str:
    """Alias for :func:`serialize_records` with identical behavior."""
    return serialize_records(
        records,
        fieldnames=fieldnames,
        include_header=include_header,
        missing_value=missing_value,
    )
</br>