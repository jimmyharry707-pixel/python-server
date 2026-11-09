"""Application endpoints (per-user isolated)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Application, User
from ..pipeline import (
    APPLICATION_STATUSES,
    normalize_application_status,
)
from ..schemas import ApplicationCreate, ApplicationOut
from ..security import get_current_user, get_user_profile_ids, is_admin_site_user

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("/", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Application:
    app = Application(
        user_id=user.id,
        creator_user_id=user.id,
        last_editor_user_id=user.id,
        company_name=payload.company_name,
        position_title=payload.position_title,
        job_link=payload.job_link,
        applied_date=payload.applied_date or datetime.utcnow(),
        status=payload.status,
        account=payload.account,
        resume_file_path=payload.resume_file_path,
        cover_letter_file_path=payload.cover_letter_file_path,
        notes=payload.notes,
        last_modified=datetime.utcnow(),
        client_created_at=datetime.utcnow(),
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


@router.get("/", response_model=list[ApplicationOut])
def list_applications(
    limit: int = 500,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Application]:
    rows = db.execute(
        select(Application)
        .where(Application.user_id == user.id, Application.deleted_at.is_(None))
        .order_by(Application.applied_date.desc())
        .limit(min(limit, 1000))
        .offset(offset)
    ).scalars().all()
    return list(rows)


@router.get("/stats")
def application_stats(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict:
    total = db.execute(
        select(func.count())
        .select_from(Application)
        .where(Application.user_id == user.id, Application.deleted_at.is_(None))
    ).scalar_one()

    by_status_rows = db.execute(
        select(Application.status, func.count())
        .where(Application.user_id == user.id, Application.deleted_at.is_(None))
        .group_by(Application.status)
    ).all()

    return {
        "total": total,
        "by_status": {status_value: count for status_value, count in by_status_rows},
    }


class ApplicationStatusUpdate(BaseModel):
    """Status change from the web app (the desktop writes via sync instead)."""

    status: str


@router.patch("/{application_id}/status")
def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Set an application's status from the web.

    Every timestamp the sync protocol depends on has to move together here, or
    the edit is silently lost:

    * ``last_modified`` drives last-write-wins on the desktop. Left alone, the
      next push from a client holding an older copy would look newer and quietly
      overwrite this change.
    * ``server_modified`` drives the pull cursor. Left alone, no client would
      ever be handed the row and the web and desktop would disagree forever.
    * ``revision`` is what a revision-aware client sends back as
      ``base_revision``. Bumping it makes that client's next push conflict
      rather than clobber; the rejection carries ``server_revision`` so it
      recovers on the following cycle.
    """
    canonical = normalize_application_status(payload.status)
    if canonical is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {payload.status!r}. "
            f"Expected one of: {', '.join(APPLICATION_STATUSES)}",
        )

    app = db.get(Application, application_id)
    if app is None or app.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Application not found")

    # Admins and managers already *see* every application through scope=all, so
    # restricting them from closing one would only push the fix to raw SQL.
    # Everyone else must be assigned to the owning profile, or own the row
    # personally when it has no profile - the same rule the read endpoints use.
    if not is_admin_site_user(user):
        assigned = get_user_profile_ids(db, user.id)
        permitted = (
            app.profile_id in assigned if app.profile_id else app.user_id == user.id
        )
        if not permitted:
            raise HTTPException(
                status_code=403, detail="Not assigned to this application"
            )

    if app.status == canonical:
        return {"id": app.id, "status": app.status, "revision": app.revision}

    now = datetime.utcnow()
    app.status = canonical
    app.last_modified = now
    app.server_modified = now
    app.revision = int(app.revision or 1) + 1
    app.last_editor_user_id = user.id

    # Deliberately does NOT write interview outcomes. An application status is
    # the user's statement about the application; inferring from it which
    # rounds were passed was guesswork persisted as fact, and it guessed wrong
    # in two routine cases - a round still merely *scheduled* was marked passed
    # because a later one existed, and a cancelled final round read "pending"
    # because the cancelled->withdrew rule only covered non-final rounds.
    # Outcome is now recorded by a person or not at all; round numbers are
    # derived from the ordered rounds at read time (see server.pipeline).
    db.commit()

    return {
        "id": app.id,
        "status": app.status,
        "revision": app.revision,
    }
