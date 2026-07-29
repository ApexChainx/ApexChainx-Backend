import csv
import io
import json
from collections.abc import Iterable

from app.models.outage import Outage


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
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
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
        ],
    )
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
