"""Daily review scans: a reviewer checks one supporter's work for one day.

Who can use it:

* admins and managers - any supporter
* members - the supporters on their own team
* supporters - nothing

A reviewer has at most one open scan; it must be finished or cancelled
before the next one. Scans run one at a time on the server (see
``server.scan.worker``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import profile_service
from ..config import get_settings
from ..database import get_db
from ..models import Application, ReviewScan, ReviewScanJob, ReviewSettings, User
from ..scan import worker
from ..scan.archive import MAX_UPLOAD_BYTES, ScanFileError, check_zip
from ..scan.options import ScanOptions, merge_defaults
from ..security import get_current_user, is_admin_site_user, is_true_admin, normalize_role

router = APIRouter(prefix="/review", tags=["review"])


# ----- access -----


def _reviewer(user: User) -> None:
    if not (is_admin_site_user(user) or normalize_role(user.role) == "member"):
        raise HTTPException(status_code=403, detail="Reviews are for admins, managers and members")


def _supporter_ids(db: Session, user: User) -> set[str] | None:
    """Supporters the caller may review; None means every supporter."""
    _reviewer(user)
    if is_admin_site_user(user):
        return None
    return profile_service.team_supporter_ids(db, user.id, active_only=True)


def _supporter(db: Session, user: User, supporter_id: str) -> User:
    allowed = _supporter_ids(db, user)
    supporter = db.get(User, supporter_id)
    if (
        supporter is None
        or normalize_role(supporter.role) != "supporter"
        or (allowed is not None and supporter_id not in allowed)
    ):
        raise HTTPException(status_code=404, detail="Supporter not found")
    return supporter


def _visible(db: Session, user: User, scan_id: str) -> ReviewScan:
    allowed = _supporter_ids(db, user)
    scan = db.get(ReviewScan, scan_id)
    if scan is None or (
        allowed is not None and scan.reviewer_id != user.id and scan.supporter_id not in allowed
    ):
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


def _own(db: Session, user: User, scan_id: str) -> ReviewScan:
    scan = _visible(db, user, scan_id)
    if scan.reviewer_id != user.id and not is_true_admin(user):
        raise HTTPException(status_code=403, detail="Only the reviewer who started this scan can change it")
    return scan


def _open_scan(db: Session, reviewer_id: str) -> ReviewScan | None:
    return db.execute(
        select(ReviewScan).where(
            ReviewScan.reviewer_id == reviewer_id, ReviewScan.status.in_(worker.OPEN_STATUSES)
        ).order_by(ReviewScan.created_at.desc())
    ).scalars().first()


def _parse_day(value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Pick a date") from None
    if day > date.today() + timedelta(days=1):
        raise HTTPException(status_code=400, detail="That date is in the future")
    return day


# ----- output -----


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _person(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.name or "", "email": user.email, "avatar_url": user.avatar_url}


def _detail(db: Session, user: User, scan: ReviewScan, with_jobs: bool = True) -> dict:
    out = {
        "id": scan.id,
        "status": scan.status,
        "progress": scan.progress,
        "stage": scan.stage,
        "error": scan.error,
        "work_date": scan.work_date,
        "created_at": _iso(scan.created_at),
        "scanned_at": _iso(scan.scanned_at),
        "finished_at": _iso(scan.finished_at),
        "files_deleted": scan.files_deleted_at is not None,
        "supporter": _person(db.get(User, scan.supporter_id)),
        "reviewer": _person(db.get(User, scan.reviewer_id)),
        "options": merge_defaults(json.loads(scan.options or "{}")).model_dump(),
        "uploads": json.loads(scan.upload_info or "{}"),
        "summary": json.loads(scan.summary or "{}"),
        "day_findings": json.loads(scan.day_findings or "[]"),
        "queue_position": worker.queue_position(db, scan),
        "can_edit": scan.reviewer_id == user.id or is_true_admin(user),
        "reviewer_note": scan.reviewer_note or "",
    }
    if with_jobs and scan.status in ("ready", "finished", "expired", "cancelled"):
        jobs = db.execute(
            select(ReviewScanJob).where(ReviewScanJob.scan_id == scan.id).order_by(ReviewScanJob.position)
        ).scalars().all()
        out["jobs"] = [
            {
                "id": j.id,
                "position": j.position,
                "company": j.company,
                "title": j.title,
                "status": j.status,
                "verdict": j.verdict,
                "note": j.note,
                "reviewed_at": _iso(j.reviewed_at),
                "details": json.loads(j.details or "{}"),
                "findings": json.loads(j.findings or "[]"),
            }
            for j in jobs
        ]
        out["report_text"] = scan.report_text
    return out


# ----- choosing what to scan -----


@router.get("/supporters")
def supporters(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    allowed = _supporter_ids(db, user)
    query = select(User).where(func.lower(User.role) == "supporter", User.is_active.is_(True))
    if allowed is not None:
        query = query.where(User.id.in_(allowed or {""}))
    rows = sorted(db.execute(query).scalars(), key=lambda u: (u.name or u.email).lower())
    return [{"id": u.id, "name": u.name or "", "email": u.email} for u in rows]


@router.get("/day")
def day_preview(
    supporter_id: str,
    work_date: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """How many tracker records the supporter has on that day."""
    supporter = _supporter(db, user, supporter_id)
    day = _parse_day(work_date)
    start = datetime(day.year, day.month, day.day)
    records = db.execute(
        select(func.count()).select_from(Application).where(
            func.coalesce(Application.creator_user_id, Application.user_id) == supporter.id,
            Application.applied_date >= start,
            Application.applied_date < start + timedelta(days=1),
            Application.deleted_at.is_(None),
        )
    ).scalar_one()
    done = db.execute(
        select(func.count()).select_from(ReviewScan).where(
            ReviewScan.supporter_id == supporter.id,
            ReviewScan.work_date == day.isoformat(),
            ReviewScan.status == "finished",
        )
    ).scalar_one()
    return {"records": records, "finished_scans": done}


@router.get("/defaults")
def defaults(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    _reviewer(user)
    row = db.get(ReviewSettings, 1)
    return merge_defaults(json.loads(row.options) if row else {}).model_dump()


@router.get("/current")
def current(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """The caller's open scan, if any (the wizard resumes from it)."""
    _reviewer(user)
    worker.sweep()
    scan = _open_scan(db, user.id)
    return {"scan": _detail(db, user, scan) if scan else None}


# ----- the scan -----


async def _save_upload(upload: UploadFile, dest, label: str) -> None:
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"The {label} is larger than {MAX_UPLOAD_BYTES // 2**20} MB",
                    )
                out.write(chunk)
    finally:
        await upload.close()
    if not written:
        raise HTTPException(status_code=400, detail=f"The {label} is empty")


@router.post("/scans", status_code=201)
async def create_scan(
    supporter_id: str = Form(...),
    work_date: str = Form(...),
    resumes: UploadFile = File(...),
    snapshots: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Upload the day's two ZIPs. Nothing is unpacked yet; the reviewer picks
    the options next and then starts the scan."""
    supporter = _supporter(db, user, supporter_id)
    day = _parse_day(work_date)
    if _open_scan(db, user.id) is not None:
        raise HTTPException(status_code=409, detail="Finish or cancel your open scan before starting another")

    scan_id = str(uuid.uuid4())
    folder = worker.scan_dir(scan_id)
    folder.mkdir(parents=True)
    try:
        await _save_upload(resumes, folder / "resumes.zip", "resumes ZIP")
        await _save_upload(snapshots, folder / "snapshots.zip", "snapshots ZIP")
        info = {
            "resumes": check_zip(folder / "resumes.zip", "resumes", resumes.filename or ""),
            "snapshots": check_zip(folder / "snapshots.zip", "snapshots", snapshots.filename or ""),
        }
    except ScanFileError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    warnings = []
    for key, label in (("resumes", "resumes ZIP"), ("snapshots", "snapshots ZIP")):
        named = info[key].get("date_in_name")
        if named and named != day.isoformat():
            warnings.append(f"The {label} is named for {named}, but you picked {day.isoformat()}.")
    first = (supporter.name or "").split(" ")[0].lower()
    for key, label in (("resumes", "resumes ZIP"), ("snapshots", "snapshots ZIP")):
        fname = (info[key].get("name") or "").lower()
        if first and len(first) > 2 and re.search(r"[a-z]{3}", fname) and first not in fname and "_" in fname:
            warnings.append(f"The {label} name doesn't mention {supporter.name}.")
    info["warnings"] = warnings

    row = db.get(ReviewSettings, 1)
    scan = ReviewScan(
        id=scan_id,
        reviewer_id=user.id,
        supporter_id=supporter.id,
        work_date=day.isoformat(),
        status="uploaded",
        stage="Choose the options, then start the scan",
        options=json.dumps(merge_defaults(json.loads(row.options) if row else {}).model_dump()),
        upload_info=json.dumps(info),
    )
    db.add(scan)
    db.commit()
    return _detail(db, user, scan)


@router.post("/scans/{scan_id}/start")
def start_scan(
    scan_id: str,
    options: ScanOptions,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    scan = _own(db, user, scan_id)
    if scan.status not in ("uploaded", "failed"):
        raise HTTPException(status_code=409, detail="This scan has already started")
    if not (worker.scan_dir(scan.id) / "resumes.zip").exists():
        raise HTTPException(status_code=409, detail="The uploaded files are gone. Cancel and upload again.")
    data = options.model_dump()
    scan.options = json.dumps(data)
    scan.status, scan.progress, scan.error = "queued", 0, ""
    scan.stage = "Waiting for the server"
    scan.updated_at = datetime.utcnow()
    # These options become the defaults for the next scan.
    row = db.get(ReviewSettings, 1)
    if row is None:
        row = ReviewSettings(id=1)
        db.add(row)
    row.options, row.updated_by, row.updated_at = json.dumps(data), user.id, datetime.utcnow()
    db.commit()
    worker.kick()
    return _detail(db, user, scan)


@router.get("/scans/{scan_id}")
def get_scan(scan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    scan = _visible(db, user, scan_id)
    if scan.reviewer_id == user.id and scan.status in ("uploaded", "ready") and \
            datetime.utcnow() - scan.updated_at > timedelta(minutes=5):
        scan.updated_at = datetime.utcnow()  # the reviewer is still working on it
        db.commit()
    return _detail(db, user, scan)


@router.post("/scans/{scan_id}/finish")
def finish_scan(scan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Save the final report and delete the uploaded files."""
    scan = _own(db, user, scan_id)
    if scan.status != "ready":
        raise HTTPException(status_code=409, detail="Only a scanned day can be finished")
    now = datetime.utcnow()
    scan.status, scan.stage = "finished", "Report saved"
    scan.finished_at = scan.updated_at = scan.files_deleted_at = now
    db.flush()
    scan.report_text = worker.render(db, scan)
    db.commit()
    worker.delete_files(scan.id)
    return _detail(db, user, scan)


@router.post("/scans/{scan_id}/cancel")
def cancel_scan(scan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Stop and delete the uploaded files. Nothing is saved."""
    scan = _own(db, user, scan_id)
    if scan.status not in worker.OPEN_STATUSES:
        raise HTTPException(status_code=409, detail="This scan is already closed")
    now = datetime.utcnow()
    scan.status, scan.stage = "cancelled", "Cancelled"
    scan.updated_at = scan.files_deleted_at = now
    db.commit()
    worker.delete_files(scan.id)
    return _detail(db, user, scan, with_jobs=False)


# ----- history -----


@router.get("/scans")
def list_scans(
    supporter_id: str | None = None,
    include_cancelled: bool = False,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Closed scans the caller can see, newest day first."""
    allowed = _supporter_ids(db, user)
    statuses = ["finished", "expired"] + (["cancelled"] if include_cancelled else [])
    filters = [ReviewScan.status.in_(statuses)]
    if allowed is not None:
        filters.append(or_(ReviewScan.supporter_id.in_(allowed or {""}), ReviewScan.reviewer_id == user.id))
    if supporter_id:
        filters.append(ReviewScan.supporter_id == supporter_id)
    total = db.execute(select(func.count()).select_from(ReviewScan).where(*filters)).scalar_one()
    rows = db.execute(
        select(ReviewScan).where(*filters)
        .order_by(ReviewScan.work_date.desc(), ReviewScan.created_at.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    people = {
        u.id: u for u in db.execute(
            select(User).where(User.id.in_({r.supporter_id for r in rows} | {r.reviewer_id for r in rows} or {""}))
        ).scalars()
    }
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "work_date": r.work_date,
                "status": r.status,
                "supporter": _person(people.get(r.supporter_id)),
                "reviewer": _person(people.get(r.reviewer_id)),
                "summary": json.loads(r.summary or "{}"),
                "finished_at": _iso(r.finished_at),
                "has_report": bool(r.report_text),
            }
            for r in rows
        ],
    }


@router.get("/summary")
def summary_report(
    date_from: str | None = None,
    date_to: str | None = None,
    supporter_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Combine finished reviews in a date range (only saved results)."""
    allowed = _supporter_ids(db, user)
    end = _parse_day(date_to) if date_to else date.today()
    start = _parse_day(date_from) if date_from else end - timedelta(days=29)
    if start > end:
        raise HTTPException(status_code=400, detail="The start date is after the end date")
    if (end - start).days > 366:
        raise HTTPException(status_code=400, detail="Pick a range of a year or less")
    filters = [
        ReviewScan.status == "finished",
        ReviewScan.work_date >= start.isoformat(),
        ReviewScan.work_date <= end.isoformat(),
    ]
    if allowed is not None:
        filters.append(or_(ReviewScan.supporter_id.in_(allowed or {""}), ReviewScan.reviewer_id == user.id))
    if supporter_id:
        filters.append(ReviewScan.supporter_id == supporter_id)
    scans = db.execute(select(ReviewScan).where(*filters)).scalars().all()
    ids = [s.id for s in scans]
    jobs: dict[str, list[dict]] = {i: [] for i in ids}
    for chunk in range(0, len(ids), 500):
        for j in db.execute(
            select(ReviewScanJob).where(ReviewScanJob.scan_id.in_(ids[chunk : chunk + 500]))
        ).scalars():
            jobs[j.scan_id].append(
                {"status": j.status, "verdict": j.verdict, "findings": json.loads(j.findings or "[]")}
            )
    people = {
        u.id: (u.name or u.email)
        for u in db.execute(
            select(User).where(User.id.in_({s.supporter_id for s in scans} | {s.reviewer_id for s in scans} or {""}))
        ).scalars()
    }
    from ..scan.summary import build_summary

    return build_summary(
        [
            {
                "id": s.id,
                "work_date": s.work_date,
                "supporter": {"id": s.supporter_id, "name": people.get(s.supporter_id, "Supporter")},
                "reviewer": people.get(s.reviewer_id, "Reviewer"),
                "summary": json.loads(s.summary or "{}"),
                "day_findings": json.loads(s.day_findings or "[]"),
                "reviewer_note": s.reviewer_note or "",
                "finished_at": s.finished_at,
                "jobs": jobs[s.id],
            }
            for s in scans
        ],
        date_from=start,
        date_to=end,
        made_by=user.name or user.email,
    )


@router.get("/scans/{scan_id}/report.txt")
def report_file(scan_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    scan = _visible(db, user, scan_id)
    if not scan.report_text:
        raise HTTPException(status_code=404, detail="This scan has no report")
    supporter = db.get(User, scan.supporter_id)
    who = re.sub(r"[^A-Za-z0-9]+", "_", (supporter.name if supporter else "") or "supporter").strip("_")
    return Response(
        scan.report_text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{scan.work_date}_review_{who}.txt"'},
    )


# ----- reviewing -----


class VerdictIn(BaseModel):
    verdict: Literal["pass", "issue", "fail"] | None = None
    note: str = Field(default="", max_length=2000)


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=5000)


def _reviewable(db: Session, user: User, scan_id: str) -> ReviewScan:
    scan = _own(db, user, scan_id)
    if scan.status != "ready":
        raise HTTPException(status_code=409, detail="This scan can't be changed any more")
    return scan


@router.put("/scans/{scan_id}/jobs/{job_id}")
def save_verdict(
    scan_id: str,
    job_id: int,
    payload: VerdictIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The reviewer's Pass / Issue / Fail and note for one job."""
    scan = _reviewable(db, user, scan_id)
    job = db.get(ReviewScanJob, job_id)
    if job is None or job.scan_id != scan.id:
        raise HTTPException(status_code=404, detail="Job not found")
    now = datetime.utcnow()
    job.verdict = payload.verdict
    job.note = payload.note.strip()
    job.reviewed_at = now if payload.verdict else None
    scan.updated_at = now
    db.commit()
    return {"id": job.id, "verdict": job.verdict, "note": job.note, "reviewed_at": _iso(job.reviewed_at)}


@router.put("/scans/{scan_id}/note")
def save_note(
    scan_id: str,
    payload: NoteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The reviewer's overall comment for the day."""
    scan = _reviewable(db, user, scan_id)
    scan.reviewer_note = payload.note.strip()
    scan.updated_at = datetime.utcnow()
    db.commit()
    return {"reviewer_note": scan.reviewer_note}


# ----- viewing the uploaded files -----
#
# The page keeps its login token in the browser, and a frame can't send it.
# So each file opens through a short-lived signed link that works for one
# file of one open scan only. Form captures are third-party web pages: they
# are served sandboxed, with scripts and outside connections blocked.

LINK_TTL = 30 * 60
_AREAS = {"resumes", "snapshots"}
_TYPES = {
    ".pdf": ("application/pdf", "pdf"),
    ".html": ("text/html; charset=utf-8", "html"),
    ".htm": ("text/html; charset=utf-8", "html"),
    ".png": ("image/png", "image"),
    ".jpg": ("image/jpeg", "image"),
    ".jpeg": ("image/jpeg", "image"),
    ".webp": ("image/webp", "image"),
    ".gif": ("image/gif", "image"),
    ".txt": ("text/plain; charset=utf-8", "text"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "download"),
    ".doc": ("application/msword", "download"),
}
_CAPTURE_CSP = (
    "sandbox; default-src 'none'; img-src data: blob:; style-src 'unsafe-inline' data:; "
    "font-src data:; media-src data:; frame-ancestors 'self'"
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    key = ("review-file:" + get_settings().jwt_secret).encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:40]


def _file_path(scan_id: str, area: str, rel: str) -> Path:
    if area not in _AREAS:
        raise HTTPException(status_code=404, detail="File not found")
    base = (worker.scan_dir(scan_id) / area).resolve()
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file() or path.suffix.lower() not in _TYPES:
        raise HTTPException(status_code=404, detail="File not found")
    return path


@router.get("/scans/{scan_id}/file-link")
def file_link(
    scan_id: str,
    area: str,
    path: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    scan = _visible(db, user, scan_id)
    if scan.status != "ready":
        raise HTTPException(status_code=409, detail="The files of this scan are gone")
    file = _file_path(scan.id, area, path)
    payload = json.dumps([scan.id, area, path, user.id, int(time.time()) + LINK_TTL], separators=(",", ":"))
    token = _b64(payload.encode()) + "." + _sign(payload)
    return {
        "url": f"/api/v1/review/files/{token}/{_b64(file.name.encode())[:40] or 'file'}",
        "kind": _TYPES[file.suffix.lower()][1],
        "name": file.name,
    }


@router.get("/files/{token}/{_name}")
def serve_file(token: str, _name: str, db: Session = Depends(get_db)) -> Response:
    """Serve one uploaded file through a signed link (no login header)."""
    try:
        encoded, signature = token.split(".", 1)
        payload = _unb64(encoded).decode()
        scan_id, area, rel, user_id, expires = json.loads(payload)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(status_code=404, detail="File not found") from None
    if not hmac.compare_digest(signature, _sign(payload)) or time.time() > int(expires):
        raise HTTPException(status_code=403, detail="This link has expired. Open the file again.")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Not allowed")
    scan = _visible(db, user, scan_id)
    if scan.status != "ready":
        raise HTTPException(status_code=404, detail="The files of this scan are gone")
    file = _file_path(scan.id, area, rel)
    media, kind = _TYPES[file.suffix.lower()]
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
    }
    if kind == "html":
        headers["Content-Security-Policy"] = _CAPTURE_CSP
    elif kind == "pdf":
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    else:
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'self'"
    disposition = "attachment" if kind == "download" else "inline"
    return FileResponse(file, media_type=media, headers=headers, filename=file.name,
                         content_disposition_type=disposition)
