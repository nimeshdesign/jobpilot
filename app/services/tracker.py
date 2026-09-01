"""Post-application tracking: what happened after you hit submit.

The automation status (queued -> submitted) answers "did the robot do its job".
This answers the question that matters weeks later: is anyone replying, how far
did each application get, and which ones have gone quiet long enough to chase.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import models

# key, label, stage (position in the funnel), is_terminal
# Stage 0 means "no reply yet"; anything above 0 counts as a response.
OUTCOMES: list[tuple[str, str, int, bool]] = [
    ("applied", "Applied — waiting", 0, False),
    ("acknowledged", "Acknowledged", 1, False),
    ("screening", "Screening call", 2, False),
    ("interview", "Interview", 3, False),
    ("final", "Final round", 4, False),
    ("offer", "Offer", 5, True),
    ("accepted", "Accepted", 6, True),
    ("rejected", "Rejected", 0, True),
    ("withdrawn", "Withdrawn", 0, True),
    ("ghosted", "No response — closed", 0, True),
]

BY_KEY = {key: (label, stage, terminal) for key, label, stage, terminal in OUTCOMES}
DEFAULT_OUTCOME = "applied"

# How long a submitted application can sit with no reply before it is worth
# chasing. Two weeks is the usual point at which a polite nudge is reasonable.
FOLLOW_UP_DAYS = 14
# After this long with nothing, it is realistically dead.
GHOST_DAYS = 45


def is_valid(outcome: str) -> bool:
    return outcome in BY_KEY


def label_for(outcome: str) -> str:
    return BY_KEY.get(outcome, ("", 0, False))[0] or outcome


def stage_of(outcome: str) -> int:
    return BY_KEY.get(outcome, ("", 0, False))[1]


def is_terminal(outcome: str) -> bool:
    return BY_KEY.get(outcome, ("", 0, False))[2]


def _naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def days_since(value: datetime | None) -> int | None:
    value = _naive(value)
    if value is None:
        return None
    return max(0, (models.utcnow().replace(tzinfo=None) - value).days)


def record(application, outcome: str, note: str = "") -> None:
    """Move an application to a new outcome and append to its history."""
    now = models.utcnow()
    previous = application.outcome or ""

    application.outcome = outcome
    application.outcome_at = now
    if note:
        application.notes = (
            f"{application.notes}\n\n{note}".strip() if application.notes else note
        )

    # The first time an employer engages at all, remember when -- that is what
    # makes "average days to first reply" meaningful.
    if stage_of(outcome) > 0 and application.first_response_at is None:
        application.first_response_at = now

    if is_terminal(outcome):
        application.follow_up_at = None
    elif application.follow_up_at is None and outcome == DEFAULT_OUTCOME:
        anchor = _naive(application.submitted_at) or _naive(now)
        application.follow_up_at = anchor + timedelta(days=FOLLOW_UP_DAYS)

    application.events = (application.events or []) + [{
        "at": now.isoformat(timespec="seconds"),
        "from": previous,
        "outcome": outcome,
        "note": note,
    }]


def needs_follow_up(application) -> bool:
    """Submitted, still silent, and old enough that a nudge is reasonable."""
    if application.status != "submitted":
        return False
    if is_terminal(application.outcome or ""):
        return False
    if stage_of(application.outcome or DEFAULT_OUTCOME) > 0:
        return False
    waited = days_since(application.submitted_at)
    return waited is not None and waited >= FOLLOW_UP_DAYS


def looks_ghosted(application) -> bool:
    waited = days_since(application.submitted_at)
    return (
        application.status == "submitted"
        and not is_terminal(application.outcome or "")
        and stage_of(application.outcome or DEFAULT_OUTCOME) == 0
        and waited is not None
        and waited >= GHOST_DAYS
    )


def summarise(applications: list) -> dict:
    """Funnel counts and the two rates that actually tell you if this is working."""
    submitted = [a for a in applications if a.status == "submitted"]
    counts = {key: 0 for key, *_ in OUTCOMES}
    for app in submitted:
        counts[app.outcome or DEFAULT_OUTCOME] = counts.get(app.outcome or DEFAULT_OUTCOME, 0) + 1

    responded = [a for a in submitted if stage_of(a.outcome or DEFAULT_OUTCOME) > 0
                 or (a.outcome or "") == "rejected"]
    interviewed = [a for a in submitted if stage_of(a.outcome or "") >= 2]
    offers = [a for a in submitted if (a.outcome or "") in ("offer", "accepted")]

    response_days = [
        days_since(a.submitted_at) - (days_since(a.first_response_at) or 0)
        for a in submitted if a.first_response_at and a.submitted_at
    ]
    response_days = [d for d in response_days if d is not None and d >= 0]

    total = len(submitted) or 1
    return {
        "submitted": len(submitted),
        "in_progress": len([a for a in submitted if not is_terminal(a.outcome or "")]),
        "counts": counts,
        "labels": {key: label for key, label, *_ in OUTCOMES},
        "response_rate": round(len(responded) / total * 100, 1),
        "interview_rate": round(len(interviewed) / total * 100, 1),
        "offer_rate": round(len(offers) / total * 100, 1),
        "avg_days_to_reply": round(sum(response_days) / len(response_days), 1)
        if response_days else None,
        "needs_follow_up": len([a for a in submitted if needs_follow_up(a)]),
        "possibly_ghosted": len([a for a in submitted if looks_ghosted(a)]),
        "follow_up_days": FOLLOW_UP_DAYS,
    }
