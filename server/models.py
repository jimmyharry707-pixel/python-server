"""ORM models for the Baymax central server.

Covers user accounts, per-user application/interview data (with UUID primary
keys to match the client's sync format), admin-provisioned profiles, licensing
(seats bound to devices), and fleet monitoring (presence via last_seen,
telemetry, errors), plus consent records and release manifests.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")  # admin | manager | supporter | member
    # False = archived: can't sign in and is hidden from every people list,
    # picker and ranking. Their tracked data still counts in profile totals.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # IANA zone (e.g. "Asia/Kolkata"), admin-set. Lets the Colleagues tab bucket a
    # supporter's own activity by her local day/hour instead of the viewer's.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    # Supporters only: the one member they work for. Set by an admin/manager;
    # the member can then assign this supporter to their own profiles.
    member_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    # Members only, set by an admin/manager: may read everyone's work plan and
    # comment on it, but not change it. Ignored for other roles.
    can_review_work_plans: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", foreign_keys="Application.user_id"
    )
    interviews: Mapped[list["Interview"]] = relationship(
        back_populates="user", foreign_keys="Interview.user_id"
    )
    work_plan_items: Mapped[list["WorkPlanItem"]] = relationship(back_populates="user")
    work_plan_comments: Mapped[list["WorkPlanComment"]] = relationship(
        back_populates="author"
    )


class AuthSession(Base):
    """One sign-in: a browser or a desktop app (see server/auth_sessions.py).

    The refresh token is signed from ``id``, ``generation`` and ``salt`` with
    the server secret, so the table never holds a usable token. Each renewal
    bumps ``generation``; an older token showing up again ends the session.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client: Mapped[str] = mapped_column(String(16), nullable=False, default="desktop")  # web | desktop
    remember: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    rotated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Ends at whichever comes first.
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class SignupRequest(Base):
    """App-initiated signup awaiting admin-issued OTP verification.

    A User row is created only after successful OTP verify.
    status: pending | otp_issued | completed | rejected
    """

    __tablename__ = "signup_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    otp_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    otp_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_profile_keyset", "profile_id", "server_modified", "id"),
        # Lookup for merging concurrent duplicate creates within one profile.
        Index("ix_applications_profile_company", "profile_id", "company_name"),
        Index("ix_applications_profile_dedup", "profile_id", "job_link"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # ``user_id`` is retained as the "last editor / creator" for audit; data
    # ownership for sync is by ``profile_id`` (shared across assigned accounts).
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    creator_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    last_editor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("profiles.id"), index=True, nullable=True
    )

    company_name: Mapped[str] = mapped_column(String(512), default="Unknown")
    position_title: Mapped[str] = mapped_column(String(512), default="Unknown Position")
    job_link: Mapped[str] = mapped_column(Text, default="")
    applied_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="applied")
    account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resume_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cover_letter_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Client-supplied edit time; drives last-write-wins conflict resolution.
    last_modified: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    # Server-side write time; drives the incremental pull cursor. A record can
    # arrive with an older ``last_modified`` than a peer's pull cursor (clients
    # edit offline, clocks differ), so pulling by ``last_modified`` would skip
    # it forever.
    server_modified: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # When the record was made on the PC, as far as the server can tell: the
    # client edit time of the first version it received. ``created_at`` is the
    # server's insert time, which bunches up when an offline PC syncs a batch.
    # Used by the daily review scan's pace check.
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(
        back_populates="applications", foreign_keys=[user_id]
    )


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (
        Index("ix_interviews_profile_keyset", "profile_id", "server_modified", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    creator_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    last_editor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("profiles.id"), index=True, nullable=True
    )
    application_id: Mapped[str | None] = mapped_column(
        ForeignKey("applications.id"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(512), default="Interview")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time_utc: Mapped[datetime] = mapped_column(DateTime)
    timezone: Mapped[str] = mapped_column(String(16), default="EST")
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    interviewer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interviewer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interview_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Did the meeting happen: scheduled | completed | cancelled | rescheduled.
    status: Mapped[str] = mapped_column(String(32), default="scheduled")

    # --- Pipeline position (see ``server.pipeline``) ---
    # Ladder rung derived from ``interview_type``; NULL when unclassified.
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 1-based position within the application, by start time.
    round_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Did it advance the application - a separate question from ``status``:
    # pending | passed | failed | no_response | withdrew.
    outcome: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_step_due: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_modified: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    # Server-side write time driving the pull cursor (see Application).
    server_modified: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped[User] = relationship(
        back_populates="interviews", foreign_keys=[user_id]
    )


class WorkPlanItem(Base):
    """Per-user work-plan todo for a week, month, or calendar quarter."""

    __tablename__ = "work_plan_items"
    __table_args__ = (
        Index("ix_work_plan_items_user_period", "user_id", "horizon", "period_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)

    # week | month | quarter
    horizon: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # YYYY-MM-DD: week Saturday, month 1st, quarter 1st (Jan/Apr/Jul/Oct)
    period_start: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    # Required for week: current_job | self_study | work_plans. Null for month/quarter.
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    contents: Mapped[str | None] = mapped_column(Text, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    user: Mapped[User] = relationship(back_populates="work_plan_items")
    comments: Mapped[list["WorkPlanComment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class WorkPlanComment(Base):
    """Comment on a work-plan item by the plan owner or an admin."""

    __tablename__ = "work_plan_comments"
    __table_args__ = (
        Index("ix_work_plan_comments_item", "item_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("work_plan_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    item: Mapped[WorkPlanItem] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="work_plan_comments")


class Profile(Base):
    """A resume profile that an admin can provision to users."""

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # ``key`` is the filename the client writes (config/profiles/<key>.json)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    linkedin: Mapped[str] = mapped_column(String(512), default="")
    university_name: Mapped[str] = mapped_column(String(255), default="")
    university_location: Mapped[str] = mapped_column(String(255), default="")
    degree: Mapped[str] = mapped_column(String(255), default="")
    education_period: Mapped[str] = mapped_column(String(128), default="")
    # Optional second education history (e.g. a Master's after a Bachelor's).
    # Blank means the profile has only one entry; never required.
    university_name_2: Mapped[str] = mapped_column(String(255), default="")
    university_location_2: Mapped[str] = mapped_column(String(255), default="")
    degree_2: Mapped[str] = mapped_column(String(255), default="")
    education_period_2: Mapped[str] = mapped_column(String(128), default="")
    default_save_path: Mapped[str] = mapped_column(Text, default="")
    # Blank means "derive FirstName_LastName_resume / _cover_letter from name"
    # (see gui/profile/store.py:effective_document_filenames).
    resume_filename: Mapped[str] = mapped_column(String(255), default="")
    cover_letter_filename: Mapped[str] = mapped_column(String(255), default="")
    # Archived profiles keep every application/interview but are no longer
    # provisioned to the desktop app or listed on members' pages.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProfileAssignment(Base):
    __tablename__ = "profile_assignments"
    __table_args__ = (UniqueConstraint("user_id", "profile_id", name="uq_user_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MemberAvatarOverride(Base):
    """A member's private display avatar for a supporter."""

    __tablename__ = "member_avatar_overrides"
    __table_args__ = (
        UniqueConstraint("member_id", "supporter_id", name="uq_member_supporter_avatar"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    supporter_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SyncAudit(Base):
    """One row per ``POST /sync/push`` for admin monitoring of sync activity.

    Records who pushed, on behalf of which profile, from which device, and the
    per-push outcome counts so the admin site can spot anomalies (epoch
    re-push storms, cross-account ``not_assigned`` attempts, duplicate spikes).
    """

    __tablename__ = "sync_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Coarse buckets (kept for backward compat with existing readers). Each
    # one conflates two or three distinct rejection reasons - see the finer
    # counters below, incremented alongside these at the same call sites.
    rejected_not_assigned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version_conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    other_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # rejected_not_assigned split: a row with no profile link at all (client
    # bug) vs. a genuine cross-account/permissions rejection.
    rejected_unscoped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # version_conflicts split: optimistic-concurrency revision mismatch vs.
    # the timestamp-based "server has newer version" check.
    revision_conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # other_rejected split into its three distinct causes.
    rejected_missing_parent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_time_conflict: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_error: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # New inserts on this push. A large count on a non-first sync is the main
    # anomaly signal for epoch re-push storms / cross-account duplication.
    new_inserts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    apps_in_request: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interviews_in_request: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Which app the key unlocks (see server/products.py).
    product: Mapped[str] = mapped_column(String(32), default="careercraft", index=True)
    # Raw key is shown once at issuance; only hash + prefix are stored.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), default="", index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    # Who the key was issued to; shown to the app as "Licensed to".
    customer: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The key's color on the admin site (see server/key_colors.py): one
    # customer's keys get different colors.
    color: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan: Mapped[str] = mapped_column(String(64), default="standard")
    features: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list/dict
    seat_limit: Mapped[int] = mapped_column(Integer, default=1)
    # False = suspended by an admin.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Offline days: how long a lease lets the app run without reaching the
    # server. NULL -> server default. (Named grace_days before it was clear.)
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    seats: Mapped[list["LicenseSeat"]] = relationship(back_populates="license")


class LicenseSeat(Base):
    __tablename__ = "license_seats"
    __table_args__ = (
        UniqueConstraint("license_id", "hardware_fingerprint", name="uq_license_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    # Hardware-derived fingerprint (preferred). device_id kept for fleet linkage.
    hardware_fingerprint: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default="")
    # SHA-256 of the one-time seat secret returned at activation.
    seat_secret_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    token_jti: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # revoked = the seat no longer counts (released). blocked = also barred:
    # this machine can't activate the key again until an admin releases it.
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The machine as the app last described it.
    hostname: Mapped[str] = mapped_column(String(255), default="")
    os_name: Mapped[str] = mapped_column(String(128), default="")
    os_version: Mapped[str] = mapped_column(String(128), default="")
    app_version: Mapped[str] = mapped_column(String(64), default="")
    os_user: Mapped[str] = mapped_column(String(255), default="")
    activated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Last activate/refresh, refused or not.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    license: Mapped[License] = relationship(back_populates="seats")


class LicenseSeatUser(Base):
    """Everyone seen using a licensed machine, with when they were first and last seen.

    CareerCraft records the signed-in account; Sidekick and SnowLink have no
    accounts, so they record the Windows user name. ``identity`` is
    ``user:<id>`` or ``os:<lower-cased name>``: one row per person per machine.
    """

    __tablename__ = "license_seat_users"
    __table_args__ = (UniqueConstraint("seat_id", "identity", name="uq_seat_user_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("license_seats.id"), index=True, nullable=False)
    identity: Mapped[str] = mapped_column(String(300), nullable=False)
    # Soft reference (no FK): deleting a user keeps the history row.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    os_user: Mapped[str] = mapped_column(String(255), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LicenseAudit(Base):
    """Append-only audit trail for license activations, refreshes, and admin actions."""

    __tablename__ = "license_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    product: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    license_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    seat_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Client(Base):
    """A registered desktop install, keyed by device_id (fleet monitoring).

    ``last_seen`` is refreshed on sync push/pull (and license activate).
    """

    __tablename__ = "clients"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    app_version: Mapped[str] = mapped_column(String(64), default="")
    os_name: Mapped[str] = mapped_column(String(128), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(128), index=True)
    app_version: Mapped[str] = mapped_column(String(64), default="")
    properties: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string, metadata only
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ErrorReport(Base):
    __tablename__ = "error_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    app_version: Mapped[str] = mapped_column(String(64), default="")
    error_type: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    policy_version: Mapped[str] = mapped_column(String(32), default="1.0")
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), default="stable", index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class TrainingLesson(Base):
    """One step of the supporter training course, authored as Markdown."""

    __tablename__ = "training_lessons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(96), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class TrainingAsset(Base):
    """Image referenced from lesson Markdown, stored in the DB so it ships with backups."""

    __tablename__ = "training_assets"

    # The sanitized filename doubles as the key Markdown links use.
    name: Mapped[str] = mapped_column(String(160), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class TrainingProgress(Base):
    """A lesson a user has marked complete."""

    __tablename__ = "training_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_training_progress"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("training_lessons.id", ondelete="CASCADE"), index=True, nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Announcement(Base):
    """A staff-authored notice (Markdown) shown to members only."""

    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AnnouncementRead(Base):
    """An announcement a member has opened or dismissed."""

    __tablename__ = "announcement_reads"
    __table_args__ = (UniqueConstraint("user_id", "announcement_id", name="uq_announcement_read"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    announcement_id: Mapped[str] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE"), index=True, nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(DateTime, default=_now)



class ReviewSettings(Base):
    """The scan options used last (one row, id=1), offered as the defaults for
    the next daily review scan. Stored as JSON so options can grow freely."""

    __tablename__ = "review_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    options: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReviewScan(Base):
    """One reviewer checking one supporter's work for one day.

    The uploaded ZIPs live on disk only while the scan is open; finishing,
    cancelling or 48 hours without activity deletes them. The text report
    stays.
    """

    __tablename__ = "review_scans"
    __table_args__ = (Index("ix_review_scans_reviewer_status", "reviewer_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    supporter_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    work_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    # uploaded -> queued -> scanning -> ready -> finished; or cancelled / failed / expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    options: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    upload_info: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    day_findings: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    report_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The reviewer's overall comment for the day, printed at the top of the report.
    reviewer_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Last time anyone worked on the scan; files expire 48 h after this.
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    files_deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewScanJob(Base):
    """One job (application) in a scan: its tracker records, resume folder,
    captures and what the automatic checks found."""

    __tablename__ = "review_scan_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(
        ForeignKey("review_scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    company: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  # worst finding
    # JSON: tracker records, resume/cover files, captures, findings, answers.
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    findings: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Set by the reviewer in the review screen.
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
