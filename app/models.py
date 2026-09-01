from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """Single row (id=1): who you are + how the automation should behave."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # --- identity, used to fill application forms ---
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    country: Mapped[str] = mapped_column(String(120), default="")
    linkedin: Mapped[str] = mapped_column(String(300), default="")
    github: Mapped[str] = mapped_column(String(300), default="")
    portfolio: Mapped[str] = mapped_column(String(300), default="")

    # --- work authorisation / visa answers ---
    current_visa_status: Mapped[str] = mapped_column(String(200), default="")
    needs_sponsorship: Mapped[bool] = mapped_column(Boolean, default=True)
    authorized_countries: Mapped[list] = mapped_column(JSON, default=list)
    willing_to_relocate: Mapped[bool] = mapped_column(Boolean, default=True)
    notice_period: Mapped[str] = mapped_column(String(120), default="")
    years_experience: Mapped[float] = mapped_column(Float, default=0.0)
    # Fallback used when a country has no entry and no override.
    desired_salary: Mapped[str] = mapped_column(String(120), default="")
    # Per-country overrides, {"Germany": "€75,000 - €85,000"}. Anything not
    # listed falls back to the built-in market estimate scaled by seniority.
    salary_expectations: Mapped[dict] = mapped_column(JSON, default=dict)
    pronouns: Mapped[str] = mapped_column(String(60), default="")

    # --- search preferences ---
    target_titles: Mapped[list] = mapped_column(JSON, default=list)
    must_have_keywords: Mapped[list] = mapped_column(JSON, default=list)
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list)
    preferred_countries: Mapped[list] = mapped_column(JSON, default=list)
    remote_only: Mapped[bool] = mapped_column(Boolean, default=True)
    visa_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Calibrated against real postings: resume-to-JD text similarity is
    # inherently low, so live scores top out around 60-65. A threshold of 65
    # matches almost nothing and makes "Queue top matches" look broken.
    min_match_score: Mapped[float] = mapped_column(Float, default=50.0)

    # --- automation behaviour ---
    auto_submit: Mapped[bool] = mapped_column(Boolean, default=False)
    headless: Mapped[bool] = mapped_column(Boolean, default=False)
    apply_delay_seconds: Mapped[int] = mapped_column(Integer, default=45)
    daily_apply_limit: Mapped[int] = mapped_column(Integer, default=20)
    generate_cover_letter: Mapped[bool] = mapped_column(Boolean, default=True)
    use_llm: Mapped[bool] = mapped_column(Boolean, default=True)

    # free-form answers for recurring ATS questions: {question_fragment: answer}
    custom_answers: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled_sources: Mapped[list] = mapped_column(JSON, default=list)
    # Per-company career boards to pull from, e.g. the "gitlab" in
    # boards.greenhouse.io/gitlab. These are where visa-sponsoring employers
    # actually post, so the list is worth curating.
    greenhouse_boards: Mapped[list] = mapped_column(JSON, default=list)
    lever_boards: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="My resume")
    filename: Mapped[str] = mapped_column(String(300), default="")
    stored_path: Mapped[str] = mapped_column(String(600), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_ext"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(60), index=True)
    external_id: Mapped[str] = mapped_column(String(200))

    title: Mapped[str] = mapped_column(String(400), default="")
    company: Mapped[str] = mapped_column(String(300), default="", index=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    country: Mapped[str] = mapped_column(String(120), default="")
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    employment_type: Mapped[str] = mapped_column(String(80), default="")
    salary: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(String(900), default="")
    apply_url: Mapped[str] = mapped_column(String(900), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    job_skills: Mapped[list] = mapped_column(JSON, default=list)

    # visa_status is one of: yes / no / unknown
    visa_status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    visa_score: Mapped[float] = mapped_column(Float, default=0.0)
    visa_evidence: Mapped[list] = mapped_column(JSON, default=list)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    matches: Mapped[list[Match]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("job_id", "resume_id", name="uq_match_job_resume"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), index=True
    )

    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_skills: Mapped[list] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="matches")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", name="uq_application_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )

    # queued | preparing | ready | needs_review | submitted | failed | skipped
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    ats: Mapped[str] = mapped_column(String(40), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)

    tailored_summary: Mapped[str] = mapped_column(Text, default="")
    tailored_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    resume_docx: Mapped[str] = mapped_column(String(600), default="")
    resume_pdf: Mapped[str] = mapped_column(String(600), default="")
    screenshot: Mapped[str] = mapped_column(String(600), default="")

    filled_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    unfilled_fields: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- what happened after you applied -------------------------------- #
    # `status` above tracks the automation (queued -> submitted). `outcome`
    # tracks the hiring process, which is the part you actually care about
    # weeks later: applied -> screening -> interview -> offer / rejected.
    outcome: Mapped[str] = mapped_column(String(20), default="", index=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # [{"at": iso, "outcome": str, "note": str}] -- the history, not just the
    # latest state, so you can see how long each stage actually took.
    events: Mapped[list] = mapped_column(JSON, default=list)

    job: Mapped[Job] = relationship()


class RunLog(Base):
    """One row per background pipeline run so the UI can show live progress."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # fetch | match | apply
    status: Mapped[str] = mapped_column(String(20), default="running")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
