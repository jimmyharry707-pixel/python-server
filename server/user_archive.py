"""Archive / restore user accounts (replaces plain deactivate).

Archived = ``is_active`` False plus ``archived_at``: the person can't sign in
(and is signed out everywhere, so a restore doesn't revive old sessions),
their license seats are freed, and they are hidden from every people list,
picker and ranking. Their profile assignments and team link are kept so a
restore is lossless, and the applications/interviews they logged still count
in profile and organization totals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth_sessions, profile_service
from .models import License, LicenseSeat, User
from .license_service import audit as _audit, seats_used as _seats_used
from .security import is_true_admin, normalize_role

ARCHIVE_REASON = "user_archived"
SupporterAction = Literal["unlink", "move", "archive"]


def _check_can_change(actor: User, user: User) -> None:
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="You can't archive your own account")
    if normalize_role(user.role) == "admin" and not is_true_admin(actor):
        raise HTTPException(status_code=403, detail="Only admins can archive admin accounts")


def _revoke_seats(db: Session, actor: User, user: User, now: datetime) -> int:
    seats = db.execute(
        select(LicenseSeat).where(
            LicenseSeat.user_id == user.id,
            LicenseSeat.revoked.is_(False),
            LicenseSeat.revoked_at.is_(None),
        )
    ).scalars().all()
    for seat in seats:
        seat.revoked = True
        seat.revoked_at = now
        seat.revoked_reason = ARCHIVE_REASON
        _audit(
            db,
            event="revoke_seat",
            license_id=seat.license_id,
            seat_id=seat.id,
            user_id=actor.id,
            detail={"reason": ARCHIVE_REASON, "archived_user_id": user.id},
        )
    return len(seats)


def archive_user(
    db: Session,
    actor: User,
    user: User,
    supporters: SupporterAction = "unlink",
    move_to: str | None = None,
) -> dict:
    """Archive ``user``. For a member, ``supporters`` says what happens to
    their team: move to ``move_to``, archive too, or leave without a member."""
    _check_can_change(actor, user)
    now = datetime.utcnow()
    archived = [user.id]
    seats = 0
    if normalize_role(user.role) == "member":
        team = db.execute(
            select(User).where(User.member_id == user.id, User.is_active.is_(True))
        ).scalars().all()
        if supporters == "move":
            if not move_to or move_to == user.id:
                raise HTTPException(status_code=400, detail="Choose the member to move supporters to")
            target = db.get(User, move_to)
            if target is None or not target.is_active:
                raise HTTPException(status_code=400, detail="Choose an active member")
            for supporter in team:
                profile_service.link_supporter(db, supporter, move_to)
        elif supporters == "archive":
            for supporter in team:
                supporter.is_active = False
                supporter.archived_at = now
                seats += _revoke_seats(db, actor, supporter, now)
                archived.append(supporter.id)
        else:
            for supporter in team:
                supporter.member_id = None
    user.is_active = False
    user.archived_at = now
    seats += _revoke_seats(db, actor, user, now)
    for user_id in archived:
        auth_sessions.end_user_sessions(db, user_id, reason="archived")
    return {"archived_user_ids": archived, "revoked_seats": seats}


def restore_user(db: Session, actor: User, user: User) -> dict:
    """Let the person sign in again and give back the seats archiving freed,
    where the license still has room (otherwise they activate afresh)."""
    _check_can_change(actor, user)
    user.is_active = True
    user.archived_at = None
    restored = 0
    skipped = 0
    seats = db.execute(
        select(LicenseSeat).where(
            LicenseSeat.user_id == user.id, LicenseSeat.revoked_reason == ARCHIVE_REASON
        )
    ).scalars().all()
    for seat in seats:
        lic = db.get(License, seat.license_id)
        if lic is None or not lic.is_active or _seats_used(db, lic.id) >= lic.seat_limit:
            skipped += 1
            continue
        seat.revoked = False
        seat.revoked_at = None
        seat.revoked_reason = None
        db.flush()
        _audit(
            db,
            event="restore_seat",
            license_id=seat.license_id,
            seat_id=seat.id,
            user_id=actor.id,
            detail={"reason": "user_restored", "restored_user_id": user.id},
        )
        restored += 1
    return {"restored_seats": restored, "seats_without_room": skipped}
