"""Fleet monitoring: usage telemetry, error reports, consent.

Client online presence is updated by sync push/pull (see ``server.presence``),
not a dedicated heartbeat endpoint.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ConsentRecord, ErrorReport, TelemetryEvent, User
from ..schemas import (
    ConsentIn,
    ErrorReportIn,
    TelemetryBatch,
)
from ..security import get_current_user

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.post("/telemetry")
def telemetry(
    batch: TelemetryBatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    for ev in batch.events:
        db.add(
            TelemetryEvent(
                device_id=batch.device_id,
                user_id=user.id,
                event=ev.event,
                app_version=batch.app_version,
                properties=json.dumps(ev.properties) if ev.properties else None,
            )
        )
    db.commit()
    return {"ok": True, "accepted": len(batch.events)}


@router.post("/errors")
def report_error(
    req: ErrorReportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    db.add(
        ErrorReport(
            device_id=req.device_id,
            user_id=user.id,
            app_version=req.app_version,
            error_type=req.error_type,
            message=req.message,
            traceback=req.traceback,
        )
    )
    db.commit()
    return {"ok": True}


@router.post("/consent")
def record_consent(
    req: ConsentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    db.add(
        ConsentRecord(
            user_id=user.id,
            device_id=req.device_id,
            policy_version=req.policy_version,
            accepted=req.accepted,
        )
    )
    db.commit()
    return {"ok": True}
