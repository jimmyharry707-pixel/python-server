"""Release manifest endpoints for client auto-update."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Release, User
from ..release_versioning import latest_for_channel, parse_version_tuple
from ..schemas import ReleaseOut
from ..security import get_current_admin

router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("/latest", response_model=ReleaseOut)
def latest_release(channel: str = "stable", db: Session = Depends(get_db)) -> Release:
    release = latest_for_channel(db, channel)
    if release is None:
        raise HTTPException(status_code=404, detail="No release published")
    return release


@router.post("", response_model=ReleaseOut, status_code=201)
def publish_release(
    payload: ReleaseOut,
    channel: str = "stable",
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Release:
    current = latest_for_channel(db, channel)
    if current is not None and parse_version_tuple(payload.version) <= parse_version_tuple(
        current.version
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Version {payload.version} is not newer than published {current.version}",
        )
    release = Release(
        channel=channel,
        version=payload.version,
        url=payload.url,
        sha256=payload.sha256,
        mandatory=payload.mandatory,
        notes=payload.notes,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release
