"""Background apply-queue worker.

One worker thread, one application at a time, with a configurable delay between
submissions. Serial-by-design: parallel browser sessions against the same ATS
look exactly like abuse and get you rate-limited or blocked.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Application, Job, Profile, Resume, utcnow
from . import pipeline, tracker
from .ats import autofill
from .ats import fields as F

log = logging.getLogger("jobpilot.worker")


class ApplyWorker:
    """Singleton-ish worker; `state` is what the UI polls."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.state: dict = {
            "running": False,
            "current": None,
            "done": 0,
            "total": 0,
            "messages": [],
            "started_at": None,
            "finished_at": None,
            "mode": "",
        }

    # ------------------------------------------------------------------ #
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        self._log("Stop requested — finishing the current application then halting")

    def _log(self, message: str) -> None:
        with self._lock:
            self.state["messages"] = (self.state["messages"] + [
                f"{time.strftime('%H:%M:%S')}  {message}"
            ])[-200:]
        log.info(message)

    def _set(self, **kwargs) -> None:
        with self._lock:
            self.state.update(kwargs)

    # ------------------------------------------------------------------ #
    def start(self, application_ids: list[int], auto_submit: bool | None = None) -> dict:
        if self.is_running():
            return {"ok": False, "error": "A run is already in progress"}

        ok, message = autofill.playwright_available()
        if not ok:
            return {"ok": False, "error": message}

        self._stop.clear()
        self._set(
            running=True, done=0, total=len(application_ids), current=None,
            messages=[], started_at=utcnow().isoformat(timespec="seconds"),
            finished_at=None,
            mode="auto-submit" if auto_submit else "fill & review",
        )
        self._thread = threading.Thread(
            target=self._run, args=(application_ids, auto_submit), daemon=True,
            name="jobpilot-apply",
        )
        self._thread.start()
        return {"ok": True, "queued": len(application_ids)}

    # ------------------------------------------------------------------ #
    def _run(self, application_ids: list[int], auto_submit_override: bool | None) -> None:
        try:
            with SessionLocal() as db:
                profile = db.get(Profile, 1)
                auto_submit = (
                    profile.auto_submit if auto_submit_override is None else auto_submit_override
                )
                delay = max(5, int(profile.apply_delay_seconds or 30))
                limit = int(profile.daily_apply_limit or 0)

                self._log(
                    f"Starting {len(application_ids)} application(s) — "
                    f"mode: {'AUTO-SUBMIT' if auto_submit else 'fill & review'}, "
                    f"{delay}s between each"
                )

                for index, app_id in enumerate(application_ids):
                    if self._stop.is_set():
                        self._log("Stopped by user")
                        break

                    if auto_submit and limit:
                        today = pipeline.applications_today(db)
                        if today >= limit:
                            self._log(
                                f"Daily limit reached ({today}/{limit}) — stopping. "
                                "Raise it in Settings if you want more."
                            )
                            break

                    self._process_one(db, app_id, auto_submit)
                    self._set(done=index + 1)

                    if index + 1 < len(application_ids) and not self._stop.is_set():
                        self._log(f"Waiting {delay}s before the next application…")
                        for _ in range(delay):
                            if self._stop.is_set():
                                break
                            time.sleep(1)

        except Exception as exc:  # noqa: BLE001
            self._log(f"Worker crashed: {type(exc).__name__}: {exc}")
            log.exception("worker crashed")
        finally:
            self._set(
                running=False, current=None,
                finished_at=utcnow().isoformat(timespec="seconds"),
            )
            self._log("Run finished")

    # ------------------------------------------------------------------ #
    def _process_one(self, db, app_id: int, auto_submit: bool) -> None:
        application = db.get(Application, app_id)
        if application is None:
            self._log(f"Application {app_id} vanished; skipping")
            return

        job = db.get(Job, application.job_id)
        profile = db.get(Profile, 1)
        if job is None:
            application.status = "failed"
            application.error = "Job no longer exists"
            db.commit()
            return

        self._set(current={
            "id": application.id, "title": job.title, "company": job.company,
            "url": job.apply_url, "score": application.score,
        })
        self._log(f"▶ {job.title} @ {job.company or 'unknown'} (score {application.score})")

        # 1. tailor + generate documents if not already done
        if application.status in ("queued", "failed") or not application.resume_pdf:
            self._log("  Tailoring resume and generating documents…")
            application = pipeline.prepare_application(db, application)
            if application.status == "failed":
                self._log(f"  ✗ {application.error}")
                return
            self._log("  Documents ready")

        if not job.apply_url:
            application.status = "failed"
            application.error = "No application URL for this posting"
            db.commit()
            self._log("  ✗ No application URL")
            return

        # 2. build the value table for form filling
        resume = db.get(Resume, application.resume_id) if application.resume_id else None
        tailored = None
        if application.tailored_json:
            try:
                from .tailor import TailoredResume

                tailored = TailoredResume.model_validate(
                    {k: v for k, v in application.tailored_json.items()
                     if k != "cover_letter_pdf"}
                )
            except Exception:
                tailored = None

        values = F.build_values(profile, resume, application, tailored, job=job)
        cover_pdf = (application.tailored_json or {}).get("cover_letter_pdf", "")
        if cover_pdf and Path(cover_pdf).exists():
            values["cover_letter_file"] = cover_pdf

        # 3. drive the browser
        self._log(f"  Opening the application form ({autofill.detect_ats(job.apply_url)})…")
        report = autofill.apply_to_job(
            job.apply_url,
            values,
            headless=bool(profile.headless),
            auto_submit=auto_submit,
            custom_answers=profile.custom_answers or {},
            screenshot_name=f"app-{application.id:05d}",
            # Aggregator listings sometimes carry the employer's real ATS link
            # inside the description; that beats filling their listing page.
            description=job.description or "",
        )

        application.ats = report.ats
        application.filled_fields = report.filled
        application.unfilled_fields = report.unfilled
        application.screenshot = report.screenshot
        application.log = (application.log or []) + report.log
        application.error = report.error

        if report.error:
            application.status = "failed"
            self._log(f"  ✗ {report.error}")
        elif report.submitted:
            application.status = "submitted"
            application.submitted_at = utcnow()
            # Enter the tracker straight away so the follow-up clock starts now
            # rather than whenever you next open the Applications tab.
            if not application.outcome:
                tracker.record(application, tracker.DEFAULT_OUTCOME,
                               "Submitted by JobPilot")
            self._log(
                f"  ✓ Submitted ({len(report.filled)} fields)"
                + ("" if report.success_detected else " — no confirmation text, verify manually")
            )
        else:
            application.status = "needs_review"
            self._log(
                f"  ⏸ Filled {len(report.filled)} fields, "
                f"{len(report.unfilled)} need you — open it to review and submit"
            )

        db.commit()


worker = ApplyWorker()


def pending_application_ids(db, limit: int = 25) -> list[int]:
    return list(db.scalars(
        select(Application.id)
        .where(Application.status.in_(("queued", "ready", "failed")))
        .order_by(Application.score.desc())
        .limit(limit)
    ))
