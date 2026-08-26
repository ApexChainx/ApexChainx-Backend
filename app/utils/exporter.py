import csv
import io
import json
from collections.abc import Generator, Iterable, Iterator

from app.models.outage import Outage

CSV_FIELDNAMES = [
    "id",
    "site_name",
    "site_id",
    "severity",
    "status",
    "detected_at",
    "resolved_at",
    "description",
    "affected_services",
    "affected_subscribers",
    "assigned_to",
    "created_by",
    "location",
    "sla_status",
]


def _serialize_outage(outage: Outage) -> dict:
    return outage.model_dump(mode="json")


def export_outages(outages: Iterable[Outage], format: str = "json"):
    format = format.lower()
    rows = [_serialize_outage(outage) for outage in outages]

    if format == "json":
        return rows

    if format != "csv":
        raise ValueError("Unsupported export format. Use 'json' or 'csv'.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()

    for row in rows:
        writer.writerow(
            {
                **row,
                "affected_services": json.dumps(row.get("affected_services", [])),
                "location": json.dumps(row.get("location")),
                "sla_status": json.dumps(row.get("sla_status")),
            }
        )

    return buffer.getvalue()


def stream_export_csv(outages: Iterator[Outage]) -> Generator[str, None, None]:
    """Stream CSV rows one at a time for memory-efficient large exports."""
    # Yield header
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    yield buf.getvalue()
    # Yield rows
    for outage in outages:
        row = _serialize_outage(outage)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDNAMES)
        writer.writerow(
            {
                **row,
                "affected_services": json.dumps(row.get("affected_services", [])),
                "location": json.dumps(row.get("location")),
                "sla_status": json.dumps(row.get("sla_status")),
            }
        )
        yield buf.getvalue()


def stream_export_json(outages: Iterator[Outage]) -> Generator[str, None, None]:
    """Stream JSON array rows one at a time for memory-efficient large exports."""
    yield "["
    first = True
    for outage in outages:
        row = _serialize_outage(outage)
        if not first:
            yield ","
        yield json.dumps(row, default=str)
        first = False
    yield "]"
