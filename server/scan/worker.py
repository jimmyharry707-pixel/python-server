"""Runs daily review scans on one background thread, one scan at a time, and
deletes uploaded files when they are no longer needed.

Scans wait in the ``queued`` status until the thread picks them up, so a busy
server never runs two heavy scans at once. Uploaded files are deleted when a
scan is finished or cancelled, and 48 hours after its last activity if the
reviewer walks away.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from ..config import get_settings
from ..database import SessionLocal
from ..models import Application, Profile, ReviewScan, ReviewScanJob, User
from . import names
from .archive import ScanFileError, extract_zip
from .captures import find_captures, read_capture
from .checks import Clock, Rec, pick_clock, run_checks
from .documents import find_folders, read_document
from .options import merge_defaults
from .report import build_report

logger = logging.getLogger("baymax.scan")

OPEN_STATUSES = ("uploaded", "queued", "scanning", "ready", "failed")
CLOSED_STATUSES = ("finished", "cancelled", "expired")
IDLE_LIMIT = timedelta(hours=48)
_ID = re.compile(r"^[0-9a-f-]{36}$")

_wake = threading.Event()
_thread: threading.Thread | None = None
_start_lock = threading.Lock()
_last_sweep = datetime.min


def scan_root() -> Path:
    configured = get_settings().scan_dir
    root = Path(configured) if configured else Path(__file__).resolve().parent.parent / "data" / "scans"
    root.mkdir(parents=True, exist_ok=True)
    return root


def scan_dir(scan_id: str) -> Path:
    if not _ID.match(scan_id):
        raise ValueError("bad scan id")
    return scan_root() / scan_id


def delete_files(scan_id: str) -> None:
    try:
        shutil.rmtree(scan_dir(scan_id), ignore_errors=True)
    except ValueError:
        pass


# ----- thread -----


def start() -> None:
    """Called at server start: requeue scans cut off by a restart, clean up,
    and start the thread."""
    with SessionLocal() as db:
        for scan in db.execute(select(ReviewScan).where(ReviewScan.status == "scanning")).scalars():
            scan.status, scan.progress, scan.stage = "queued", 0, "Waiting (the server restarted)"
        db.commit()
    sweep(force=True)
    kick()


def kick() -> None:
    global _thread
    with _start_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="review-scan-worker", daemon=True)
            _thread.start()
    _wake.set()


def _loop() -> None:
    while True:
        _wake.clear()
        try:
            sweep()
            scan_id = _next_queued()
            if scan_id:
                process(scan_id)
                continue
        except Exception:  # keep the thread alive whatever happens
            logger.exception("Review scan worker error")
        _wake.wait(timeout=300)


def _next_queued() -> str | None:
    with SessionLocal() as db:
        return db.execute(
            select(ReviewScan.id).where(ReviewScan.status == "queued").order_by(ReviewScan.updated_at)
        ).scalars().first()


def queue_position(db, scan: ReviewScan) -> int:
    """How many scans run before this one (0 = next)."""
    if scan.status != "queued":
        return 0
    return db.execute(
        select(func.count()).select_from(ReviewScan).where(
            or_(ReviewScan.status == "scanning",
                (ReviewScan.status == "queued") & (ReviewScan.updated_at < scan.updated_at))
        )
    ).scalar_one()


# ----- clean-up -----


def sweep(force: bool = False) -> None:
    """Expire idle scans and delete files nobody needs. Runs at most every 10
    minutes unless forced."""
    global _last_sweep
    now = datetime.utcnow()
    if not force and now - _last_sweep < timedelta(minutes=10):
        return
    _last_sweep = now
    with SessionLocal() as db:
        stale = db.execute(
            select(ReviewScan).where(
                ReviewScan.status.in_(("uploaded", "queued", "ready", "failed")),
                ReviewScan.updated_at < now - IDLE_LIMIT,
            )
        ).scalars().all()
        for scan in stale:
            delete_files(scan.id)
            scan.status, scan.stage = "expired", "Files deleted after 48 hours without activity"
            scan.files_deleted_at = now
        db.commit()
        # Folders left behind by closed scans (e.g. a delete that failed).
        # Only folders this database knows are closed: the scans folder may be
        # shared with another server (a test run next to production), so
        # unknown folders are never touched.
        names = [p.name for p in scan_root().iterdir() if p.is_dir() and _ID.match(p.name)]
        closed = set()
        for i in range(0, len(names), 500):
            closed |= set(db.execute(
                select(ReviewScan.id).where(
                    ReviewScan.id.in_(names[i : i + 500]), ReviewScan.status.in_(CLOSED_STATUSES)
                )
            ).scalars())
    for name in closed:
        delete_files(name)


# ----- the scan -----


def _set(db, scan: ReviewScan, progress: int, stage: str) -> None:
    scan.progress, scan.stage = progress, stage
    db.commit()


def _cancelled(db, scan_id: str) -> bool:
    db.expire_all()
    scan = db.get(ReviewScan, scan_id)
    return scan is None or scan.status != "scanning"


def process(scan_id: str) -> None:
    with SessionLocal() as db:
        scan = db.get(ReviewScan, scan_id)
        if scan is None or scan.status != "queued":
            return
        scan.status = "scanning"
        _set(db, scan, 5, "Unpacking the resumes ZIP")
        try:
            _run(db, scan)
        except ScanFileError as exc:
            db.rollback()
            scan = db.get(ReviewScan, scan_id)
            if scan is not None and scan.status == "scanning":
                scan.status, scan.error, scan.stage = "failed", str(exc), "Stopped"
                db.commit()
        except Exception:
            logger.exception("Review scan %s failed", scan_id)
            db.rollback()
            scan = db.get(ReviewScan, scan_id)
            if scan is not None and scan.status == "scanning":
                scan.status, scan.stage = "failed", "Stopped"
                scan.error = "The scan stopped because of an unexpected error. Cancel it and try again."
                db.commit()


def _records(db, supporter: User, day: date, window: int) -> tuple[list[Rec], list[Rec], dict, dict]:
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    creator = func.coalesce(Application.creator_user_id, Application.user_id)
    today = db.execute(
        select(Application).where(
            creator == supporter.id,
            Application.applied_date >= start,
            Application.applied_date < end,
            Application.deleted_at.is_(None),
        )
    ).scalars().all()
    profile_ids = {a.profile_id for a in today if a.profile_id}
    history: dict[str, Application] = {}
    if profile_ids:
        for a in db.execute(
            select(Application).where(
                Application.profile_id.in_(profile_ids),
                Application.deleted_at.is_(None),
                Application.applied_date >= start - timedelta(days=window + 1),
                Application.applied_date < end,
            )
        ).scalars():
            history[a.id] = a
        # A matching job link is shown however long ago it was. Links are
        # compared in Python (several addresses can mean one job), so read
        # just the candidates' links first, then the records that match.
        wanted = {(kind, key) for a in today for kind, key in names.job_keys(a.job_link).items()}
        exact = {(a.job_link or "").strip().rstrip("/").lower() for a in today if a.job_link}
        matched = [
            app_id
            for app_id, link in db.execute(
                select(Application.id, Application.job_link).where(
                    Application.profile_id.in_(profile_ids),
                    Application.deleted_at.is_(None),
                    Application.applied_date < start - timedelta(days=window + 1),
                    Application.job_link.isnot(None),
                    Application.job_link != "",
                )
            )
            if link.strip().rstrip("/").lower() in exact
            or any((kind, key) in wanted for kind, key in names.job_keys(link).items())
        ]
        for i in range(0, len(matched), 500):
            for a in db.execute(select(Application).where(Application.id.in_(matched[i : i + 500]))).scalars():
                history[a.id] = a
    user_ids = {a.creator_user_id or a.user_id for a in list(history.values()) + list(today)}
    people = {u.id: (u.name or u.email) for u in db.execute(select(User).where(User.id.in_(user_ids or {""}))).scalars()}
    profiles = {p.id: p for p in db.execute(select(Profile).where(Profile.id.in_(profile_ids or {""}))).scalars()}

    def rec(a: Application) -> Rec:
        creator_id = a.creator_user_id or a.user_id
        profile = profiles.get(a.profile_id)
        return Rec(
            id=a.id,
            profile_id=a.profile_id,
            profile_name=(profile.name or profile.key) if profile else "",
            creator_id=creator_id,
            creator_name=people.get(creator_id, "someone"),
            company=a.company_name or "",
            title=a.position_title or "",
            link=a.job_link or "",
            status=a.status or "",
            notes=a.notes or "",
            applied_day=a.applied_date.date() if a.applied_date else None,
            created_utc=a.client_created_at or a.created_at,
            resume_path=a.resume_file_path or "",
        )

    contact = {
        pid: ((p.email or "").strip().lower(), re.sub(r"\D", "", p.phone or "")[-10:])
        for pid, p in profiles.items()
    }
    return [rec(a) for a in today], [rec(a) for a in history.values()], contact, profiles


def _run(db, scan: ReviewScan) -> None:
    folder = scan_dir(scan.id)
    opts = merge_defaults(json.loads(scan.options or "{}"))
    extract_zip(folder / "resumes.zip", folder / "resumes", "resumes")
    _set(db, scan, 15, "Unpacking the snapshots ZIP")
    extract_zip(folder / "snapshots.zip", folder / "snapshots", "snapshots")
    if _cancelled(db, scan.id):
        return

    _set(db, scan, 25, "Reading resumes and cover letters")
    resume_root = folder / "resumes"
    folders = find_folders(resume_root)
    for f in folders:
        for doc in f.files:
            if doc.kind in ("resume", "cover"):
                read_document(resume_root, doc)

    _set(db, scan, 55, "Reading the form captures")
    snap_root = folder / "snapshots"
    pages, other = find_captures(snap_root)
    captures = [read_capture(snap_root, p) for p in pages]
    if _cancelled(db, scan.id):
        return

    _set(db, scan, 80, "Matching files to the tracker and checking")
    supporter = db.get(User, scan.supporter_id)
    day = date.fromisoformat(scan.work_date)
    records, history, contact, _ = _records(db, supporter, day, opts.duplicate_window_days)
    try:
        tz = ZoneInfo(supporter.timezone or "UTC")
        fallback = tz.utcoffset(datetime(day.year, day.month, day.day, 12)) or timedelta(0)
        label = supporter.timezone or "UTC"
    except Exception:
        fallback, label = timedelta(0), "UTC"
    clock = pick_clock(captures, fallback, label)
    jobs, day_findings, summary = run_checks(
        records=records, history=history, folders=folders, captures=captures,
        other_evidence=[p.relative_to(snap_root).as_posix() for p in other],
        opts=opts, work_day=day, contacts_by_profile=contact, clock=clock,
    )
    if _cancelled(db, scan.id):
        return

    db.query(ReviewScanJob).filter(ReviewScanJob.scan_id == scan.id).delete()
    for i, job in enumerate(jobs):
        db.add(ReviewScanJob(
            scan_id=scan.id,
            position=i,
            company=job.main.company[:500],
            title=job.main.title[:500],
            status=job.status,
            details=json.dumps(_details(job, clock), default=str),
            findings=json.dumps(job.findings),
        ))
    scan.summary = json.dumps(summary)
    scan.day_findings = json.dumps(day_findings)
    scan.status, scan.progress, scan.stage = "ready", 100, "Ready for review"
    scan.scanned_at = scan.updated_at = datetime.utcnow()
    db.flush()
    scan.report_text = render(db, scan)
    db.commit()


def _details(job, clock: Clock) -> dict:
    folder = job.folder
    return {
        "time": clock.hm(job.main.created_utc),
        "profile": job.main.profile_name,
        "records": [
            {"id": r.id, "time": clock.hm(r.created_utc), "company": r.company, "title": r.title,
             "link": r.link, "status": r.status, "notes": r.notes}
            for r in job.records
        ],
        "folder": folder.rel if folder else None,
        "files": [
            {"rel": f.rel, "kind": f.kind, "pages": f.pages, "page_size": f.page_size,
             "made_at": f.made_at.strftime("%H:%M") if f.made_at else None}
            for f in (folder.files if folder else [])
        ],
        "captures": [
            {"rel": c.rel, "title": c.title, "url": c.url,
             "saved": c.saved_local.strftime("%H:%M") if c.saved_local else None,
             "confirmation": c.confirmation, "attached": c.attached,
             "answers": [[q, a] for q, a in c.answers[:80]]}
            for c in job.captures
        ],
    }


def render(db, scan: ReviewScan) -> str:
    """Build the text report from what is stored for the scan."""
    people = {u.id: (u.name or u.email) for u in db.execute(
        select(User).where(User.id.in_({scan.reviewer_id, scan.supporter_id}))).scalars()}
    jobs = db.execute(
        select(ReviewScanJob).where(ReviewScanJob.scan_id == scan.id).order_by(ReviewScanJob.position)
    ).scalars().all()
    return build_report(
        supporter=people.get(scan.supporter_id, "Supporter"),
        reviewer=people.get(scan.reviewer_id, "Reviewer"),
        work_date=scan.work_date,
        status=scan.status,
        scanned_at=scan.scanned_at,
        finished_at=scan.finished_at,
        summary=json.loads(scan.summary or "{}"),
        day_findings=json.loads(scan.day_findings or "[]"),
        jobs=[
            {"company": j.company, "title": j.title, "status": j.status, "verdict": j.verdict,
             "note": j.note, "details": json.loads(j.details or "{}"),
             "findings": json.loads(j.findings or "[]")}
            for j in jobs
        ],
        options=merge_defaults(json.loads(scan.options or "{}")),
        uploads=json.loads(scan.upload_info or "{}"),
        reviewer_note=scan.reviewer_note or "",
    )
