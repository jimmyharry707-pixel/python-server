"""Batch sync endpoints (/sync/push, /sync/pull).

Implements the idempotent, UUID-keyed, last_modified conflict rules the desktop
client already relies on (see ``backend/production_sync.py``):

- PUSH: upsert by client-provided UUID. If the server copy is newer, reject with
  a "newer version" reason (the client treats this as a normal version conflict
  and silently skips it). Interviews additionally get time-overlap detection;
  overlaps are rejected as a "time conflict" unless ``force_override`` is set.
- PULL: return this user's rows modified since the client's cursor, keyset-paginated.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import time
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Application, Interview, LicenseSeat, SyncAudit, User
from ..presence import touch_client_presence
from ..schemas import (
    SyncAccepted,
    SyncCheckIdsRequest,
    SyncCheckIdsResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncRejection,
)
from ..security import (
    get_current_user,
    get_user_profile_label_map,
    get_user_profile_map,
)
from .interviews import _serialize as _serialize_interview
from ..pipeline import normalize_outcome, normalize_stage, stage_for_type
from .license import require_active_license

router = APIRouter(prefix="/sync", tags=["sync"])

logger = logging.getLogger(__name__)

_settings = get_settings()

_EPOCH = datetime(1970, 1, 1)
_DEFAULT_PULL_LIMIT = 500
_MAX_PULL_LIMIT = 1000
# Ingest guardrail: reject oversized single-request payloads outright.
_MAX_PUSH_RECORDS = 2000

# Per-user sliding-window ingest limiter (in-process). Each entry is a list of
# (monotonic_ts, row_count) tuples pruned to the last 60 seconds.
_rate_lock = Lock()
_push_events: dict[str, list[tuple[float, int]]] = defaultdict(list)


def _enforce_push_rate(user_id: str, rows: int) -> None:
    """Throttle a user's push volume; raise 429 with Retry-After if exceeded."""
    max_pushes = max(1, _settings.sync_push_rate_per_minute)
    max_rows = max(1, _settings.sync_push_rows_per_minute)
    now = time.monotonic()
    window = 60.0
    with _rate_lock:
        events = _push_events[user_id]
        cutoff = now - window
        events[:] = [(ts, n) for ts, n in events if ts >= cutoff]
        req_count = len(events)
        row_count = sum(n for _, n in events)
        if req_count >= max_pushes or (row_count + rows) > max_rows:
            oldest = events[0][0] if events else now
            retry_after = max(1, int(window - (now - oldest)) + 1)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Sync push rate limit exceeded; slow down",
                headers={"Retry-After": str(retry_after)},
            )
        events.append((now, rows))


class _PushStats:
    """Per-request counters recorded to ``SyncAudit`` for admin monitoring."""

    def __init__(self) -> None:
        self.accepted = 0
        self.rejected_not_assigned = 0
        self.version_conflicts = 0
        self.other_rejected = 0
        # Finer breakdown of the three buckets above - see SyncAudit for why.
        self.rejected_unscoped = 0
        self.revision_conflicts = 0
        self.rejected_missing_parent = 0
        self.rejected_time_conflict = 0
        self.rejected_error = 0
        self.new_inserts = 0
        self.target_profile_ids: dict[str, int] = {}
        # Rows this request wrote, stamped with a single ``server_modified`` just
        # before commit (see ``_stamp_server_modified``).
        self.touched_application_ids: list[str] = []
        self.touched_interview_ids: list[str] = []

    def note_target(self, profile_id: str | None) -> None:
        if profile_id:
            self.target_profile_ids[profile_id] = (
                self.target_profile_ids.get(profile_id, 0) + 1
            )

    def primary_profile_id(self) -> str | None:
        if not self.target_profile_ids:
            return None
        return max(self.target_profile_ids.items(), key=lambda kv: kv[1])[0]


def _naive(dt: datetime | None) -> datetime | None:
    """Drop tzinfo so naive DB datetimes compare cleanly with client values."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# Fixed UTC offsets (hours) for the timezone abbreviations interviews store.
# Despite the column name, ``start_time_utc``/``end_time_utc`` hold the raw
# wall-clock time the user entered, not a real UTC instant - the sibling
# ``timezone`` column is what says which zone that wall-clock time is in (kept
# in sync with the desktop client's ``interview_service.TIMEZONE_UTC_OFFSETS``).
# Overlap detection has to apply this offset itself, or two interviews booked
# at the same wall-clock hour in different zones (e.g. 11:00 Pacific and 11:00
# Central) look identical and get flagged as conflicting even though they are
# hours apart in real time.
_TIMEZONE_UTC_OFFSETS = {
    "EST": -5, "EDT": -4,
    "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8,
    "HST": -10,
    "AST": -4, "ADT": -3,
    "GMT": 0, "UTC": 0,
    "BST": 1,
}
_MAX_TIMEZONE_OFFSET_SPAN = timedelta(hours=11)  # HST (-10) to BST (+1)


def _true_utc(dt: datetime | None, tz: str | None) -> datetime | None:
    """Convert a stored wall-clock time + zone abbreviation to a real UTC instant.

    Unknown/missing abbreviations are treated as already-UTC (offset 0) so a
    row we can't interpret degrades to today's naive comparison instead of
    raising.
    """
    if dt is None:
        return None
    offset = _TIMEZONE_UTC_OFFSETS.get((tz or "").strip().upper(), 0)
    return dt - timedelta(hours=offset)


def _advisory_lock_key(user_id: str | int) -> int:
    """Map a user id to a signed 64-bit key for ``pg_advisory_xact_lock``.

    User primary keys are UUID strings; Postgres advisory locks need a bigint.
    """
    if isinstance(user_id, int):
        return user_id
    digest = hashlib.blake2b(str(user_id).encode("utf-8"), digest_size=8).digest()
    return int(struct.unpack(">q", digest)[0])


def _acquire_user_sync_lock(db: Session, user_id: str | int) -> None:
    """Serialize concurrent sync pushes for the same user (Postgres advisory lock)."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        # Transaction-scoped; released on commit/rollback.
        db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _advisory_lock_key(user_id)},
        )


def _acquire_profile_sync_locks(db: Session, profile_ids: set[str]) -> None:
    """Serialize pushes from different accounts into the same shared profile.

    The user lock alone lets two accounts insert into one profile at the same
    time, which is exactly the race that produces duplicate rows.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Stable order avoids deadlocks between batches touching several profiles.
    for pid in sorted(profile_ids):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _advisory_lock_key(f"profile:{pid}")},
        )


def _clean_text(value: str | None) -> str | None:
    """Strip NUL bytes (and other C0 controls except tab/newline) for Postgres TEXT."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if "\x00" not in value and not any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        return value
    return "".join(
        ch for ch in value if ch in "\t\n\r" or ord(ch) >= 32
    )


def _resolve_target_profile(
    item,
    user_profiles: dict[str, str],
    label_to_id: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Map an item to an owned profile id.

    Resolution order:
      1. Explicit ``profile_key`` → assigned profile key (exact), else label map
         (case-insensitive key/name) for clients that send a display name as key.
      2. Empty key + ``account`` label → assigned profile whose key/name matches
         (e.g. account ``\"Thomas Wu\"`` → profile ``thomas``).
      3. Otherwise unresolved. Push handlers reject this — personal
         (``profile_id=None``) was a client bug, not a supported scope.

    Returns ``(profile_id, error)``. ``error='not_assigned'`` when a key is
    supplied that the caller is not assigned to (or does not exist).
    """
    labels = label_to_id or {}
    key = (getattr(item, "profile_key", None) or "").strip()
    if key:
        pid = user_profiles.get(key) or labels.get(key.lower())
        if pid is None:
            return None, "not_assigned"
        return pid, None

    account = (getattr(item, "account", None) or "").strip().lower()
    if account:
        pid = labels.get(account)
        if pid is not None:
            return pid, None
    return None, None


def _can_write_existing(existing, user: User, user_profile_ids: set[str]) -> bool:
    """Whether ``user`` may modify an existing row under the shared-by-profile rules."""
    if existing.profile_id is not None:
        return existing.profile_id in user_profile_ids
    # Personal / legacy row: only the owner may write it.
    return existing.user_id == user.id


def _targeted_profile_ids(
    payload,
    user_profiles: dict[str, str],
    label_to_id: dict[str, str] | None = None,
) -> set[str]:
    """Profile ids this push writes to (skipping keys the caller cannot use)."""
    pids: set[str] = set()
    for item in list(payload.applications) + list(payload.interviews):
        pid, err = _resolve_target_profile(item, user_profiles, label_to_id)
        if err is None and pid is not None:
            pids.add(pid)
    return pids


def _push_applications(
    db: Session,
    user: User,
    apps,
    accepted: list,
    rejected: list,
    user_profiles: dict[str, str],
    user_profile_ids: set[str],
    stats: _PushStats,
    label_to_id: dict[str, str] | None = None,
) -> None:
    if not apps:
        return
    ids = [item.id for item in apps]
    existing_by_id = {
        row.id: row
        for row in db.execute(select(Application).where(Application.id.in_(ids)))
        .scalars()
        .all()
    }
    for item in apps:
        try:
            with db.begin_nested():
                existing = existing_by_id.get(item.id)
                was_existing = existing is not None
                incoming_mod = _naive(item.last_modified) or datetime.utcnow()

                target_pid, scope_err = _resolve_target_profile(
                    item, user_profiles, label_to_id
                )
                if scope_err == "not_assigned":
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Not assigned to this profile",
                            status="not_assigned",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    continue
                # Every row must resolve to an assigned profile. Personal
                # (profile_id=None) was a client bug that dumped local accounts.
                if target_pid is None:
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Not scoped to an assigned profile",
                            status="unscoped",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    stats.rejected_unscoped += 1
                    continue

                if existing is not None and not _can_write_existing(
                    existing, user, user_profile_ids
                ):
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Record belongs to a profile you are not assigned to",
                            status="not_assigned",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    continue

                if existing is not None:
                    if (
                        item.base_revision is not None
                        and item.base_revision != existing.revision
                    ):
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason=(
                                    "Stale base revision: client="
                                    f"{item.base_revision}, server={existing.revision}"
                                ),
                                status="revision_conflict",
                                server_revision=existing.revision,
                            )
                        )
                        stats.version_conflicts += 1
                        stats.revision_conflicts += 1
                        continue
                    if (
                        item.base_revision is None
                        and
                        _naive(existing.last_modified)
                        and _naive(existing.last_modified) > incoming_mod
                    ):
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason="Server has newer version",
                                status="version_conflict",
                                server_revision=existing.revision,
                            )
                        )
                        stats.version_conflicts += 1
                        continue
                    # Keep an existing shared assignment unless the client
                    # explicitly targets a (still-owned) profile.
                    existing.profile_id = (
                        target_pid if target_pid is not None else existing.profile_id
                    )
                else:
                    existing = Application(
                        id=item.id,
                        user_id=user.id,
                        creator_user_id=user.id,
                        last_editor_user_id=user.id,
                        profile_id=target_pid,
                        revision=1,
                        client_created_at=incoming_mod,
                    )
                    db.add(existing)
                    existing_by_id[item.id] = existing
                    stats.new_inserts += 1

                stats.note_target(existing.profile_id)
                if existing.creator_user_id is None:
                    existing.creator_user_id = existing.user_id
                existing.user_id = user.id
                existing.last_editor_user_id = user.id
                existing.company_name = _clean_text(item.company_name)
                existing.position_title = _clean_text(item.position_title)
                existing.job_link = _clean_text(item.job_link)
                existing.applied_date = _naive(item.applied_date)
                existing.status = _clean_text(item.status)
                existing.account = _clean_text(item.account)
                existing.resume_file_path = _clean_text(item.resume_file_path)
                existing.resume_version = _clean_text(item.resume_version)
                existing.cover_letter_file_path = _clean_text(item.cover_letter_file_path)
                existing.notes = _clean_text(item.notes)
                existing.last_modified = incoming_mod
                # ``server_modified`` (the pull cursor field) is stamped for the
                # whole request just before commit - see _stamp_server_modified.
                stats.touched_application_ids.append(existing.id)
                existing.deleted_at = _naive(item.deleted_at)
                if was_existing:
                    existing.revision = int(existing.revision or 1) + 1
                accepted.append(
                    SyncAccepted(id=item.id, revision=existing.revision)
                )
                stats.accepted += 1
        except Exception as exc:
            rejected.append(
                SyncRejection(
                    id=item.id,
                    reason=f"Could not save application: {exc}",
                    status="error",
                )
            )
            stats.other_rejected += 1
            stats.rejected_error += 1


def _intervals_overlap(a_start, a_end, b_start, b_end) -> bool:
    if not a_start or not a_end or not b_start or not b_end:
        return False
    return a_start < b_end and a_end > b_start


def _accessible_filter(model, user: User, user_profile_ids: set[str]):
    """Rows the user may read/write: any of their profiles, or their own personal rows."""
    personal = and_(model.profile_id.is_(None), model.user_id == user.id)
    if user_profile_ids:
        return or_(model.profile_id.in_(user_profile_ids), personal)
    return personal


def _scope_key(profile_id: str | None, user_id: str):
    """Group rows into a shared-calendar scope: a profile, or a user's personal set."""
    return ("p", profile_id) if profile_id else ("u", user_id)


def _push_interviews(
    db: Session,
    user: User,
    interviews,
    accepted: list,
    rejected: list,
    user_profiles: dict[str, str],
    user_profile_ids: set[str],
    stats: _PushStats,
    label_to_id: dict[str, str] | None = None,
) -> None:
    if not interviews:
        return

    # Ensure apps from this same request are visible for FK checks.
    db.flush()

    ids = [item.id for item in interviews]
    existing_by_id = {
        row.id: row
        for row in db.execute(select(Interview).where(Interview.id.in_(ids)))
        .scalars()
        .all()
    }

    app_ids = {item.application_id for item in interviews if item.application_id}
    known_app_profile: dict[str, str | None] = {}
    if app_ids:
        for aid, pid in db.execute(
            select(Application.id, Application.profile_id).where(
                Application.id.in_(app_ids),
                _accessible_filter(Application, user, user_profile_ids),
            )
        ).all():
            known_app_profile[aid] = pid
    known_app_ids = set(known_app_profile)

    # Overlap detection only ever compares against interviews that intersect the
    # incoming batch's own time span, so load that window instead of every live
    # interview the caller can see. The unbounded form grew with the account's
    # whole history on every single push.
    window_bounds = [
        (_naive(item.start_time_utc), _naive(item.end_time_utc))
        for item in interviews
        if not item.deleted_at
        and not item.force_override
        and item.start_time_utc
        and item.end_time_utc
    ]
    live_by_id: dict[str, Interview] = {}
    if window_bounds:
        window_start = min(start for start, _ in window_bounds)
        window_end = max(end for _, end in window_bounds)
        # The stored columns are wall-clock, not real UTC (see _true_utc), so a
        # candidate that looks outside [window_start, window_end] at the SQL
        # level can still be the same real instant once its own zone's offset
        # is applied. Pad the prefetch by the widest possible offset span and
        # do the exact, timezone-aware comparison in Python below.
        live_rows = (
            db.execute(
                select(Interview).where(
                    _accessible_filter(Interview, user, user_profile_ids),
                    Interview.deleted_at.is_(None),
                    Interview.start_time_utc < window_end + _MAX_TIMEZONE_OFFSET_SPAN,
                    Interview.end_time_utc > window_start - _MAX_TIMEZONE_OFFSET_SPAN,
                )
            )
            .scalars()
            .all()
        )
        live_by_id = {row.id: row for row in live_rows}

    for item in interviews:
        try:
            with db.begin_nested():
                existing = existing_by_id.get(item.id)
                was_existing = existing is not None
                incoming_mod = _naive(item.last_modified) or datetime.utcnow()

                target_pid, scope_err = _resolve_target_profile(
                    item, user_profiles, label_to_id
                )
                if scope_err == "not_assigned":
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Not assigned to this profile",
                            status="not_assigned",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    continue

                app_id = item.application_id
                if app_id and app_id not in known_app_ids:
                    # Parent app not on the server (its batch failed, it was
                    # rejected, or it was never pushed). Accepting the interview
                    # here is what orphans it: the client marks an accepted row
                    # clean, so a link dropped now is never repaired and the
                    # interview stops counting against its application
                    # everywhere downstream.
                    if existing is not None and existing.application_id is not None:
                        # Re-push of a row we already hold a good link for: keep
                        # the link rather than clearing it.
                        app_id = existing.application_id
                    else:
                        # Reject so the row stays dirty on the client and retries
                        # once the parent application lands. The status and reason
                        # are deliberately outside the client's version-conflict
                        # vocabulary ("version_conflict"/"revision_conflict",
                        # "newer version", "timestamp") so it does not pin the row
                        # clean, and outside its time-conflict test so no dialog
                        # is raised.
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason=(
                                    "Parent application is not on the server yet; "
                                    "retry after it syncs"
                                ),
                                status="missing_parent",
                            )
                        )
                        stats.other_rejected += 1
                        stats.rejected_missing_parent += 1
                        continue

                # Interviews often omit profile_key; inherit from the linked app
                # on an assigned profile before treating the row as unscoped.
                if target_pid is None and app_id is not None:
                    inherited = known_app_profile.get(app_id)
                    if inherited is not None:
                        target_pid = inherited

                if target_pid is None:
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Not scoped to an assigned profile",
                            status="unscoped",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    stats.rejected_unscoped += 1
                    continue

                if existing is not None and not _can_write_existing(
                    existing, user, user_profile_ids
                ):
                    rejected.append(
                        SyncRejection(
                            id=item.id,
                            reason="Record belongs to a profile you are not assigned to",
                            status="not_assigned",
                        )
                    )
                    stats.rejected_not_assigned += 1
                    continue

                # Effective profile: explicit/inherited key wins, else keep existing.
                effective_pid = target_pid
                if effective_pid is None and existing is not None:
                    effective_pid = existing.profile_id

                start = _naive(item.start_time_utc)
                end = _naive(item.end_time_utc)

                if existing is not None:
                    if (
                        item.base_revision is not None
                        and item.base_revision != existing.revision
                    ):
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason=(
                                    "Stale base revision: client="
                                    f"{item.base_revision}, server={existing.revision}"
                                ),
                                status="revision_conflict",
                                server_revision=existing.revision,
                            )
                        )
                        stats.version_conflicts += 1
                        stats.revision_conflicts += 1
                        continue
                    if (
                        item.base_revision is None
                        and
                        _naive(existing.last_modified)
                        and _naive(existing.last_modified) > incoming_mod
                    ):
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason="Server has newer version",
                                status="version_conflict",
                                server_revision=existing.revision,
                            )
                        )
                        stats.version_conflicts += 1
                        continue

                if not item.deleted_at and not item.force_override and start and end:
                    item_scope = _scope_key(effective_pid, user.id)
                    item_tz = _clean_text(item.timezone) or "EST"
                    real_start = _true_utc(start, item_tz)
                    real_end = _true_utc(end, item_tz)
                    conflict = False
                    for other in live_by_id.values():
                        if other.id == (existing.id if existing is not None else item.id):
                            continue
                        if _scope_key(other.profile_id, other.user_id) != item_scope:
                            continue
                        if _intervals_overlap(
                            real_start,
                            real_end,
                            _true_utc(_naive(other.start_time_utc), other.timezone),
                            _true_utc(_naive(other.end_time_utc), other.timezone),
                        ):
                            conflict = True
                            break
                    if conflict:
                        rejected.append(
                            SyncRejection(
                                id=item.id,
                                reason="Time conflict detected with an existing interview",
                                status="conflict",
                            )
                        )
                        stats.other_rejected += 1
                        stats.rejected_time_conflict += 1
                        continue

                if existing is None:
                    existing = Interview(
                        id=item.id,
                        user_id=user.id,
                        creator_user_id=user.id,
                        last_editor_user_id=user.id,
                        revision=1,
                    )
                    db.add(existing)
                    existing_by_id[item.id] = existing
                    stats.new_inserts += 1

                existing.profile_id = effective_pid
                stats.note_target(effective_pid)
                if existing.creator_user_id is None:
                    existing.creator_user_id = existing.user_id
                existing.user_id = user.id
                existing.last_editor_user_id = user.id
                existing.application_id = app_id
                existing.title = _clean_text(item.title) or "Interview"
                existing.description = _clean_text(item.description)
                existing.start_time_utc = start
                existing.end_time_utc = end
                existing.timezone = _clean_text(item.timezone) or "EST"
                existing.location = _clean_text(item.location)
                existing.interviewer_name = _clean_text(item.interviewer_name)
                existing.interviewer_email = _clean_text(item.interviewer_email)
                existing.interview_type = _clean_text(item.interview_type)
                existing.notes = _clean_text(item.notes)
                existing.status = _clean_text(item.status) or "scheduled"

                # Pipeline fields are copied only when the client actually sent
                # them. A build predating the pipeline omits them entirely, and
                # Pydantic fills None - copying that unconditionally (as every
                # field above does) would let an old desktop client silently
                # erase a stage or outcome set anywhere else. Absent means
                # "unchanged", not "cleared".
                if item.stage is not None:
                    existing.stage = normalize_stage(item.stage)
                if item.round_index is not None:
                    existing.round_index = item.round_index
                if item.outcome is not None:
                    existing.outcome = normalize_outcome(item.outcome)
                if item.outcome_at is not None:
                    existing.outcome_at = _naive(item.outcome_at)
                if item.next_step is not None:
                    existing.next_step = _clean_text(item.next_step)
                if item.next_step_due is not None:
                    existing.next_step_due = _naive(item.next_step_due)
                # A row created by an old client still needs a classification,
                # or it would sit off the ladder until someone edited it.
                if existing.stage is None:
                    existing.stage = stage_for_type(existing.interview_type)
                if not existing.outcome:
                    existing.outcome = "pending"

                existing.last_modified = incoming_mod
                # Stamped for the whole request before commit; see _stamp_server_modified.
                stats.touched_interview_ids.append(existing.id)
                existing.deleted_at = _naive(item.deleted_at)
                if was_existing:
                    existing.revision = int(existing.revision or 1) + 1

                if existing.deleted_at:
                    live_by_id.pop(existing.id, None)
                else:
                    live_by_id[existing.id] = existing

                accepted.append(
                    SyncAccepted(id=item.id, revision=existing.revision)
                )
                stats.accepted += 1
        except Exception as exc:
            rejected.append(
                SyncRejection(
                    id=item.id,
                    reason=f"Could not save interview: {exc}",
                    status="error",
                )
            )
            stats.other_rejected += 1
            stats.rejected_error += 1


# Chunk size for the id IN (...) stamp. SQLite's default variable limit is 999.
_STAMP_CHUNK = 400


def _stamp_server_modified(db: Session, stats: _PushStats) -> datetime:
    """Stamp every row this request wrote with one ``server_modified``, late.

    ``server_modified`` drives the pull cursor, so the gap between stamping a row
    and the transaction becoming visible is a window in which a concurrent pull
    can hand out a cursor above the stamp and skip the row forever. Stamping every
    row in one bulk UPDATE immediately before commit shrinks that window to the
    commit itself.

    Assigning the ORM attribute per row would not work: ``begin_nested()`` flushes
    on every iteration, so the rows are already written by the time the loop ends
    and re-assigning would issue a fresh UPDATE per row inside ``commit()``.
    """
    batch_ts = datetime.utcnow()
    for model, ids in (
        (Application, stats.touched_application_ids),
        (Interview, stats.touched_interview_ids),
    ):
        for start in range(0, len(ids), _STAMP_CHUNK):
            chunk = ids[start : start + _STAMP_CHUNK]
            db.execute(
                update(model)
                .where(model.id.in_(chunk))
                .values(server_modified=batch_ts)
                .execution_options(synchronize_session=False)
            )
    return batch_ts


def _pull_watermark(db: Session) -> datetime:
    """Cursor value safe to hand a client: below every not-yet-visible write.

    On Postgres this is derived from the oldest in-flight transaction, so it is
    exact: a push stamped at ``T`` necessarily has ``T >= its xact_start >=
    MIN(xact_start)``. Elsewhere (SQLite dev/test) it falls back to a fixed lag.

    The result is clamped into ``[now - max_lag, now - min_lag]``: the floor stops
    one long-running unrelated transaction from pinning every client into a full
    re-download, the ceiling covers clock skew between workers.
    """
    settings = get_settings()
    now = datetime.utcnow()
    min_lag = timedelta(seconds=max(0, settings.sync_cursor_lag_seconds))
    max_lag = timedelta(
        seconds=max(
            settings.sync_cursor_lag_seconds, settings.sync_cursor_max_lag_seconds
        )
    )
    ceiling = now - min_lag
    floor = now - max_lag

    oldest_age_seconds = None
    try:
        if db.get_bind().dialect.name == "postgresql":
            # Ask Postgres for the *age* rather than the timestamp: xact_start is
            # timestamptz and comes back in the session timezone, so subtracting
            # it from utcnow() would be wrong by the UTC offset on any server not
            # running in UTC. An interval is timezone-independent.
            oldest_age_seconds = db.execute(
                text(
                    "SELECT EXTRACT(EPOCH FROM (now() - MIN(xact_start))) "
                    "FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND xact_start IS NOT NULL AND pid <> pg_backend_pid()"
                )
            ).scalar()
    except Exception:
        # Missing privileges / unsupported view: fall back to the fixed lag.
        oldest_age_seconds = None

    if oldest_age_seconds is None:
        return ceiling
    try:
        age = timedelta(seconds=float(oldest_age_seconds))
    except (TypeError, ValueError):
        return ceiling
    if age < timedelta(0):
        # Clock went backwards between statements; trust the fixed lag instead.
        return ceiling
    candidate = now - age - timedelta(microseconds=1)
    return max(floor, min(ceiling, candidate))


def _keyset_filter(model, since_dt: datetime, after_id: str):
    """Rows strictly after (since_dt, after_id) ordered by server_modified, id."""
    if after_id:
        return or_(
            model.server_modified > since_dt,
            and_(model.server_modified == since_dt, model.id > after_id),
        )
    return model.server_modified > since_dt


def _serialize_application(a: Application, id_to_key: dict[str, str]) -> dict:
    return {
        "id": a.id,
        "profile_key": id_to_key.get(a.profile_id) if a.profile_id else None,
        "company_name": a.company_name,
        "position_title": a.position_title,
        "job_link": a.job_link,
        "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
        "status": a.status,
        "account": a.account,
        "resume_file_path": a.resume_file_path,
        "cover_letter_file_path": a.cover_letter_file_path,
        "notes": a.notes,
        "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
        # Cursor field — clients filter on this, not last_modified.
        "server_modified": (
            a.server_modified.isoformat() + "Z" if a.server_modified else None
        ),
        "deleted_at": a.deleted_at.isoformat() + "Z" if a.deleted_at else None,
        "revision": a.revision,
        # Always attributable. The desktop client's push→pull dedupe heal only
        # welds a local row onto a server row it can attribute to the signed-in
        # account; a NULL here makes it skip that check and adopt a teammate's
        # record, silently discarding a brand-new local application. ``user_id``
        # is the last writer and is never NULL, so it is the correct stand-in for
        # rows written before the attribution columns were populated.
        "creator_user_id": a.creator_user_id or a.user_id,
        "last_editor_user_id": a.last_editor_user_id or a.user_id,
    }


@router.post("/push", response_model=SyncPushResponse)
def push(
    payload: SyncPushRequest,
    user: User = Depends(get_current_user),
    _seat: LicenseSeat = Depends(require_active_license),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_app_version: str | None = Header(default=None, alias="X-App-Version"),
    x_os_name: str | None = Header(default=None, alias="X-OS-Name"),
    x_hostname: str | None = Header(default=None, alias="X-Hostname"),
) -> SyncPushResponse:
    total = len(payload.applications) + len(payload.interviews)
    if total > _MAX_PUSH_RECORDS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many records in one request (max {_MAX_PUSH_RECORDS})",
        )
    _enforce_push_rate(user.id, total)

    accepted: list[SyncAccepted] = []
    rejected: list[SyncRejection] = []
    stats = _PushStats()
    user_profiles = get_user_profile_map(db, user.id)
    label_to_id = get_user_profile_label_map(db, user.id)
    user_profile_ids = set(user_profiles.values())

    _acquire_user_sync_lock(db, user.id)
    _acquire_profile_sync_locks(
        db, _targeted_profile_ids(payload, user_profiles, label_to_id)
    )
    _push_applications(
        db, user, payload.applications, accepted, rejected,
        user_profiles, user_profile_ids, stats, label_to_id,
    )
    _push_interviews(
        db, user, payload.interviews, accepted, rejected,
        user_profiles, user_profile_ids, stats, label_to_id,
    )

    touch_client_presence(
        db,
        device_id=x_device_id,
        user_id=user.id,
        app_version=x_app_version or "",
        os_name=x_os_name or "",
        hostname=x_hostname or "",
    )
    db.add(
        SyncAudit(
            user_id=user.id,
            profile_id=stats.primary_profile_id(),
            device_id=(x_device_id or None),
            accepted=stats.accepted,
            rejected_not_assigned=stats.rejected_not_assigned,
            version_conflicts=stats.version_conflicts,
            other_rejected=stats.other_rejected,
            rejected_unscoped=stats.rejected_unscoped,
            revision_conflicts=stats.revision_conflicts,
            rejected_missing_parent=stats.rejected_missing_parent,
            rejected_time_conflict=stats.rejected_time_conflict,
            rejected_error=stats.rejected_error,
            new_inserts=stats.new_inserts,
            apps_in_request=len(payload.applications),
            interviews_in_request=len(payload.interviews),
        )
    )
    # Stamp the pull cursor field as late as possible, then commit immediately.
    db.flush()
    batch_ts = _stamp_server_modified(db, stats)
    db.commit()

    # The stamp->visible window must stay well inside the pull cursor lag, or the
    # race this stamping exists to close reopens. Surface it rather than assume it.
    stamp_gap = (datetime.utcnow() - batch_ts).total_seconds()
    lag = get_settings().sync_cursor_lag_seconds
    if lag and stamp_gap > lag * 0.5:
        logger.error(
            "sync push commit lag %.2fs exceeds half of sync_cursor_lag_seconds "
            "(%ss); pulls may skip rows - raise the lag or shorten the push",
            stamp_gap,
            lag,
        )
    # Everything was rejected because the caller is not assigned to the target
    # profile(s): tell the client to stop re-sending this payload.
    stop_pushing = (
        total > 0
        and stats.accepted == 0
        and stats.rejected_not_assigned == total
    )
    return SyncPushResponse(
        accepted=accepted, rejected=rejected, stop_pushing=stop_pushing
    )


@router.post("/check_ids", response_model=SyncCheckIdsResponse)
def check_ids(
    payload: SyncCheckIdsRequest,
    user: User = Depends(get_current_user),
    _seat: LicenseSeat = Depends(require_active_license),
    db: Session = Depends(get_db),
) -> SyncCheckIdsResponse:
    """Return which client UUIDs are missing (or soft-deleted) for this user.

    Used to recover from server-side hard deletes / orphan purges that left the
    desktop marking rows clean with no pull tombstone to clear them.
    """
    user_profiles = get_user_profile_map(db, user.id)
    user_profile_ids = set(user_profiles.values())

    def _missing(model, ids: list[str]) -> list[str]:
        cleaned = [str(i).strip() for i in ids if i and str(i).strip()]
        if not cleaned:
            return []
        rows = db.execute(
            select(model.id, model.profile_id, model.user_id, model.deleted_at).where(
                model.id.in_(cleaned)
            )
        ).all()
        present: set[str] = set()
        for row_id, profile_id, owner_id, deleted_at in rows:
            if deleted_at is not None:
                continue
            if profile_id is not None and profile_id in user_profile_ids:
                present.add(row_id)
            elif profile_id is None and owner_id == user.id:
                # Legacy personal row still owned by this user.
                present.add(row_id)
        return [i for i in cleaned if i not in present]

    return SyncCheckIdsResponse(
        missing_applications=_missing(Application, payload.application_ids),
        missing_interviews=_missing(Interview, payload.interview_ids),
    )


def _pull_page(
    db: Session,
    user: User,
    *,
    since: str = "1970-01-01T00:00:00Z",
    limit: int = _DEFAULT_PULL_LIMIT,
    after_app_id: str = "",
    after_interview_id: str = "",
    since_apps: str | None = None,
    since_interviews: str | None = None,
) -> dict:
    """One keyset page of the caller's accessible apps + interviews.

    Split out of the ``/pull`` route so the cursor contract can be tested without
    HTTP, auth, or licensing (see ``tests/test_sync_pull_watermark.py``).
    """
    try:
        base_since = _naive(datetime.fromisoformat(since.replace("Z", ""))) or _EPOCH
    except Exception:
        base_since = _EPOCH

    def _parse_since(raw: str | None) -> datetime:
        if not raw:
            return base_since
        try:
            return _naive(datetime.fromisoformat(raw.replace("Z", ""))) or base_since
        except Exception:
            return base_since

    apps_since = _parse_since(since_apps)
    ivs_since = _parse_since(since_interviews)
    # Held behind the wall clock so a push that stamped but has not yet committed
    # cannot end up below the cursor we hand out. See _pull_watermark.
    server_time = _pull_watermark(db).isoformat() + "Z"

    user_profiles = get_user_profile_map(db, user.id)
    id_to_key = {pid: key for key, pid in user_profiles.items()}
    user_profile_ids = set(user_profiles.values())

    app_rows = db.execute(
        select(Application)
        .where(
            _accessible_filter(Application, user, user_profile_ids),
            _keyset_filter(Application, apps_since, after_app_id or ""),
        )
        .order_by(Application.server_modified.asc(), Application.id.asc())
        .limit(limit + 1)
    ).scalars().all()

    iv_rows = db.execute(
        select(Interview)
        .where(
            _accessible_filter(Interview, user, user_profile_ids),
            _keyset_filter(Interview, ivs_since, after_interview_id or ""),
        )
        .order_by(Interview.server_modified.asc(), Interview.id.asc())
        .limit(limit + 1)
    ).scalars().all()

    has_more_apps = len(app_rows) > limit
    has_more_interviews = len(iv_rows) > limit
    app_page = app_rows[:limit]
    iv_page = iv_rows[:limit]

    next_since_apps = apps_since.isoformat() + "Z"
    next_after_app_id = after_app_id or ""
    if app_page:
        last = app_page[-1]
        next_since_apps = (
            last.server_modified.isoformat() + "Z"
            if last.server_modified
            else next_since_apps
        )
        next_after_app_id = last.id

    next_since_interviews = ivs_since.isoformat() + "Z"
    next_after_interview_id = after_interview_id or ""
    if iv_page:
        last = iv_page[-1]
        next_since_interviews = (
            last.server_modified.isoformat() + "Z"
            if last.server_modified
            else next_since_interviews
        )
        next_after_interview_id = last.id

    def _iv_dict(iv: Interview) -> dict:
        d = _serialize_interview(iv)
        d["profile_key"] = id_to_key.get(iv.profile_id) if iv.profile_id else None
        return d

    return {
        "applications": [_serialize_application(a, id_to_key) for a in app_page],
        "interviews": [_iv_dict(iv) for iv in iv_page],
        "server_time": server_time,
        "has_more": has_more_apps or has_more_interviews,
        "has_more_apps": has_more_apps,
        "has_more_interviews": has_more_interviews,
        "next_since_apps": next_since_apps,
        "next_after_app_id": next_after_app_id,
        "next_since_interviews": next_since_interviews,
        "next_after_interview_id": next_after_interview_id,
    }


@router.get("/pull")
def pull(
    since: str = "1970-01-01T00:00:00Z",
    limit: int = Query(default=_DEFAULT_PULL_LIMIT, ge=1, le=_MAX_PULL_LIMIT),
    after_app_id: str = "",
    after_interview_id: str = "",
    since_apps: str | None = None,
    since_interviews: str | None = None,
    user: User = Depends(get_current_user),
    _seat: LicenseSeat = Depends(require_active_license),
    db: Session = Depends(get_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_app_version: str | None = Header(default=None, alias="X-App-Version"),
    x_os_name: str | None = Header(default=None, alias="X-OS-Name"),
    x_hostname: str | None = Header(default=None, alias="X-Hostname"),
) -> dict:
    """Keyset-paginated pull of the caller's accessible apps + interviews.

    Accessible = rows owned by any profile assigned to the caller (shared), plus
    the caller's own personal rows (``profile_id IS NULL``). Use ``since`` for the
    committed client cursor on the first page. Subsequent pages pass ``since_apps``
    / ``after_app_id`` and ``since_interviews`` / ``after_interview_id`` from the
    previous response.

    Cursors are compared against ``server_modified`` (this server's clock), not
    the client-supplied ``last_modified``, so a record written by another account
    is always newer than the cursor of a client that pulled before that write.
    """
    page = _pull_page(
        db,
        user,
        since=since,
        limit=limit,
        after_app_id=after_app_id,
        after_interview_id=after_interview_id,
        since_apps=since_apps,
        since_interviews=since_interviews,
    )

    touch_client_presence(
        db,
        device_id=x_device_id,
        user_id=user.id,
        app_version=x_app_version or "",
        os_name=x_os_name or "",
        hostname=x_hostname or "",
    )
    db.commit()

    return page
