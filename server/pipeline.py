"""Interview pipeline vocabulary and per-application derivation.

An interview's ``interview_type`` is a free-text label; ``stage`` is its rung on
an ordered ladder, which is what makes "how far along is this application"
answerable. ``status`` records whether a meeting happened; ``outcome`` records
whether it advanced the application - two separate questions that were
previously conflated into one field, which is why the pipeline could not be
measured at all.

The ladder here is deliberately duplicated from the desktop client's
``interview_service``: the server image copies only ``server/``, so it cannot
import that module. Change both together.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

STAGE_ORDER = (
    "screen",
    "hiring_manager",
    "technical",
    "behavioral",
    "onsite",
    "final",
    "offer",
)

STAGE_LABELS = {
    "screen": "Screen",
    "hiring_manager": "Hiring Manager",
    "technical": "Technical",
    "behavioral": "Behavioral",
    "onsite": "Onsite / Panel",
    "final": "Final",
    "offer": "Offer",
}

# 1-based rung number; 0 is "applied, no interview yet".
STAGE_INDEX = {key: i + 1 for i, key in enumerate(STAGE_ORDER)}

TYPE_TO_STAGE = {
    "Intro Call": "screen",
    "Recruiter Screen": "screen",
    "Phone Screen": "screen",
    "HR Screen": "screen",
    "Hiring Manager": "hiring_manager",
    "Technical": "technical",
    "Coding": "technical",
    "System Design": "technical",
    "Take-Home": "technical",
    "Pair Programming": "technical",
    "Behavioral": "behavioral",
    "Culture Fit": "behavioral",
    "Case Study": "behavioral",
    "Portfolio Review": "behavioral",
    "Presentation": "behavioral",
    "Panel": "onsite",
    "On-site": "onsite",
    "Final Round": "final",
    "Offer Discussion": "offer",
}

INTERVIEW_OUTCOMES = ("pending", "passed", "failed", "no_response", "withdrew")

INTERVIEW_OUTCOME_LABELS = {
    "pending": "Pending",
    "passed": "Passed",
    "failed": "Did not pass",
    "no_response": "No response",
    "withdrew": "Withdrew",
}

# Outcomes that end the pursuit at that rung.
TERMINAL_OUTCOMES = frozenset({"failed", "no_response", "withdrew"})

# The application's own status, in the desktop client's canonical casing (see
# ``JobTrackerDB.STATUS_CANONICAL``) so a value written from the web is
# byte-identical to one written from the desktop and syncs without churn.
APPLICATION_STATUSES = (
    "Applied",
    "Phone Screen",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
)

# Statuses that record a human decision to end the pursuit. These override any
# state inferred from the interviews - an explicit judgement beats a guess.
TERMINAL_APPLICATION_STATUSES = {
    "offer": "offer",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
}

# An application with no activity for this long, and nothing scheduled ahead of
# it, is quietly dying rather than progressing.
STALLED_AFTER_DAYS = 21


# What a terminal application status implies about the *final* round. Applied /
# Phone Screen / Interview say nothing about how the last round went - the
# pursuit is still open - so they leave it pending rather than guessing.
# Retained only for the sync schema's vocabulary; nothing infers an outcome
# from an application status any more. See the note in ``interview_service``.
_FINAL_ROUND_OUTCOME_BY_APP_STATUS = {
    "rejected": "failed",
    "offer": "passed",
    "withdrawn": "withdrew",
}


def final_round_outcome_for(application_status):
    """Outcome the application status implies for the last round.

    Returns 'pending' for any non-terminal status: an open pursuit has not
    judged its most recent round yet.
    """
    key = str(application_status or "").strip().lower()
    return FINAL_ROUND_OUTCOME_BY_APP_STATUS.get(key, "pending")


def stage_for_type(interview_type: Any) -> str | None:
    """Map a free-text interview type onto its ladder rung (None = unclassified)."""
    return TYPE_TO_STAGE.get(str(interview_type or "").strip())


def normalize_stage(value: Any) -> str | None:
    """Canonical stage key, or None when absent/unrecognized."""
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in STAGE_INDEX else None


def stage_label(value: Any) -> str:
    """Human-readable stage name; unclassified rows read as 'Other'."""
    key = normalize_stage(value)
    return STAGE_LABELS[key] if key else "Other"


def stage_index(value: Any) -> int:
    """1-based rung number; 0 for unclassified so it sorts below every stage."""
    key = normalize_stage(value)
    return STAGE_INDEX[key] if key else 0


def normalize_application_status(value: Any) -> str | None:
    """Canonical application status, or None when unrecognized.

    Matches case-insensitively but returns the desktop's casing, so a web edit
    and a desktop edit write the same string.
    """
    key = str(value or "").strip().lower()
    for canonical in APPLICATION_STATUSES:
        if canonical.lower() == key:
            return canonical
    return None


def normalize_outcome(value: Any) -> str:
    """Canonical outcome, defaulting to 'pending' (not yet judged)."""
    key = str(value or "").strip().lower()
    return key if key in INTERVIEW_OUTCOMES else "pending"


def outcome_label(value: Any) -> str:
    """Human-readable label for an interview outcome."""
    return INTERVIEW_OUTCOME_LABELS[normalize_outcome(value)]


def _sort_key(step: dict) -> tuple:
    """Order steps by when they happen, falling back to the stored round."""
    start = step.get("start_time_utc")
    return (start or datetime.max, step.get("round_index") or 0)


def summarize_application(
    steps: Iterable[dict],
    *,
    application_status: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Reduce one application's interviews to its pipeline position.

    ``steps`` are dicts with ``stage``, ``round_index``, ``status``, ``outcome``
    and ``start_time_utc`` (naive UTC, the server storage convention). Returns
    the summary the pipeline board and list views are built from.
    """
    now = now or datetime.utcnow()
    ordered = sorted(list(steps), key=_sort_key)

    for position, step in enumerate(ordered, start=1):
        # The stored round_index can be stale (an interview inserted between two
        # others, or rescheduled across another round). Position in the sorted
        # sequence is the truth; the column is a cache for sorting in SQL.
        step["round_index"] = position
        step["stage_index"] = stage_index(step.get("stage"))
        step["outcome"] = normalize_outcome(step.get("outcome"))

    passed = [s for s in ordered if s["outcome"] == "passed"]
    terminal = [s for s in ordered if s["outcome"] in TERMINAL_OUTCOMES]
    scheduled_ahead = [
        s
        for s in ordered
        if str(s.get("status") or "").lower() == "scheduled"
        and (s.get("start_time_utc") or datetime.min) >= now
    ]

    # Furthest rung reached, not merely the latest one chronologically: a
    # follow-up screen scheduled after an onsite must not read as going
    # backwards.
    furthest = max((s["stage_index"] for s in ordered), default=0)
    current = next(
        (s for s in reversed(ordered) if s["stage_index"] == furthest), None
    )

    # Only rounds that have actually happened count as activity. Including a
    # future booking would report a negative "days quiet", and a pursuit is not
    # made livelier by something that has yet to occur - what keeps it active is
    # ``scheduled_ahead``, tracked separately.
    last_activity = max(
        (
            s["start_time_utc"]
            for s in ordered
            if s.get("start_time_utc") and s["start_time_utc"] <= now
        ),
        default=None,
    )

    state = _pipeline_state(
        ordered,
        application_status=application_status,
        terminal=terminal,
        scheduled_ahead=scheduled_ahead,
        last_activity=last_activity,
        now=now,
    )

    return {
        "current_stage": current.get("stage") if current else None,
        "current_stage_label": stage_label(current.get("stage")) if current else None,
        "current_stage_index": furthest,
        "current_round": current.get("round_index") if current else 0,
        "rounds_total": len(ordered),
        "rounds_passed": len(passed),
        "rounds_pending": sum(1 for s in ordered if s["outcome"] == "pending"),
        "pipeline_state": state,
        "next_scheduled": (
            scheduled_ahead[0]["start_time_utc"] if scheduled_ahead else None
        ),
        "last_activity": last_activity,
        "days_since_activity": (
            (now - last_activity).days if last_activity else None
        ),
        "next_step": next(
            (s.get("next_step") for s in reversed(ordered) if s.get("next_step")), None
        ),
        "next_step_due": next(
            (
                s.get("next_step_due")
                for s in reversed(ordered)
                if s.get("next_step_due")
            ),
            None,
        ),
        "steps": ordered,
    }


def _pipeline_state(
    ordered: list[dict],
    *,
    application_status: str | None,
    terminal: list[dict],
    scheduled_ahead: list[dict],
    last_activity: datetime | None,
    now: datetime,
) -> str:
    """Where this pursuit stands: active, stalled, offer, rejected, withdrawn.

    The application's own status wins when it records a terminal decision - it
    is the explicit human judgement. Otherwise the state is read off the
    interviews, and 'stalled' is inferred: nothing scheduled ahead and no
    activity for weeks is a pursuit quietly dying, which nothing in the product
    surfaced before.
    """
    app_status = str(application_status or "").strip().lower()
    if app_status in TERMINAL_APPLICATION_STATUSES:
        return TERMINAL_APPLICATION_STATUSES[app_status]

    if any(s["outcome"] == "passed" and s["stage_index"] == STAGE_INDEX["offer"] for s in ordered):
        return "offer"
    if terminal and terminal[-1] is ordered[-1]:
        # Only the *most recent* round ending badly closes the pursuit; an
        # earlier withdrawal followed by more rounds means it resumed.
        return "withdrawn" if terminal[-1]["outcome"] == "withdrew" else "rejected"
    if scheduled_ahead:
        return "active"
    if last_activity and now - last_activity > timedelta(days=STALLED_AFTER_DAYS):
        return "stalled"
    return "active"
