"""Orchestration: fetch -> score -> tailor -> apply."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import SERVERLESS
from ..models import Application, Job, Match, Profile, Resume, RunLog, utcnow
from ..sources import providers
from ..sources.base import JobPost
from . import docgen, matcher, tailor, visa
from .ats import autofill
from .skills import extract_skills

log = logging.getLogger("jobpilot.pipeline")

# How long a hosted fetch may spend writing postings before it stops and asks
# to be run again. A serverless request is killed at a hard limit, and a kill
# loses the whole batch, so stopping early on purpose stores strictly more.
WRITE_BUDGET_SECONDS = 30.0


# --------------------------------------------------------------------------- #
# 1. fetch
# --------------------------------------------------------------------------- #
def _existing_jobs(db: Session, posts: list[JobPost]) -> dict[tuple[str, str], Job]:
    """Every stored job these postings might update, in one query per source.

    A fetch carries thousands of postings and nearly all of them already exist.
    Looking each one up on its own is thousands of round trips, which a hosted
    database on the other side of a network cannot serve inside one request.
    """
    wanted: dict[str, set[str]] = {}
    for post in posts:
        if post.external_id:
            wanted.setdefault(post.source, set()).add(post.external_id)

    found: dict[tuple[str, str], Job] = {}
    for source, ids in wanted.items():
        id_list = list(ids)
        for start in range(0, len(id_list), 500):
            chunk = id_list[start:start + 500]
            for job in db.scalars(
                select(Job).where(Job.source == source, Job.external_id.in_(chunk))
            ):
                found[(job.source, job.external_id)] = job
    return found


def _is_unchanged(existing: Job | None, post: JobPost) -> bool:
    """Would storing this posting write the same row back?

    Boards republish the same advert on every fetch. Rewriting thousands of
    identical rows — and re-running the visa and skill analysis to do it — was
    most of the cost of a fetch, and on a hosted instance it ran out of time.
    """
    if existing is None:
        return False
    return (
        existing.title == post.title
        and existing.company == post.company
        and existing.location == post.location
        and existing.salary == post.salary
        and existing.url == post.url
        and existing.description == (post.description or "")
    )


def _upsert_job(db: Session, post: JobPost, existing: Job | None) -> tuple[Job, bool]:
    status, score, evidence = visa.analyze(
        post.description, post.title, post.tags, explicit=post.visa_flag
    )
    # A known-sponsoring employer is a weaker claim than a posting that says so
    # outright, and these firms publish thousands of ads. Marking them all "yes"
    # made 60% of the database look sponsored and rendered the filter useless,
    # so they get their own, clearly weaker tier.
    if status == "unknown" and visa.company_is_known_sponsor(post.company):
        status, score = "likely", 1.5
        evidence = [
            f"{post.company} has a public track record of sponsoring work visas, "
            "but this posting does not mention it"
        ]

    # Resolve the link to the actual application form now, so the stored job is
    # applyable and the UI shows the real ATS. Greenhouse hands out company
    # careers URLs for over half its postings; those pages carry no form, only a
    # gh_jid pointing at one.
    from .ats.autofill import resolve_apply_url

    apply_url, _ = resolve_apply_url(post.apply_url, post.description)

    payload = {
        "title": post.title,
        "company": post.company,
        "location": post.location,
        "country": post.country,
        "is_remote": post.is_remote,
        "employment_type": post.employment_type,
        "salary": post.salary,
        "url": post.url,
        "apply_url": apply_url,
        "description": post.description,
        "tags": post.tags,
        "job_skills": extract_skills(f"{post.title}\n{post.description}"),
        "visa_status": status,
        "visa_score": score,
        "visa_evidence": evidence,
        "posted_at": post.posted_at,
        "fetched_at": utcnow(),
    }

    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        return existing, False

    job = Job(source=post.source, external_id=post.external_id, **payload)
    db.add(job)
    # Flush now so a later posting with the same id in this same batch is seen
    # as existing. Autoflush is off, so without this the SELECT above misses
    # pending rows and the commit dies on the unique constraint.
    db.flush()
    return job, True


def _dedupe_key(job: Job) -> tuple[str, str] | None:
    import re

    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    company, title = norm(job.company), norm(job.title)
    return (company, title) if company and title else None


def dedupe_across_sources(db: Session) -> int:
    """Hide aggregator copies of jobs we also have straight from the employer.

    The same opening is listed by several boards. The aggregator's link points
    at its own description page, which has no form on it, so queuing that copy
    wastes an application slot and fills nothing. When we also hold the
    employer's ATS link for the same role, that one is strictly better.

    Deliberately conservative: only an aggregator ('generic') copy is hidden,
    and only when a real ATS copy of the same role exists. Two postings on the
    same ATS may be genuinely different openings, so those are left alone.
    """
    from .ats.autofill import detect_ats

    applied_job_ids = set(db.scalars(select(Application.job_id)))
    # Four columns, not whole rows: every job carries its full description, and
    # loading thousands of those to compare company and title reads megabytes
    # out of the database for nothing.
    rows = db.execute(
        select(Job.id, Job.company, Job.title, Job.apply_url).where(Job.hidden.is_(False))
    ).all()

    groups: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        key = _dedupe_key(row)
        if key:
            groups.setdefault(key, []).append(row)

    hide_ids: list[int] = []
    for jobs in groups.values():
        if len(jobs) < 2:
            continue
        has_real_ats = any(detect_ats(j.apply_url) != "generic" for j in jobs)
        if not has_real_ats:
            continue
        for job in jobs:
            if detect_ats(job.apply_url) != "generic":
                continue
            if job.id in applied_job_ids:
                continue  # never hide something already queued or applied
            hide_ids.append(job.id)

    for start in range(0, len(hide_ids), 1000):
        db.execute(
            update(Job).where(Job.id.in_(hide_ids[start:start + 1000])).values(hidden=True)
        )
    if hide_ids:
        db.commit()
    return len(hide_ids)


async def fetch_jobs(db: Session, profile: Profile, opts: dict | None = None) -> dict:
    """Pull from every enabled source and upsert into the local database."""
    opts = dict(opts or {})
    keys = opts.pop("sources", None) or profile.enabled_sources or []

    # Fall back to the curated company boards saved in Settings, so a plain
    # "Fetch jobs" from the dashboard still pulls from them.
    if not opts.get("greenhouse_boards") and profile.greenhouse_boards:
        opts["greenhouse_boards"] = list(profile.greenhouse_boards)
    if not opts.get("lever_boards") and profile.lever_boards:
        opts["lever_boards"] = list(profile.lever_boards)

    posts, notes = await providers.fetch_all(keys, opts)

    stored = _existing_jobs(db, posts)

    created = updated = unchanged = skipped = failed = 0
    touched: list[int] = []
    seen: set[tuple[str, str]] = set()
    deadline = time.monotonic() + WRITE_BUDGET_SECONDS if SERVERLESS else None
    ran_out = False

    for post in posts:
        if not post.title or not post.external_id:
            skipped += 1
            continue

        # Boards do repost the same id inside one response; take the first copy.
        key = (post.source, post.external_id)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)

        existing = stored.get(key)
        if _is_unchanged(existing, post):
            unchanged += 1
            continue

        if deadline is not None and time.monotonic() > deadline:
            ran_out = True
            break

        try:
            # A savepoint per posting: if one row is malformed we lose that row,
            # not the hundreds already staged in this batch.
            with db.begin_nested():
                job, is_new = _upsert_job(db, post, existing)
            if is_new:
                stored[key] = job
            created += int(is_new)
            updated += int(not is_new)
            touched.append(job.id)
        except Exception as exc:  # noqa: BLE001 - one bad posting must not lose the batch
            failed += 1
            log.warning("skipped %s/%s: %s", post.source, post.external_id, exc)

    db.commit()

    deduped = dedupe_across_sources(db)
    if deduped:
        notes.append(
            f"Hid {deduped} aggregator copy(ies) of jobs we have direct from the employer"
        )
    if skipped:
        notes.append(f"Skipped {skipped} duplicate or incomplete posting(s)")
    if unchanged:
        notes.append(f"{unchanged} posting(s) already stored, unchanged")
    if failed:
        notes.append(f"{failed} posting(s) could not be stored — see the server log")
    if ran_out:
        notes.append(
            "Ran out of time storing postings — press Fetch again to carry on from here"
        )

    return {
        "fetched": len(posts),
        "new": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "failed": failed,
        "partial": ran_out,
        "touched": touched,
        "sources": notes,
    }


# --------------------------------------------------------------------------- #
# 2. score
# --------------------------------------------------------------------------- #
def default_resume(db: Session) -> Resume | None:
    resume = db.scalar(select(Resume).where(Resume.is_default.is_(True)))
    return resume or db.scalar(select(Resume).order_by(Resume.created_at.desc()))


def rescore(
    db: Session,
    profile: Profile,
    resume: Resume,
    job_ids: list[int] | None = None,
    only_new: bool = False,
) -> dict:
    """(Re)compute match scores. Returns counts."""
    query = select(Job).where(Job.hidden.is_(False))
    if job_ids:
        query = query.where(Job.id.in_(job_ids))
    jobs = list(db.scalars(query))

    if only_new and not job_ids:
        scored_ids = set(
            db.scalars(select(Match.job_id).where(Match.resume_id == resume.id))
        )
        jobs = [j for j in jobs if j.id not in scored_ids]

    existing = {
        m.job_id: m
        for m in db.scalars(select(Match).where(Match.resume_id == resume.id))
    }

    above_threshold = 0
    for job in jobs:
        result = matcher.match(job, resume, profile)
        record = existing.get(job.id)
        if record is None:
            record = Match(job_id=job.id, resume_id=resume.id)
            db.add(record)
        record.score = result.score
        record.breakdown = result.breakdown
        record.matched_skills = result.matched_skills
        record.missing_skills = result.missing_skills
        record.reasons = result.reasons
        record.created_at = utcnow()
        if result.score >= profile.min_match_score:
            above_threshold += 1

    db.commit()
    return {"scored": len(jobs), "above_threshold": above_threshold}


# --------------------------------------------------------------------------- #
# 3. tailor + generate documents
# --------------------------------------------------------------------------- #
def prepare_application(db: Session, application: Application) -> Application:
    """Tailor the resume for this job and render the documents."""
    profile = db.get(Profile, 1)
    job = db.get(Job, application.job_id)
    resume = db.get(Resume, application.resume_id) if application.resume_id else None
    resume = resume or default_resume(db)

    if job is None or resume is None:
        application.status = "failed"
        application.error = "Missing job or resume"
        db.commit()
        return application

    # Remember how far this application had already got, because the line below
    # overwrites it and we must not rewind past "form already filled"/"sent".
    prior_status = application.status
    application.status = "preparing"
    application.resume_id = resume.id
    db.commit()

    match_record = db.scalar(
        select(Match).where(Match.job_id == job.id, Match.resume_id == resume.id)
    )
    if match_record is None:
        result = matcher.match(job, resume, profile)
        match_payload = {
            "score": result.score,
            "matched_skills": result.matched_skills,
            "missing_skills": result.missing_skills,
        }
        application.score = result.score
    else:
        match_payload = {
            "score": match_record.score,
            "matched_skills": match_record.matched_skills,
            "missing_skills": match_record.missing_skills,
        }
        application.score = match_record.score

    try:
        tailored, engine = tailor.tailor(resume, job, match_payload, profile)
    except Exception as exc:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Tailoring failed: {type(exc).__name__}: {exc}"
        db.commit()
        return application

    application.tailored_json = tailored.model_dump()
    application.tailored_summary = tailored.summary
    application.cover_letter = tailored.cover_letter if profile.generate_cover_letter else ""

    try:
        paths = docgen.generate_all(tailored, resume, profile, job, application.id)
    except Exception as exc:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Document generation failed: {type(exc).__name__}: {exc}"
        db.commit()
        return application

    application.resume_docx = paths.get("docx", "")
    application.resume_pdf = paths.get("pdf", "")
    # Hosted instances keep the documents in the row; locally these stay None
    # and the files on disk are used.
    application.resume_docx_bytes = paths.get("docx_bytes") or None
    application.resume_pdf_bytes = paths.get("pdf_bytes") or None
    application.cover_pdf_bytes = paths.get("cover_bytes") or None
    application.ats = application.ats or ""
    # Regenerating documents must not rewind the application's progress. It was
    # unconditionally setting "ready", which erased "needs_review" (form already
    # filled, waiting on you) and "submitted" (already sent).
    application.status = (
        prior_status if prior_status in ("needs_review", "submitted") else "ready"
    )
    application.error = ""
    application.log = (application.log or []) + [
        f"Tailored with the {engine} engine",
        f"Generated {', '.join(k for k in paths if not k.endswith('_error'))}",
    ]
    if paths.get("cover_letter_pdf"):
        application.tailored_json = {
            **application.tailored_json,
            "cover_letter_pdf": paths["cover_letter_pdf"],
        }
    db.commit()
    return application


# --------------------------------------------------------------------------- #
# 4. queue selection
# --------------------------------------------------------------------------- #
def queue_top_matches(db: Session, profile: Profile, resume: Resume, limit: int = 10) -> dict:
    """Queue the highest-scoring jobs that have not been queued already."""
    already = set(db.scalars(select(Application.job_id)))

    rows = db.execute(
        select(Match, Job)
        .join(Job, Job.id == Match.job_id)
        .where(
            Match.resume_id == resume.id,
            Match.score >= profile.min_match_score,
            Job.hidden.is_(False),
        )
        .order_by(Match.score.desc())
    ).all()

    queued = 0
    skipped_reasons: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    for match_row, job in rows:
        if queued >= limit:
            break
        if job.id in already:
            skip("already queued")
            continue
        if profile.remote_only and not job.is_remote:
            skip("not remote")
            continue
        if profile.visa_only and job.visa_status not in ("yes", "likely", "global"):
            skip("no visa signal")
            continue
        if not job.apply_url:
            skip("no application URL")
            continue
        # A link to a job board's own description page can never be filled in;
        # queueing it burns a slot and produces an empty form every time.
        if autofill.is_aggregator(job.apply_url):
            skip("link goes to a job-board listing, not an application form")
            continue

        db.add(Application(
            job_id=job.id,
            resume_id=resume.id,
            status="queued",
            score=match_row.score,
            ats="",
            log=[f"Queued at {utcnow().isoformat(timespec='seconds')} "
                 f"with score {match_row.score}"],
        ))
        queued += 1

    db.commit()
    return {"queued": queued, "skipped": skipped_reasons}


def applications_today(db: Session) -> int:
    since = utcnow() - timedelta(hours=24)
    return db.scalar(
        select(func.count(Application.id)).where(
            Application.submitted_at.is_not(None), Application.submitted_at >= since
        )
    ) or 0


# --------------------------------------------------------------------------- #
# run bookkeeping
# --------------------------------------------------------------------------- #
def start_run(db: Session, kind: str, detail: dict | None = None) -> RunLog:
    run = RunLog(kind=kind, status="running", detail=detail or {}, messages=[])
    db.add(run)
    db.commit()
    return run


def finish_run(db: Session, run: RunLog, status: str, detail: dict | None = None) -> None:
    run.status = status
    run.finished_at = utcnow()
    if detail:
        run.detail = {**(run.detail or {}), **detail}
    db.commit()


def append_message(db: Session, run: RunLog, message: str) -> None:
    run.messages = (run.messages or []) + [
        f"{datetime.now().strftime('%H:%M:%S')}  {message}"
    ]
    db.commit()
