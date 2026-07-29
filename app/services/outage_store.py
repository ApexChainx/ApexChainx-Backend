
import builtins

from app.models import Outage
from app.models.enums import OutageStatus, Severity


class OutageStore:
    """
    Deprecated in-memory store retained only as a lightweight compatibility layer.
    The active runtime path uses the SQLAlchemy-backed repository.
    """

    def __init__(self):
        self._data: dict[str, Outage] = {}

    def list(
        self,
        severity: Severity | None = None,
        status: OutageStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        items = list(self._data.values())

        if severity:
            items = [o for o in items if o.severity == severity.value]
        if status:
            items = [o for o in items if o.status == status.value]

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_all(self) -> builtins.list[Outage]:
        return list(self._data.values())

    def get(self, outage_id: str) -> Outage | None:
        return self._data.get(outage_id)

    def create(self, outage: Outage) -> Outage:
        self._data[outage.id] = outage
        return outage

    def bulk_create(self, outages: builtins.list[Outage]) -> builtins.list[Outage]:
        created = []
        for outage in outages:
            created.append(self.create(outage))
        return created

    def update(self, outage_id: str, outage: Outage) -> Outage:
        self._data[outage_id] = outage
        return outage

    def delete(self, outage_id: str) -> None:
        self._data.pop(outage_id, None)


outage_store = OutageStore()
