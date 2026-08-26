from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.orm.session import SessionORM
from app.models.orm.token_family import TokenFamilyORM


class TokenFamilyRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_family(self, family_id: str, email: str) -> TokenFamilyORM:
        family = TokenFamilyORM(
            family_id=family_id,
            email=email,
            current_sequence=0,
            compromised=False,
        )
        self.db.add(family)
        self.db.commit()
        self.db.refresh(family)
        return family

    def get_family(self, family_id: str) -> TokenFamilyORM | None:
        return self.db.query(TokenFamilyORM).filter(TokenFamilyORM.family_id == family_id).first()

    def increment_sequence(self, family_id: str) -> TokenFamilyORM | None:
        family = self.get_family(family_id)
        if family:
            family.current_sequence += 1
            family.updated_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(family)
        return family

    def compromise_family(self, family_id: str) -> TokenFamilyORM | None:
        family = self.get_family(family_id)
        if family:
            family.compromised = True
            family.updated_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(family)
        return family

    def delete_families_by_email(self, email: str) -> int:
        families = self.db.query(TokenFamilyORM).filter(TokenFamilyORM.email == email).all()
        count = len(families)
        for family in families:
            self.db.delete(family)
        self.db.commit()
        return count

    def delete_orphaned_families(self, batch_size: int = 1000) -> int:
        """Delete token families that no longer have any sessions. Returns total count deleted."""
        total_deleted = 0
        while True:
            orphaned = (
                self.db.query(TokenFamilyORM)
                .outerjoin(SessionORM, SessionORM.family_id == TokenFamilyORM.family_id)
                .filter(SessionORM.family_id.is_(None))
                .limit(batch_size)
                .all()
            )
            if not orphaned:
                break
            for family in orphaned:
                self.db.delete(family)
            self.db.commit()
            total_deleted += len(orphaned)
            if len(orphaned) < batch_size:
                break
        return total_deleted
