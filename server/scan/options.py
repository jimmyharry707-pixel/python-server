"""The options a reviewer sets before a scan runs."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .names import parse_sites

DEFAULT_MARKERS = [
    "[...]",  # special: any text in square brackets, e.g. "[need to verify]"
    "need to verify",
    "not verified",
    "TBD",
    "TODO",
    "Lorem ipsum",
    "Company Name",
    "Job Title",
    "Hiring Manager Name",
    "**",
    "###",
]
# Dice jobs are applied on Dice with the candidate's Dice login, so Dice
# isn't on the list.
DEFAULT_SITES = ["linkedin.com", "indeed.com", "turing.com"]


class ScanOptions(BaseModel):
    """Every check can be tuned or switched off for one scan."""

    # "Twice check period": another application to a company with a matching
    # name for the same candidate within this many days is shown as a warning.
    # A matching job link is shown however long ago it was.
    duplicate_window_days: int = Field(30, ge=1, le=365)
    # Whether an application exactly duplicate_window_days ago still counts.
    window_includes_last_day: bool = True
    # The tracker link must be the Jobright job link (bidder training 6.2).
    require_jobright_link: bool = False
    # Supporters may apply through Jobright's Apply button (links carrying
    # Jobright's jr_id tag). When off, those applications are flagged.
    allow_jobright_apply: bool = True
    excluded_sites: list[str] = Field(default_factory=lambda: list(DEFAULT_SITES), max_length=50)
    # The same-company rule applies to end clients; these names are skipped.
    staffing_agencies: list[str] = Field(default_factory=list, max_length=200)
    leftover_markers: list[str] = Field(default_factory=lambda: list(DEFAULT_MARKERS), max_length=60)
    resume_max_pages: int = Field(2, ge=0, le=10)  # 0 = don't check
    cover_letter_max_pages: int = Field(1, ge=0, le=10)
    # Flag consent, signature and background-check answers for the reviewer.
    flag_consent_answers: bool = True
    # Flag different answers to the same question on different jobs that day
    # (work authorization, sponsorship, state, referral).
    check_answer_consistency: bool = True
    # A form saved before its resume was made: the resume in the ZIP may not
    # be the one that was uploaded.
    check_capture_order: bool = True
    # Note applications made sooner than this after the previous one. 0 = off.
    pace_minutes: int = Field(4, ge=0, le=240)

    @field_validator("excluded_sites")
    @classmethod
    def _sites(cls, value: list[str]) -> list[str]:
        return parse_sites(value)

    @field_validator("staffing_agencies", "leftover_markers")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            item = " ".join(str(item).split())[:120]
            if item and item.lower() not in {o.lower() for o in out}:
                out.append(item)
        return out


def merge_defaults(saved: dict | None) -> ScanOptions:
    """Saved options on top of the defaults, ignoring keys that no longer exist."""
    data = {k: v for k, v in (saved or {}).items() if k in ScanOptions.model_fields}
    try:
        return ScanOptions(**data)
    except ValueError:
        return ScanOptions()
