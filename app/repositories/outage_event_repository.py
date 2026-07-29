import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.orm.outage_event import CURRENT_SCHEMA_VERSION, OutageEventORM
from app.models.outage_event import validate_event_detail


class OutageEventRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(self, outage_id: str, event_type: str, detail: dict[str, Any] | None = None) -> OutageEventORM:
        detail = validate_event_detail(event_type, detail)
        orm = OutageEventORM(
            id=f"evt_{uuid4().hex[:12]}",
            outage_id=outage_id,
            event_type=event_type,
            detail=json.dumps(detail) if detail else None,
            schema_version=CURRENT_SCHEMA_VERSION,
            occurred_at=datetime.now(timezone.utc),
        )
        self.db.add(orm)
        self.db.commit()
        self.db.refresh(orm)
        return orm

    def list_for_outage(
        self,
        outage_id: str,
        event_type: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        query = self.db.query(OutageEventORM).filter(OutageEventORM.outage_id == outage_id)
        if event_type:
            query = query.filter(OutageEventORM.event_type == event_type)
        if start_date:
            query = query.filter(OutageEventORM.occurred_at >= start_date)
        if end_date:
            query = query.filter(OutageEventORM.occurred_at <= end_date)

        query = query.order_by(OutageEventORM.occurred_at.asc())
        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()

        return {
            "items": [
                {
                    "id": r.id,
                    "outage_id": r.outage_id,
                    "event_type": r.event_type,
                    "schema_version": r.schema_version,
                    "detail": json.loads(r.detail) if r.detail else None,
                    "occurred_at": r.occurred_at.isoformat(),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
