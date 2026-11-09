"""Pydantic request/response models.

Field names match the desktop client's existing wire format (see
``backend/api_client.py`` and ``backend/production_sync.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ----- Auth -----
class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # Website only. The desktop app sends neither: its sessions are long and
    # it stores the refresh token itself.
    remember: bool = True  # "Keep me signed in"
    use_cookie: bool = False  # refresh token in an HttpOnly cookie, not the body


class TokenResponse(BaseModel):
    access_token: str
    # Left out when the refresh token went into the website's cookie.
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int  # seconds
    user_id: str | None = None


class RefreshRequest(BaseModel):
    # Empty: the website's cookie is used instead.
    refresh_token: str = ""


class SignupRequestIn(BaseModel):
    email: EmailStr
    name: str = ""


class SignupPendingResponse(BaseModel):
    status: str = "pending"
    message: str = (
        "Signup submitted. An administrator must issue an OTP and send it to you."
    )


class SignupVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    password: str = Field(min_length=8)


class SignupCompleteResponse(BaseModel):
    status: str = "completed"
    email: str
    message: str = "Account created. Sign in with your email and password."


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    avatar_url: str | None = None
    # Member may read everyone's work plan and comment (see can_view_others_work_plans).
    can_review_work_plans: bool = False

    class Config:
        from_attributes = True


class UserUpdateMe(BaseModel):
    """Self-service profile update. Email change requires ``current_password``."""

    name: str | None = None
    email: EmailStr | None = None
    current_password: str | None = None


class UserChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# ----- Applications -----
class ApplicationCreate(BaseModel):
    company_name: str = "Unknown"
    position_title: str = "Unknown Position"
    job_link: str = ""
    applied_date: datetime | None = None
    status: str = "applied"
    account: str | None = None
    resume_file_path: str | None = None
    cover_letter_file_path: str | None = None
    notes: str | None = None


class ApplicationOut(BaseModel):
    id: str
    company_name: str
    position_title: str
    job_link: str
    applied_date: datetime | None
    status: str
    account: str | None
    resume_file_path: str | None
    cover_letter_file_path: str | None
    notes: str | None
    last_modified: datetime

    class Config:
        from_attributes = True


# ----- Sync -----
class SyncApplication(BaseModel):
    id: str
    # Shared-dataset key. When set and the caller is assigned to it, the record
    # is owned by that profile (shared across accounts). Omitted/empty means
    # personal data scoped to the caller (legacy clients).
    profile_key: str | None = None
    company_name: str = "Unknown"
    position_title: str = "Unknown Position"
    job_link: str = ""
    applied_date: datetime | None = None
    status: str = "applied"
    account: str | None = None
    resume_file_path: str | None = None
    resume_version: str | None = None
    cover_letter_file_path: str | None = None
    notes: str | None = None
    last_modified: datetime
    deleted_at: datetime | None = None
    # Revision observed by the editing client. None keeps legacy clients
    # timestamp-compatible; revision-aware clients must match the server value.
    base_revision: int | None = Field(default=None, ge=1)


class SyncInterview(BaseModel):
    id: str
    profile_key: str | None = None
    application_id: str | None = None
    title: str = "Interview"
    description: str | None = None
    start_time_utc: datetime
    end_time_utc: datetime
    timezone: str = "EST"
    location: str | None = None
    interviewer_name: str | None = None
    interviewer_email: str | None = None
    interview_type: str | None = None
    notes: str | None = None
    status: str = "scheduled"
    # Pipeline fields. All default to None so a client build that predates them
    # round-trips unchanged - the push handler treats None as "not sent" and
    # leaves the stored value alone rather than blanking it.
    stage: str | None = None
    round_index: int | None = None
    outcome: str | None = None
    outcome_at: datetime | None = None
    next_step: str | None = None
    next_step_due: datetime | None = None
    last_modified: datetime
    deleted_at: datetime | None = None
    force_override: bool = False
    base_revision: int | None = Field(default=None, ge=1)


class SyncPushRequest(BaseModel):
    applications: list[SyncApplication] = Field(default_factory=list)
    interviews: list[SyncInterview] = Field(default_factory=list)


class SyncRejection(BaseModel):
    id: str
    reason: str
    status: str = "rejected"
    # The server's current revision for this record, set on version/revision
    # conflicts. Without it a client whose local ``server_revision`` has drifted
    # stale can never recover: a rejected push does not bump ``server_modified``,
    # so the row is not re-offered by any later cursor-filtered pull, and every
    # subsequent push repeats the same stale ``base_revision`` and is rejected
    # again. Optional so older clients (which ignore it) keep working unchanged.
    server_revision: int | None = None


class SyncAccepted(BaseModel):
    id: str
    # Set when the pushed record was merged into an existing row of the same
    # shared profile (two accounts logged the same application before either
    # pulled). The client must adopt this UUID for its local row.
    canonical_id: str | None = None
    revision: int | None = None


class SyncPushResponse(BaseModel):
    accepted: list[SyncAccepted]
    rejected: list[SyncRejection]
    # Hint to the client to stop re-pushing this payload: every record was
    # rejected because it targets a profile the caller is not assigned to.
    stop_pushing: bool = False


class SyncCheckIdsRequest(BaseModel):
    """Ask which of these UUIDs are missing (or soft-deleted) on the server."""

    application_ids: list[str] = Field(default_factory=list, max_length=500)
    interview_ids: list[str] = Field(default_factory=list, max_length=500)


class SyncCheckIdsResponse(BaseModel):
    missing_applications: list[str] = Field(default_factory=list)
    missing_interviews: list[str] = Field(default_factory=list)


class SyncPullResponse(BaseModel):
    applications: list[dict[str, Any]]
    interviews: list[dict[str, Any]]
    server_time: str
    has_more: bool = False
    has_more_apps: bool = False
    has_more_interviews: bool = False
    next_since_apps: str | None = None
    next_after_app_id: str | None = None
    next_since_interviews: str | None = None
    next_after_interview_id: str | None = None


# ----- Profiles -----
class ProfileOut(BaseModel):
    key: str
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    university_name: str = ""
    university_location: str = ""
    degree: str = ""
    education_period: str = ""
    # Optional second education history (e.g. a Master's after a Bachelor's).
    university_name_2: str = ""
    university_location_2: str = ""
    degree_2: str = ""
    education_period_2: str = ""
    default_save_path: str = ""
    resume_filename: str = ""
    cover_letter_filename: str = ""

    class Config:
        from_attributes = True


class ProfileUpsert(ProfileOut):
    pass


class ProfileAdminUpsert(BaseModel):
    """Admin/member-editable fields. The save-path stays desktop-client-only."""

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    university_name: str = ""
    university_location: str = ""
    degree: str = ""
    education_period: str = ""
    university_name_2: str = ""
    university_location_2: str = ""
    degree_2: str = ""
    education_period_2: str = ""
    resume_filename: str = ""
    cover_letter_filename: str = ""


class ProfileAdminOut(ProfileOut):
    assigned_user_ids: list[str] = []
    archived_at: datetime | None = None


class ProfileAssignees(BaseModel):
    user_ids: list[str] = []


class MemberProfileSupporters(BaseModel):
    supporter_ids: list[str] = []


class MemberAssignments(BaseModel):
    """Grid save: ``{profile_key: [supporter_id, ...]}`` for the member's profiles."""

    assignments: dict[str, list[str]] = {}



# ----- License -----
class MachineInfo(BaseModel):
    """What an app says about the machine it runs on (all optional)."""

    hostname: str | None = None
    os_name: str | None = None  # e.g. "Windows 11 Pro"
    os_version: str | None = None  # e.g. "10.0.26200"
    app_version: str | None = None
    os_user: str | None = None  # Windows user name


class LicenseActivateRequest(MachineInfo):
    license_key: str
    hardware_fingerprint: str
    device_id: str | None = None  # optional fleet linkage


class LicenseActivateResponse(BaseModel):
    valid: bool
    # Machine-readable reason when valid is False (see server/license_service.py).
    code: str | None = None
    reason: str | None = None
    product: str | None = None
    licensed_to: str | None = None
    lease_token: str | None = None
    seat_secret: str | None = None
    lease_expires_at: datetime | None = None
    license_expires_at: datetime | None = None
    server_time: datetime | None = None
    plan: str | None = None
    features: list[Any] | dict[str, Any] | None = None
    seat_limit: int | None = None
    seats_used: int | None = None
    # Public key for offline verification (also available at GET .../public-keys).
    public_key_pem: str | None = None
    kid: str | None = None


class LicenseRefreshRequest(MachineInfo):
    hardware_fingerprint: str
    seat_secret: str
    lease_token: str | None = None
    device_id: str | None = None


class LicenseRefreshResponse(BaseModel):
    valid: bool
    code: str | None = None
    reason: str | None = None
    product: str | None = None
    licensed_to: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    license_expires_at: datetime | None = None
    server_time: datetime | None = None
    plan: str | None = None
    features: list[Any] | dict[str, Any] | None = None
    public_key_pem: str | None = None
    kid: str | None = None


class LicenseReleaseRequest(BaseModel):
    hardware_fingerprint: str
    seat_secret: str


class LicenseReleaseResponse(BaseModel):
    valid: bool
    code: str | None = None
    reason: str | None = None


class LicenseCreateRequest(BaseModel):
    product: str = "careercraft"
    label: str = ""
    customer: str = ""
    notes: str = ""
    seat_limit: int = 1
    plan: str = "standard"
    features: list[Any] | dict[str, Any] | None = None
    offline_days: int | None = None
    grace_days: int | None = None  # old name for offline_days
    expires_at: datetime | None = None


class LicenseUpdateRequest(BaseModel):
    """Only the fields sent are changed; send expires_at/offline_days null to clear."""

    label: str | None = None
    customer: str | None = None
    notes: str | None = None
    plan: str | None = None
    seat_limit: int | None = None
    offline_days: int | None = None
    expires_at: datetime | None = None


class LicenseExtendRequest(BaseModel):
    days: int = Field(ge=1, le=3650)


# ----- Fleet / telemetry / errors -----
class TelemetryEventIn(BaseModel):
    event: str
    properties: dict[str, Any] | None = None


class TelemetryBatch(BaseModel):
    device_id: str
    app_version: str = ""
    events: list[TelemetryEventIn] = Field(default_factory=list)


class ErrorReportIn(BaseModel):
    device_id: str
    app_version: str = ""
    error_type: str = ""
    message: str = ""
    traceback: str | None = None


class ConsentIn(BaseModel):
    device_id: str
    policy_version: str = "1.0"
    accepted: bool = True


# ----- Releases -----
class ReleaseOut(BaseModel):
    version: str
    url: str
    sha256: str
    mandatory: bool
    notes: str

    class Config:
        from_attributes = True


# ----- Work plans -----
class WorkPlanItemCreate(BaseModel):
    horizon: str  # week | month | quarter
    period_start: str  # YYYY-MM-DD
    title: str = Field(min_length=1, max_length=512)
    contents: str | None = None
    category: str | None = None  # required for week
    user_id: str | None = None  # admin only: create for another user


class WorkPlanItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    contents: str | None = None
    done: bool | None = None
    category: str | None = None
    sort_order: int | None = None


class WorkPlanItemOut(BaseModel):
    id: str
    user_id: str
    horizon: str
    period_start: str
    category: str | None = None
    title: str
    contents: str | None = None
    done: bool
    sort_order: int
    comment_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class WorkPlanCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


class WorkPlanCommentOut(BaseModel):
    id: str
    item_id: str
    author_id: str
    author_name: str = ""
    author_email: str = ""
    author_role: str = "member"
    body: str
    created_at: datetime | None = None

    class Config:
        from_attributes = True
