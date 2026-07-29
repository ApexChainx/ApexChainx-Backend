from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.orm.session import SessionORM


class SessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_session(
        self,
        access_token: str,
        refresh_token: str,
        email: str,
        family_id: str,
        sequence: int,
        expires_at: datetime,
    ) -> SessionORM:
        session = SessionORM(
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            family_id=family_id,
            sequence=sequence,
            expires_at=expires_at,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_session(self, access_token: str) -> Optional[SessionORM]:
        return self.db.query(SessionORM).filter(SessionORM.access_token == access_token).first()

    def get_session_by_refresh_token(self, refresh_token: str) -> Optional[SessionORM]:
        return self.db.query(SessionORM).filter(SessionORM.refresh_token == refresh_token).first()

    def delete_session(self, access_token: str) -> None:
        session = self.get_session(access_token)
        if session:
            self.db.delete(session)
            self.db.commit()

    def delete_sessions_by_family(self, family_id: str) -> int:
        """Delete all sessions belonging to a token family. Returns count."""
        sessions = self.db.query(SessionORM).filter(SessionORM.family_id == family_id).all()
        count = len(sessions)
        for session in sessions:
            self.db.delete(session)
        self.db.commit()
        return count

    def list_sessions_by_email(self, email: str) -> list[SessionORM]:
        """List all active sessions for a given email."""
        return self.db.query(SessionORM).filter(SessionORM.email == email).order_by(SessionORM.created_at.desc()).all()

    def delete_sessions_by_email(self, email: str) -> int:
        """Delete all sessions for a given email. Returns count of deleted sessions."""
        sessions = self.list_sessions_by_email(email)
        count = len(sessions)
        for session in sessions:
            self.db.delete(session)
        self.db.commit()
        return count
