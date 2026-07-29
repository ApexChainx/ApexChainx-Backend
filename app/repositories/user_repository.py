from datetime import datetime

from sqlalchemy.orm import Session

from app.models.auth import AuthUser
from app.models.enums import Role
from app.models.orm.user import UserORM


def user_orm_to_pydantic(orm: UserORM) -> AuthUser:
    return AuthUser(
        id=orm.id,
        email=orm.email,
        full_name=orm.full_name,
        role=Role(orm.role),
        stellar_wallet=orm.stellar_wallet,
        created_at=orm.created_at,
    )

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.email == email).first()

    def get_by_id(self, user_id: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.id == user_id).first()

    def create(self, user_id: str, email: str, hashed_password: str, full_name: str, role: Role) -> UserORM:
        user = UserORM(
            id=user_id,
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def increment_failed_attempts(self, email: str) -> None:
        """Increment failed login attempts for a user."""
        user = self.get_by_email(email)
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            self.db.commit()

    def reset_failed_attempts(self, email: str) -> None:
        """Reset failed login attempts for a user (on successful login)."""
        user = self.get_by_email(email)
        if user:
            user.failed_login_attempts = 0
            user.locked_until = None
            self.db.commit()

    def lock_account(self, email: str, locked_until: datetime) -> None:
        """Lock a user account until the specified time."""
        user = self.get_by_email(email)
        if user:
            user.locked_until = locked_until
            self.db.commit()

    def is_account_locked(self, email: str) -> bool:
        """Check if a user account is currently locked."""
        user = self.get_by_email(email)
        if not user:
            return False
        if user.locked_until is None:
            return False
        return user.locked_until > datetime.utcnow()

    def update_profile(self, user_id: str, full_name: str | None = None, stellar_wallet: str | None = None) -> UserORM | None:
        """Update mutable profile fields. Returns updated ORM or None if not found."""
        user = self.get_by_id(user_id)
        if not user:
            return None
        if full_name is not None:
            user.full_name = full_name
        if stellar_wallet is not None:
            user.stellar_wallet = stellar_wallet
        self.db.commit()
        self.db.refresh(user)
        return user
