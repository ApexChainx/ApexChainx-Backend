import csv
import io

from app.models.enums import OutageStatus
from app.services.sla import calculate_sla


def export_outages(outages: list, format: str):
    if format == "json":
        items = []
        for outage in outages:
            entry = outage.dict()

            if outage.status == OutageStatus.resolved:
                entry["sla"] = calculate_sla(
                    outage_id=outage.id,
                    severity=outage.severity.value,
                    mttr_minutes=outage.mttr_minutes,
                )

            items.append(entry)

        return items

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(
            [
                "id",
                "service",
                "severity",
                "status",
                "started_at",
                "mttr_minutes",
                "sla_status",
                "sla_amount",
            ]
        )

        for outage in outages:
            sla_status = ""
            sla_amount = ""

            if outage.status == OutageStatus.resolved:
                sla = calculate_sla(
                    outage_id=outage.id,
                    severity=outage.severity.value,
                    mttr_minutes=outage.mttr_minutes,
                )
                sla_status = sla["status"]
                sla_amount = sla["amount"]

            writer.writerow(
                [
                    outage.id,
                    outage.service,
                    outage.severity.value,
                    outage.status.value,
                    outage.started_at,
                    outage.mttr_minutes,
                    sla_status,
                    sla_amount,
                ]
            )

        return output.getvalue()

    raise ValueError("Invalid format")
