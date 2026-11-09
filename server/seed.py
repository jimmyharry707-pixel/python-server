"""Bootstrap data: create an initial admin user on first launch."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import SessionLocal
from .models import User
from .security import hash_password


def bootstrap_admin() -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        existing = db.execute(select(User).limit(1)).scalar_one_or_none()
        if existing is not None:
            return
        admin = User(
            email=settings.bootstrap_admin_email,
            name=settings.bootstrap_admin_name,
            password_hash=hash_password(settings.bootstrap_admin_password),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        print(
            f"[seed] Created bootstrap admin: {settings.bootstrap_admin_email} "
            "(change the password immediately)."
        )
    finally:
        db.close()
