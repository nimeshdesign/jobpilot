"""HTTP API. Everything the single-page frontend talks to."""
from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, RESUME_DIR, SERVERLESS, STORE_FILES_IN_DB
from ..db import get_db
from ..models import Application, Job, Match, Profile, Resume, RunLog, utcnow
from ..services import llm, pipeline, resume_parser
from ..services.ats import autofill
from ..services.worker import pending_application_ids, worker
from ..sources import providers

log = logging.getLogger("jobpilot.api")
router = APIRouter(prefix="/api")

ALLOWED_RESUME_TYPES = {".pdf", ".docx", ".doc", ".txt", ".md"}
MAX_RESUME_BYTES = 15 * 1024 * 1024


def get_profile(db: Session) -> Profile:
    profile = db.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        db.add(profile)
        db.commit()
    return profile


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    playwright_ok, playwright_msg = autofill.playwright_available()
    counts = {
        "jobs": db.scalar(select(func.count(Job.id))) or 0,
        "remote_jobs": db.scalar(
            select(func.count(Job.id)).where(Job.is_remote.is_(True))
        ) or 0,
        "visa_jobs": db.scalar(
            select(func.count(Job.id)).where(Job.visa_status == "yes")
        ) or 0,
        "likely_visa_jobs": db.scalar(
            select(func.count(Job.id)).where(Job.visa_status == "likely")
        ) or 0,
        "global_jobs": db.scalar(
            select(func.count(Job.id)).where(Job.visa_status == "global")
        ) or 0,
        "resumes": db.scalar(select(func.count(Resume.id))) or 0,
        "matches": db.scalar(select(func.count(Match.id))) or 0,
        "applications": db.scalar(select(func.count(Application.id))) or 0,
        "submitted": db.scalar(
            select(func.count(Application.id)).where(Application.status == "submitted")
        ) or 0,
        "needs_review": db.scalar(
            select(func.count(Application.id)).where(Application.status == "needs_review")
        ) or 0,
    }
    profile = get_profile(db)
    resume = pipeline.default_resume(db)
    return {
        "counts": counts,
        "llm": llm.describe(),
        "playwright": {"available": playwright_ok, "message": playwright_msg},
        "hosted": SERVERLESS,
        "worker": worker.state,
        "default_resume": {"id": resume.id, "label": resume.label} if resume else None,
        "applied_last_24h": pipeline.applications_today(db),
        "daily_limit": profile.daily_apply_limit,
    }


@router.post("/llm/test")
def test_llm() -> dict:
    """Prove the configured AI provider actually answers."""
    return llm.test_connection()


@router.get("/llm/models")
def llm_models() -> dict:
    """Models the configured OpenAI-compatible provider serves."""
    return {"models": llm.list_models(), "selected": llm.resolve_openai_model()}


@router.get("/salary/preview")
def salary_preview(db: Session = Depends(get_db)) -> dict:
    """What JobPilot will type into "expected salary", country by country."""
    from ..services import salary as salary_service

    profile = get_profile(db)
    resume = pipeline.default_resume(db)
    parsed = (resume.parsed or {}) if resume else {}
    years = float(profile.years_experience or parsed.get("years_experience", 0) or 0)
    seniority = parsed.get("seniority", "")

    return {
        "years": years,
        "seniority": seniority,
        "band": salary_service.band_for(years, seniority),
        "fallback": profile.desired_salary,
        "rows": salary_service.preview(
            years, seniority, profile.salary_expectations or {}
        ),
    }


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> dict:
    profile = get_profile(db)
    enabled = profile.enabled_sources or [
        s["key"] for s in providers.list_sources() if s["default_on"]
    ]
    return {"sources": providers.list_sources(), "enabled": enabled}


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #
PROFILE_FIELDS = [
    "full_name", "email", "phone", "city", "country", "linkedin", "github", "portfolio",
    "current_visa_status", "needs_sponsorship", "authorized_countries", "willing_to_relocate",
    "notice_period", "years_experience", "desired_salary", "salary_expectations",
    "pronouns",
    "target_titles", "must_have_keywords", "exclude_keywords", "preferred_countries",
    "remote_only", "visa_only", "min_match_score",
    "auto_submit", "headless", "apply_delay_seconds", "daily_apply_limit",
    "generate_cover_letter", "use_llm", "custom_answers", "enabled_sources",
    "greenhouse_boards", "lever_boards",
]


@router.get("/profile")
def read_profile(db: Session = Depends(get_db)) -> dict:
    profile = get_profile(db)
    return {field: getattr(profile, field) for field in PROFILE_FIELDS}


@router.put("/profile")
def update_profile(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict:
    profile = get_profile(db)
    for key, value in payload.items():
        if key in PROFILE_FIELDS:
            setattr(profile, key, value)
    profile.updated_at = utcnow()
    db.commit()
    return {field: getattr(profile, field) for field in PROFILE_FIELDS}


# --------------------------------------------------------------------------- #
# resumes
# --------------------------------------------------------------------------- #
def _resume_dto(resume: Resume, full: bool = False) -> dict:
    data = {
        "id": resume.id,
        "label": resume.label,
        "filename": resume.filename,
        "is_default": resume.is_default,
        "created_at": resume.created_at.isoformat() if resume.created_at else None,
        "skills": resume.skills,
        "skill_count": len(resume.skills or []),
        "summary": (resume.parsed or {}).get("summary", "")[:400],
        "years_experience": (resume.parsed or {}).get("years_experience", 0),
        "seniority": (resume.parsed or {}).get("seniority", ""),
        "sections_found": (resume.parsed or {}).get("sections_found", []),
        "experience_count": len((resume.parsed or {}).get("experience", [])),
    }
    if full:
        data["parsed"] = resume.parsed
        data["raw_text"] = resume.raw_text
    return data


@router.get("/resumes")
def list_resumes(db: Session = Depends(get_db)) -> list[dict]:
    resumes = db.scalars(select(Resume).order_by(Resume.created_at.desc()))
    return [_resume_dto(r) for r in resumes]


@router.get("/resumes/{resume_id}")
def get_resume(resume_id: int, db: Session = Depends(get_db)) -> dict:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Resume not found")
    return _resume_dto(resume, full=True)


@router.post("/resumes")
async def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_RESUME_TYPES:
        raise HTTPException(
            400,
            f"'{suffix or 'This file'}' is not supported. Upload a PDF, DOCX or "
            "TXT. If your resume is a .doc, .rtf, .odt or Pages file, export it "
            "as PDF or .docx first.",
        )

    safe_name = Path(file.filename or "resume").name
    destination = RESUME_DIR / f"{int(utcnow().timestamp())}-{safe_name}"
    written = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(1024 * 256):
                written += len(chunk)
                if written > MAX_RESUME_BYTES:
                    raise HTTPException(
                        400,
                        f"That file is larger than "
                        f"{MAX_RESUME_BYTES // (1024 * 1024)} MB. A resume this "
                        "big is usually a scan — export a text PDF instead.",
                    )
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        log.exception("could not save upload")
        raise HTTPException(400, "The file could not be saved. Try again.") from exc

    if written == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "That file is empty.")

    try:
        raw_text, parsed = resume_parser.ingest(destination)
    except resume_parser.ResumeReadError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        # Never surface a parser's internals: they carry absolute server paths
        # and mean nothing to the person holding the file.
        destination.unlink(missing_ok=True)
        log.exception("resume parsing failed for %s", safe_name)
        raise HTTPException(
            400,
            "This file could not be read as a resume. Try exporting it again "
            "as a PDF or DOCX. If it is a scanned document, the text is an "
            "image and cannot be extracted.",
        ) from exc

    is_first = (db.scalar(select(func.count(Resume.id))) or 0) == 0
    resume = Resume(
        label=Path(safe_name).stem[:180],
        filename=safe_name,
        stored_path="" if STORE_FILES_IN_DB else str(destination),
        file_bytes=destination.read_bytes() if STORE_FILES_IN_DB else None,
        raw_text=raw_text,
        parsed=parsed,
        skills=parsed.get("skills", []),
        is_default=is_first,
    )
    db.add(resume)
    db.commit()

    # seed empty profile fields from the resume so the user types less
    profile = get_profile(db)
    contact = parsed.get("contact", {})
    for profile_field, parsed_key in (
        ("full_name", "name"), ("email", "email"), ("phone", "phone"),
        ("linkedin", "linkedin"), ("github", "github"), ("portfolio", "portfolio"),
    ):
        if not getattr(profile, profile_field) and contact.get(parsed_key):
            setattr(profile, profile_field, contact[parsed_key])
    if not profile.years_experience and parsed.get("years_experience"):
        profile.years_experience = parsed["years_experience"]
    db.commit()

    return _resume_dto(resume, full=True)


@router.post("/resumes/{resume_id}/default")
def set_default_resume(resume_id: int, db: Session = Depends(get_db)) -> dict:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Resume not found")
    for other in db.scalars(select(Resume)):
        other.is_default = other.id == resume_id
    db.commit()
    return {"ok": True, "default": resume_id}


@router.post("/resumes/{resume_id}/reparse")
def reparse_resume(resume_id: int, db: Session = Depends(get_db)) -> dict:
    """Re-run the parser over the stored text, without a re-upload."""
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Resume not found")

    source = Path(resume.stored_path) if resume.stored_path else None
    if source and source.exists():
        try:
            resume.raw_text = resume_parser.extract_text(source)
        except Exception as exc:  # noqa: BLE001 - fall back to the stored text
            log.warning("re-extract failed for resume %s: %s", resume_id, exc)
    elif resume.file_bytes:
        # Hosted instance: the original upload lives in the row, so write it to
        # the scratch disk just long enough to re-read it.
        import tempfile

        suffix = Path(resume.filename or "resume.pdf").suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resume.file_bytes)
            tmp_path = Path(tmp.name)
        try:
            resume.raw_text = resume_parser.extract_text(tmp_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("re-extract from stored bytes failed: %s", exc)
        finally:
            tmp_path.unlink(missing_ok=True)

    if not (resume.raw_text or "").strip():
        raise HTTPException(400, "No resume text stored to re-parse")

    resume.parsed = resume_parser.parse_resume(resume.raw_text)
    resume.skills = resume.parsed.get("skills", [])
    db.commit()
    return _resume_dto(resume, full=True)


@router.delete("/resumes/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)) -> dict:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(404, "Resume not found")
    if resume.stored_path:
        Path(resume.stored_path).unlink(missing_ok=True)
    db.execute(delete(Match).where(Match.resume_id == resume_id))
    db.delete(resume)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
class FetchRequest(BaseModel):
    sources: list[str] | None = None
    query: str | None = None
    limit: int = 120
    pages: int = 3
    greenhouse_boards: list[str] | None = None
    lever_boards: list[str] | None = None
    rescore: bool = True


async def _fetch_task(payload: FetchRequest) -> list[str]:
    from ..db import SessionLocal

    with SessionLocal() as db:
        profile = get_profile(db)
        run = pipeline.start_run(db, "fetch", {"sources": payload.sources or []})
        try:
            options = payload.model_dump(exclude_none=True, exclude={"sources", "rescore"})
            if payload.sources:
                options["sources"] = payload.sources
            result = await pipeline.fetch_jobs(db, profile, options)
            for note in result["sources"]:
                pipeline.append_message(db, run, note)
            pipeline.append_message(
                db, run, f"{result['new']} new, {result['updated']} updated"
            )

            if payload.rescore:
                resume = pipeline.default_resume(db)
                if resume:
                    if not SERVERLESS:
                        scored = pipeline.rescore(db, profile, resume)
                    elif result["touched"]:
                        # Hosted: score what this fetch changed. Rescoring every
                        # stored job needs far more time than one request has.
                        scored = pipeline.rescore(
                            db, profile, resume, job_ids=result["touched"]
                        )
                    else:
                        scored = pipeline.rescore(db, profile, resume, only_new=True)
                    pipeline.append_message(
                        db, run,
                        f"Scored {scored['scored']} jobs, "
                        f"{scored['above_threshold']} above your {profile.min_match_score} threshold",
                    )
                    result["scored"] = scored
                else:
                    pipeline.append_message(db, run, "No resume uploaded yet — skipped scoring")

            pipeline.finish_run(db, run, "done", result)
        except Exception as exc:  # noqa: BLE001
            log.exception("fetch failed")
            # The session may be in a failed-flush state; clear it before we try
            # to record the failure, or the error handler raises its own error
            # and takes the ASGI connection down with it.
            db.rollback()
            try:
                pipeline.append_message(db, run, f"Failed: {type(exc).__name__}: {exc}")
                pipeline.finish_run(db, run, "failed", {"error": str(exc)[:500]})
            except Exception:  # noqa: BLE001
                log.exception("could not record the failed run")

        return list(run.messages or [])


@router.post("/jobs/fetch")
async def fetch_jobs(
    payload: FetchRequest, background: BackgroundTasks, db: Session = Depends(get_db)
) -> dict:
    if SERVERLESS:
        # A serverless function is frozen as soon as the response is sent, so a
        # background task never runs. Fetch inside the request instead.
        messages = await _fetch_task(payload)
        return {"ok": True, "done": True, "messages": messages}
    background.add_task(_fetch_task, payload)
    return {"ok": True, "message": "Fetching in the background — watch the Activity panel."}


def _job_dto(job: Job, match: Match | None = None, application: Application | None = None) -> dict:
    return {
        "id": job.id,
        "source": job.source,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "country": job.country,
        "is_remote": job.is_remote,
        "employment_type": job.employment_type,
        "salary": job.salary,
        "url": job.url,
        "apply_url": job.apply_url,
        "tags": job.tags,
        "job_skills": job.job_skills,
        "visa_status": job.visa_status,
        "visa_score": job.visa_score,
        "visa_evidence": job.visa_evidence,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "description": job.description,
        "hidden": job.hidden,
        "score": match.score if match else None,
        "breakdown": match.breakdown if match else None,
        "matched_skills": match.matched_skills if match else [],
        "missing_skills": match.missing_skills if match else [],
        "reasons": match.reasons if match else [],
        "application": {
            "id": application.id, "status": application.status
        } if application else None,
    }


@router.get("/jobs")
def list_jobs(
    q: str | None = None,
    remote: bool | None = None,
    visa: str | None = None,
    source: str | None = None,
    min_score: float | None = None,
    sort: str = "score",
    limit: int = Query(60, le=300),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    resume = pipeline.default_resume(db)
    resume_id = resume.id if resume else -1

    stmt = (
        select(Job, Match, Application)
        .outerjoin(Match, (Match.job_id == Job.id) & (Match.resume_id == resume_id))
        .outerjoin(Application, Application.job_id == Job.id)
        .where(Job.hidden.is_(False))
    )

    if q:
        term = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(Job.title).like(term),
            func.lower(Job.company).like(term),
            func.lower(Job.description).like(term),
        ))
    if remote is not None:
        stmt = stmt.where(Job.is_remote.is_(remote))
    if visa == "sponsor_or_global":
        stmt = stmt.where(Job.visa_status.in_(("yes", "likely", "global")))
    elif visa in ("yes", "likely", "global", "no", "unknown"):
        stmt = stmt.where(Job.visa_status == visa)
    if source:
        stmt = stmt.where(Job.source == source)
    if min_score is not None:
        stmt = stmt.where(Match.score >= min_score)

    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    if sort == "score":
        stmt = stmt.order_by(Match.score.desc().nullslast(), Job.posted_at.desc().nullslast())
    elif sort == "recent":
        stmt = stmt.order_by(Job.posted_at.desc().nullslast(), Job.fetched_at.desc())
    else:
        stmt = stmt.order_by(Job.company, Job.title)

    rows = db.execute(stmt.limit(limit).offset(offset)).all()
    return {
        "total": total,
        "items": [
            {**_job_dto(job, match, application),
             "description": (job.description or "")[:600]}
            for job, match, application in rows
        ],
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    resume = pipeline.default_resume(db)
    match = db.scalar(
        select(Match).where(Match.job_id == job_id, Match.resume_id == (resume.id if resume else -1))
    )
    application = db.scalar(select(Application).where(Application.job_id == job_id))
    return _job_dto(job, match, application)


@router.post("/jobs/{job_id}/hide")
def hide_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    job.hidden = True
    db.commit()
    return {"ok": True}


@router.delete("/jobs")
def clear_jobs(db: Session = Depends(get_db)) -> dict:
    kept = db.scalar(select(func.count(Application.id))) or 0
    applied_job_ids = set(db.scalars(select(Application.job_id)))
    removed = 0
    for job in db.scalars(select(Job)):
        if job.id not in applied_job_ids:
            db.delete(job)
            removed += 1
    db.commit()
    return {"ok": True, "removed": removed, "kept_because_applied": kept}


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
class RescoreRequest(BaseModel):
    resume_id: int | None = None
    only_new: bool = False


@router.post("/match/run")
def run_match(payload: RescoreRequest, db: Session = Depends(get_db)) -> dict:
    profile = get_profile(db)
    resume = db.get(Resume, payload.resume_id) if payload.resume_id else pipeline.default_resume(db)
    if resume is None:
        raise HTTPException(400, "Upload a resume first")
    result = pipeline.rescore(db, profile, resume, only_new=payload.only_new)
    return {"ok": True, "resume": resume.label, **result}


# --------------------------------------------------------------------------- #
# applications
# --------------------------------------------------------------------------- #
def _application_dto(application: Application, job: Job | None) -> dict:
    from ..services import tracker

    outcome = application.outcome or (
        tracker.DEFAULT_OUTCOME if application.status == "submitted" else ""
    )
    return {
        "id": application.id,
        "status": application.status,
        "ats": application.ats,
        "score": application.score,
        "outcome": outcome,
        "outcome_label": tracker.label_for(outcome) if outcome else "",
        "outcome_at": application.outcome_at.isoformat() if application.outcome_at else None,
        "days_since_applied": tracker.days_since(application.submitted_at),
        "first_response_at": application.first_response_at.isoformat()
        if application.first_response_at else None,
        "needs_follow_up": tracker.needs_follow_up(application),
        "possibly_ghosted": tracker.looks_ghosted(application),
        "notes": application.notes,
        "events": application.events or [],
        "job": {
            "id": job.id, "title": job.title, "company": job.company,
            "location": job.location, "is_remote": job.is_remote,
            "visa_status": job.visa_status, "apply_url": job.apply_url, "url": job.url,
        } if job else None,
        "tailored_summary": application.tailored_summary,
        "tailored": application.tailored_json,
        "cover_letter": application.cover_letter,
        "has_docx": bool(application.resume_docx),
        "has_pdf": bool(application.resume_pdf),
        "has_cover_pdf": bool((application.tailored_json or {}).get("cover_letter_pdf")),
        "has_screenshot": bool(application.screenshot),
        "filled_fields": application.filled_fields,
        "unfilled_fields": application.unfilled_fields,
        "log": application.log,
        "error": application.error,
        "created_at": application.created_at.isoformat() if application.created_at else None,
        "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
    }


@router.get("/applications")
def list_applications(
    status_filter: str | None = Query(None, alias="status"),
    outcome: str | None = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = (
        select(Application, Job)
        .outerjoin(Job, Job.id == Application.job_id)
        .order_by(Application.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    rows = db.execute(stmt).all()

    if outcome == "follow_up":
        from ..services import tracker
        rows = [(a, j) for a, j in rows if tracker.needs_follow_up(a)]
    elif outcome:
        rows = [(a, j) for a, j in rows
                if (a.outcome or ("applied" if a.status == "submitted" else "")) == outcome]

    return [_application_dto(a, j) for a, j in rows]


class OutcomeUpdate(BaseModel):
    outcome: str
    note: str = ""


@router.post("/applications/{application_id}/outcome")
def set_outcome(
    application_id: int, payload: OutcomeUpdate, db: Session = Depends(get_db)
) -> dict:
    """Record what the employer did next."""
    from ..services import tracker

    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    if not tracker.is_valid(payload.outcome):
        raise HTTPException(
            400,
            f"Unknown outcome '{payload.outcome}'. Valid: "
            + ", ".join(k for k, *_ in tracker.OUTCOMES),
        )

    # Recording a real-world outcome implies it went out, even if it was
    # submitted by hand after JobPilot filled the form.
    if application.status in ("ready", "needs_review"):
        application.status = "submitted"
        application.submitted_at = application.submitted_at or utcnow()

    tracker.record(application, payload.outcome, payload.note)
    db.commit()
    return _application_dto(application, db.get(Job, application.job_id))


@router.get("/applications/tracker/summary")
def tracker_summary(db: Session = Depends(get_db)) -> dict:
    from ..services import tracker

    applications = list(db.scalars(select(Application)))
    summary = tracker.summarise(applications)
    summary["outcomes"] = [
        {"key": key, "label": label, "stage": stage, "terminal": terminal}
        for key, label, stage, terminal in tracker.OUTCOMES
    ]
    return summary


@router.get("/applications/{application_id}")
def get_application(application_id: int, db: Session = Depends(get_db)) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    return _application_dto(application, db.get(Job, application.job_id))


class CreateApplication(BaseModel):
    job_id: int
    prepare: bool = True


@router.post("/applications")
def create_application(payload: CreateApplication, db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    existing = db.scalar(select(Application).where(Application.job_id == payload.job_id))
    if existing:
        return _application_dto(existing, job)

    resume = pipeline.default_resume(db)
    if resume is None:
        raise HTTPException(400, "Upload a resume first")

    match = db.scalar(
        select(Match).where(Match.job_id == job.id, Match.resume_id == resume.id)
    )
    application = Application(
        job_id=job.id, resume_id=resume.id, status="queued",
        score=match.score if match else 0.0,
        log=[f"Queued manually at {utcnow().isoformat(timespec='seconds')}"],
    )
    db.add(application)
    db.commit()

    if payload.prepare:
        application = pipeline.prepare_application(db, application)
    return _application_dto(application, job)


@router.post("/applications/{application_id}/prepare")
def prepare_application(application_id: int, db: Session = Depends(get_db)) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    application = pipeline.prepare_application(db, application)
    return _application_dto(application, db.get(Job, application.job_id))


@router.delete("/applications/{application_id}")
def delete_application(application_id: int, db: Session = Depends(get_db)) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    # Take the generated documents with it, or the folder fills up with orphaned
    # resumes whose numbering collides with future applications.
    from ..services.docgen import purge_previous

    removed = purge_previous(application_id, keep_prefix="\x00")
    if application.screenshot:
        Path(application.screenshot).unlink(missing_ok=True)

    db.delete(application)
    db.commit()
    return {"ok": True, "files_removed": removed}


class QueueTopRequest(BaseModel):
    limit: int = 10


@router.post("/applications/queue-top")
def queue_top(payload: QueueTopRequest, db: Session = Depends(get_db)) -> dict:
    profile = get_profile(db)
    resume = pipeline.default_resume(db)
    if resume is None:
        raise HTTPException(400, "Upload a resume first")
    return pipeline.queue_top_matches(db, profile, resume, payload.limit)


# --------------------------------------------------------------------------- #
# apply worker
# --------------------------------------------------------------------------- #
class ApplyRunRequest(BaseModel):
    application_ids: list[int] | None = None
    auto_submit: bool | None = None
    limit: int = 25


@router.post("/apply/start")
def start_apply(payload: ApplyRunRequest, db: Session = Depends(get_db)) -> dict:
    if SERVERLESS:
        # Chromium is ~430 MB against a 250 MB function limit, and a function
        # cannot stay alive for a multi-minute run. This is a hard platform
        # limit, not something a setting can switch on.
        raise HTTPException(
            400,
            "Form autofill cannot run on a hosted instance: it drives a real "
            "browser, which does not fit in a serverless function. Everything "
            "else works here — use this instance to find, score and tailor, "
            "then run JobPilot locally to fill the forms.",
        )
    ids = payload.application_ids or pending_application_ids(db, payload.limit)
    if not ids:
        raise HTTPException(400, "Nothing queued. Queue some matches first.")
    result = worker.start(ids, payload.auto_submit)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Could not start"))
    return result


@router.post("/apply/stop")
def stop_apply() -> dict:
    worker.stop()
    return {"ok": True}


@router.get("/apply/state")
def apply_state() -> dict:
    return worker.state


# --------------------------------------------------------------------------- #
# files + activity
# --------------------------------------------------------------------------- #
@router.get("/files/{application_id}/{kind}")
def download(application_id: int, kind: str, db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")

    # A hosted instance has no durable filesystem, so the bytes live in the
    # row. Prefer those; fall back to the path for a local install.
    blobs = {
        "docx": (application.resume_docx_bytes,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "resume.docx"),
        "pdf": (application.resume_pdf_bytes, "application/pdf", "resume.pdf"),
        "cover": (application.cover_pdf_bytes, "application/pdf", "cover-letter.pdf"),
    }
    blob, media_type, default_name = blobs.get(kind, (None, "", ""))
    if blob:
        return Response(
            content=blob,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{default_name}"'},
        )

    paths = {
        "docx": application.resume_docx,
        "pdf": application.resume_pdf,
        "cover": (application.tailored_json or {}).get("cover_letter_pdf", ""),
        "screenshot": application.screenshot,
    }
    raw_path = paths.get(kind, "")
    if not raw_path:
        raise HTTPException(404, f"No '{kind}' file for this application")

    path = Path(raw_path).resolve()
    if not path.is_file() or DATA_DIR.resolve() not in path.parents:
        raise HTTPException(404, "File is missing from disk")
    return FileResponse(path, filename=path.name)


@router.get("/runs")
def list_runs(limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    runs = list(db.scalars(select(RunLog).order_by(RunLog.started_at.desc()).limit(limit)))

    # A killed run can never finish its own row. Left alone it says "running"
    # for ever, which reads as "still working" when nothing is working.
    # Stored timestamps come back without a timezone (both SQLite and Postgres
    # hold naive DateTime here), so compare like with like.
    cutoff = utcnow().replace(tzinfo=None) - timedelta(minutes=10)
    abandoned = [
        run for run in runs
        if run.status == "running"
        and run.started_at
        and run.started_at.replace(tzinfo=None) < cutoff
    ]
    for run in abandoned:
        run.status = "failed"
        run.finished_at = utcnow()
        run.messages = (run.messages or []) + [
            "Stopped without finishing — the host's time limit was probably reached."
        ]
    if abandoned:
        db.commit()
    return [{
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "detail": run.detail,
        "messages": run.messages,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    } for run in runs]
