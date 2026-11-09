"""Sign-in sessions: one row per browser or desktop app that signed in.

Signing in creates a session and hands back two tokens:

- an access token (a JWT that lasts ``access_token_ttl_minutes``), sent with
  every request. It names its session (``sid``) and every request checks the
  session is still live, so ending a session takes effect at once.
- a refresh token ``<session id>.<generation>.<signature>``, used only to get
  the next access token. The website keeps it in an HttpOnly cookie the page
  can't read; the desktop app keeps it in the Windows credential store.

Each renewal moves the session to the next generation, retiring the token
just used. If a retired token comes back after the short grace window,
someone else has a copy, so the whole session is ended.

A session ends after its idle time without a renewal, or at its hard limit
(shorter for admins and managers), whichever comes first. Signing out,
changing or resetting a password, a role change, archiving and deleting an
account end sessions too.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import AuthSession, User

_TOKEN_RE = re.compile(r"^([0-9a-f-]{36})\.(\d{1,9})\.([0-9a-f]{64})$")
# Same as security.ADMIN_SITE_ROLES; not imported because security imports us.
_STAFF_ROLES = frozenset({"admin", "manager"})
# Ended sessions are kept this long before they are deleted.
_KEEP_ENDED = timedelta(days=7)


def _now() -> datetime:
    return datetime.utcnow()


def _signed_out(detail: str = "Your session has ended. Please sign in again.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _signature(session: AuthSession, generation: int) -> str:
    key = f"auth-session:{get_settings().jwt_secret}".encode()
    message = f"{session.id}:{generation}:{session.salt}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def refresh_token_for(session: AuthSession) -> str:
    """The session's current refresh token."""
    gen = session.generation
    return f"{session.id}.{gen}.{_signature(session, gen)}"


def _parse(token: str | None) -> tuple[str, int, str] | None:
    match = _TOKEN_RE.match((token or "").strip())
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _idle_window(remember: bool) -> timedelta:
    s = get_settings()
    if remember:
        return timedelta(days=max(1, s.session_idle_days))
    return timedelta(hours=max(1, s.session_short_idle_hours))


def _hard_limit(user: User) -> timedelta:
    s = get_settings()
    staff = (user.role or "").strip().lower() in _STAFF_ROLES
    return timedelta(days=max(1, s.session_staff_max_days if staff else s.session_max_days))


def is_live(session: AuthSession | None, now: datetime | None = None) -> bool:
    if session is None or session.revoked_at is not None:
        return False
    now = now or _now()
    return now < session.idle_expires_at and now < session.expires_at


def start_session(
    db: Session,
    user: User,
    *,
    client: str,
    remember: bool = True,
    ip: str = "",
    user_agent: str = "",
) -> AuthSession:
    """Open a session for ``user`` (flushed, not committed)."""
    now = _now()
    session = AuthSession(
        user_id=user.id,
        client=client,
        remember=remember,
        salt=secrets.token_hex(16),
        generation=0,
        created_at=now,
        rotated_at=now,
        last_used_at=now,
        idle_expires_at=now + _idle_window(remember),
        expires_at=now + _hard_limit(user),
        ip=(ip or "")[:64],
        user_agent=(user_agent or "")[:255],
    )
    db.add(session)
    db.flush()
    return session


def find_session(db: Session, token: str | None, *, lock: bool = False) -> tuple[AuthSession, int] | None:
    """The session a refresh token (of any generation) belongs to, if genuine."""
    parsed = _parse(token)
    if parsed is None:
        return None
    session_id, generation, signature = parsed
    query = select(AuthSession).where(AuthSession.id == session_id)
    if lock:
        # Postgres: two renewals of one session take turns. SQLite ignores it.
        query = query.with_for_update()
    session = db.execute(query).scalar_one_or_none()
    if session is None or not hmac.compare_digest(signature, _signature(session, generation)):
        return None
    return session, generation


def renew_session(db: Session, token: str | None) -> AuthSession:
    """Check a refresh token and move its session on. Raises 401.

    Commits only when it ends the session; the caller commits a renewal.
    """
    found = find_session(db, token, lock=True)
    if found is None:
        raise _signed_out()
    session, generation = found
    now = _now()
    if not is_live(session, now):
        raise _signed_out()
    grace = timedelta(seconds=max(0, get_settings().session_refresh_grace_seconds))
    if generation == session.generation:
        session.generation += 1
        session.rotated_at = now
    elif generation == session.generation - 1 and now - session.rotated_at <= grace:
        # Another tab or thread renewed with this same token a moment ago;
        # both end up holding the same next token.
        pass
    else:
        # A retired token came back: someone else has a copy.
        session.revoked_at = now
        session.revoked_reason = "token_reused"
        db.commit()
        raise _signed_out()
    session.last_used_at = now
    session.idle_expires_at = now + _idle_window(session.remember)
    return session


def end_session(session: AuthSession, reason: str = "signed_out") -> None:
    if session.revoked_at is None:
        session.revoked_at = _now()
        session.revoked_reason = reason


def end_user_sessions(
    db: Session, user_id: str, *, reason: str, keep: str | None = None
) -> int:
    """End every live session of ``user_id`` except ``keep``. Not committed."""
    stmt = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if keep:
        stmt = stmt.where(AuthSession.id != keep)
    result = db.execute(
        stmt.values(revoked_at=_now(), revoked_reason=reason).execution_options(
            synchronize_session="fetch"
        )
    )
    return result.rowcount or 0


def purge_ended_sessions(db: Session) -> None:
    """Delete sessions that ended over a week ago. Not committed."""
    cutoff = _now() - _KEEP_ENDED
    db.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.expires_at < cutoff,
                AuthSession.idle_expires_at < cutoff,
                AuthSession.revoked_at < cutoff,
            )
        )
    )
