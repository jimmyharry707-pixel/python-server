"""Member announcements: Markdown notices authored by admins and managers.

Only members receive them (supporters and staff don't), and each member tracks
their own read state. Images are the training course's asset store, so authors
upload through ``/training/assets`` and embed the returned Markdown snippet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Announcement, AnnouncementRead, User
from ..security import get_current_admin, get_current_member

router = APIRouter(prefix="/announcements", tags=["announcements"])

_BODY_MAX_CHARS = 100_000
# Legacy accounts may still carry the role "user", which means member.
_MEMBER_ROLES = ("member", "user")


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body_md: str = Field(default="", max_length=_BODY_MAX_CHARS)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Give the announcement a title")
        return value


def _announcement_out(item: Announcement, **extra: object) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "body_md": item.body_md,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        **extra,
    }


def _get_announcement(db: Session, announcement_id: str) -> Announcement:
    item = db.get(Announcement, announcement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return item


def _read_ids(db: Session, user_id: str) -> set[str]:
    return set(
        db.execute(
            select(AnnouncementRead.announcement_id).where(AnnouncementRead.user_id == user_id)
        ).scalars()
    )


def _unread_count(db: Session, user_id: str) -> int:
    # Count against live announcements only, so a deleted one can't linger.
    total = db.execute(select(func.count()).select_from(Announcement)).scalar_one()
    read = db.execute(
        select(func.count())
        .select_from(AnnouncementRead)
        .join(Announcement, Announcement.id == AnnouncementRead.announcement_id)
        .where(AnnouncementRead.user_id == user_id)
    ).scalar_one()
    return max(total - read, 0)


def _member_read_counts(db: Session, announcement_id: str | None = None) -> dict[str, int]:
    """How many active members have read each announcement."""
    query = (
        select(AnnouncementRead.announcement_id, func.count())
        .join(User, User.id == AnnouncementRead.user_id)
        .where(User.is_active.is_(True), func.lower(User.role).in_(_MEMBER_ROLES))
        .group_by(AnnouncementRead.announcement_id)
    )
    if announcement_id is not None:
        query = query.where(AnnouncementRead.announcement_id == announcement_id)
    return dict(db.execute(query).all())


# --- Member side -----------------------------------------------------------


@router.get("")
def list_announcements(
    user: User = Depends(get_current_member), db: Session = Depends(get_db)
) -> dict:
    items = db.execute(
        select(Announcement).order_by(Announcement.created_at.desc(), Announcement.id)
    ).scalars()
    read = _read_ids(db, user.id)
    out = [_announcement_out(item, read=item.id in read) for item in items]
    return {"items": out, "unread_count": sum(1 for x in out if not x["read"])}


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_member), db: Session = Depends(get_db)) -> dict:
    return {"count": _unread_count(db, user.id)}


@router.post("/read-all", status_code=204)
def mark_all_read(user: User = Depends(get_current_member), db: Session = Depends(get_db)) -> Response:
    read = _read_ids(db, user.id)
    for announcement_id in db.execute(select(Announcement.id)).scalars():
        if announcement_id not in read:
            db.add(AnnouncementRead(user_id=user.id, announcement_id=announcement_id))
    db.commit()
    return Response(status_code=204)


@router.post("/{announcement_id}/read", status_code=204)
def mark_read(
    announcement_id: str,
    user: User = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Response:
    _get_announcement(db, announcement_id)
    exists = db.execute(
        select(AnnouncementRead.id).where(
            AnnouncementRead.user_id == user.id,
            AnnouncementRead.announcement_id == announcement_id,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(AnnouncementRead(user_id=user.id, announcement_id=announcement_id))
        db.commit()
    return Response(status_code=204)


# --- Staff side ------------------------------------------------------------


@router.get("/manage")
def manage_announcements(_: User = Depends(get_current_admin), db: Session = Depends(get_db)) -> dict:
    members = db.execute(
        select(func.count())
        .select_from(User)
        .where(User.is_active.is_(True), func.lower(User.role).in_(_MEMBER_ROLES))
    ).scalar_one()
    read_counts = _member_read_counts(db)
    items = db.execute(
        select(Announcement).order_by(Announcement.created_at.desc(), Announcement.id)
    ).scalars()
    return {
        "items": [
            _announcement_out(item, read_count=read_counts.get(item.id, 0)) for item in items
        ],
        "member_count": members,
    }


@router.post("", status_code=201)
def create_announcement(
    payload: AnnouncementIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    item = Announcement(
        title=payload.title,
        body_md=payload.body_md,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _announcement_out(item, read_count=0)


@router.put("/{announcement_id}")
def update_announcement(
    announcement_id: str,
    payload: AnnouncementIn,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_announcement(db, announcement_id)
    item.title = payload.title
    item.body_md = payload.body_md
    item.updated_by = admin.id
    db.commit()
    db.refresh(item)
    read_count = _member_read_counts(db, item.id).get(item.id, 0)
    return _announcement_out(item, read_count=read_count)


@router.delete("/{announcement_id}", status_code=204)
def delete_announcement(
    announcement_id: str, _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> Response:
    item = _get_announcement(db, announcement_id)
    # Explicit: SQLite doesn't enforce the FK cascade.
    db.execute(delete(AnnouncementRead).where(AnnouncementRead.announcement_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=204)
