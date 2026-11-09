"""Password hashing, JWT tokens and auth dependencies."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from . import auth_sessions
from .config import get_settings
from .database import get_db
from .models import AuthSession, User

_settings = get_settings()

# tokenUrl is informational (Swagger); real login is POST {api_prefix}/auth/login.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{_settings.api_prefix}/auth/login", auto_error=False
)


def _to_bytes(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes; truncate explicitly to avoid errors.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(password), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, session_id: str, ttl_minutes: int | None = None) -> str:
    """A short-lived token for one sign-in session (see auth_sessions.py)."""
    now = datetime.utcnow()
    minutes = ttl_minutes or _settings.access_token_ttl_minutes
    payload: dict[str, Any] = {
        "sub": user_id,
        "sid": session_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algorithm)


def decode_token(
    token: str, expected_type: str | None = None, *, verify_exp: bool = True
) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _settings.jwt_secret,
            algorithms=[_settings.jwt_algorithm],
            options={"verify_exp": verify_exp},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    if expected_type and payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong token type"
        )
    return payload


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    payload = decode_token(token, expected_type="access")
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    # Tokens from before sign-in sessions existed carry no sid: sign in again.
    session = db.get(AuthSession, session_id) if session_id else None
    if not auth_sessions.is_live(session) or session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has ended. Please sign in again.",
        )
    user = db.get(User, user_id) if user_id else None
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


def get_current_session_id(token: str | None = Depends(oauth2_scheme)) -> str | None:
    """The caller's session id, so "end my other sessions" can keep this one."""
    if not token:
        return None
    try:
        return decode_token(token, expected_type="access").get("sid")
    except HTTPException:
        return None


ADMIN_SITE_ROLES = frozenset({"admin", "manager"})


def is_admin_site_user(user: User) -> bool:
    """Admins and managers may use the admin dashboard."""
    return normalize_role(user.role) in ADMIN_SITE_ROLES


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not is_admin_site_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or manager privileges required",
        )
    return user


# Account roles: admin | manager | supporter | member
# Legacy "user" is normalized to "member".
ROLES = frozenset({"admin", "manager", "supporter", "member"})
WORK_PLAN_ROLES = frozenset({"admin", "manager", "member"})


def normalize_role(role: str | None, *, default: str = "member") -> str:
    raw = (role or "").strip().lower()
    if raw == "user":
        return "member"
    if raw in ROLES:
        return raw
    return default


def has_work_plan_access(user: User) -> bool:
    return normalize_role(user.role) in WORK_PLAN_ROLES


def get_current_work_plan_user(user: User = Depends(get_current_user)) -> User:
    if not has_work_plan_access(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Work plan is not available for this role",
        )
    return user


def get_current_member(user: User = Depends(get_current_user)) -> User:
    """Members only: supporters and staff don't receive announcements."""
    if normalize_role(user.role) != "member":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Announcements are only available to members",
        )
    return user


def can_manage_others_work_plans(user: User) -> bool:
    """Admins and managers may edit another user's work plan."""
    return is_admin_site_user(user)


def can_view_others_work_plans(user: User) -> bool:
    """Who may see the team overview, read anyone's plan and comment on it:
    admins, managers, and members an admin made a work plan reviewer."""
    if is_admin_site_user(user):
        return True
    return normalize_role(user.role) == "member" and bool(user.can_review_work_plans)


def get_current_work_plan_reviewer(user: User = Depends(get_current_user)) -> User:
    if not can_view_others_work_plans(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins, managers and work plan reviewers can see the team's plans",
        )
    return user


def is_true_admin(user: User) -> bool:
    return normalize_role(user.role) == "admin"


def get_user_profile_map(db: Session, user_id: str) -> dict[str, str]:
    """Return {profile_key: profile_id} for every profile assigned to ``user_id``.

    Used by profile-scoped sync to resolve a client-supplied ``profile_key`` to
    the shared dataset it owns, and to verify the caller is actually assigned.
    """
    # Imported here to avoid a circular import at module load time.
    from sqlalchemy import select

    from .models import Profile, ProfileAssignment

    rows = db.execute(
        select(Profile.key, Profile.id)
        .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
        .where(ProfileAssignment.user_id == user_id)
    ).all()
    return {key: pid for key, pid in rows if key}


def get_user_profile_label_map(db: Session, user_id: str) -> dict[str, str]:
    """Return {normalized label: profile_id} for assigned profiles.

    Labels are the lowercased profile ``key`` and display ``name``. Sync uses
    this when a client omits ``profile_key`` but sends an ``account`` label
    like ``\"Thomas Wu\"``, so the row still attaches to the shared dataset.
    """
    from sqlalchemy import select

    from .models import Profile, ProfileAssignment

    rows = db.execute(
        select(Profile.key, Profile.name, Profile.id)
        .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
        .where(ProfileAssignment.user_id == user_id)
    ).all()
    mapping: dict[str, str] = {}
    for key, name, pid in rows:
        for label in (key, name):
            norm = (label or "").strip().lower()
            if norm:
                mapping.setdefault(norm, pid)
    return mapping


def get_user_profile_ids(db: Session, user_id: str) -> set[str]:
    """Return the set of profile ids assigned to ``user_id``."""
    return set(get_user_profile_map(db, user_id).values())
