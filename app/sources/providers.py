"""Job-board connectors.

Every source here is a documented, public, no-auth JSON API. Nothing scrapes
behind a login and nothing is called faster than once per refresh.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

import httpx

from ..config import USER_AGENT
from .base import JobPost, parse_date

log = logging.getLogger("jobpilot.sources")

Fetcher = Callable[[httpx.AsyncClient, dict], Coroutine[Any, Any, list[JobPost]]]
REGISTRY: dict[str, dict] = {}


def source(key: str, label: str, description: str, default_on: bool = True):
    def wrapper(fn: Fetcher) -> Fetcher:
        REGISTRY[key] = {
            "key": key, "label": label, "description": description,
            "default_on": default_on, "fetch": fn,
        }
        return fn

    return wrapper


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs) -> Any:
    response = await client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
@source("remotive", "Remotive", "Curated remote jobs, worldwide.")
async def fetch_remotive(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    params: dict[str, Any] = {"limit": opts.get("limit", 120)}
    if opts.get("query"):
        params["search"] = opts["query"]

    data = await _get_json(client, "https://remotive.com/api/remote-jobs", params=params)
    posts: list[JobPost] = []
    for item in data.get("jobs", []):
        posts.append(JobPost(
            source="remotive",
            external_id=str(item.get("id")),
            title=item.get("title", ""),
            company=item.get("company_name", ""),
            location=item.get("candidate_required_location", "") or "Remote",
            is_remote=True,
            employment_type=item.get("job_type", ""),
            salary=item.get("salary", "") or "",
            url=item.get("url", ""),
            description=item.get("description", ""),
            tags=item.get("tags", []) or [],
            posted_at=parse_date(item.get("publication_date")),
        ).finalize())
    return posts


@source("arbeitnow", "Arbeitnow", "EU / Germany-heavy board — richest source of visa-sponsored roles.")
async def fetch_arbeitnow(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    posts: list[JobPost] = []
    pages = max(1, min(6, int(opts.get("pages", 3))))
    for page in range(1, pages + 1):
        try:
            data = await _get_json(
                client, "https://www.arbeitnow.com/api/job-board-api", params={"page": page}
            )
        except httpx.HTTPError as exc:
            log.warning("arbeitnow page %s failed: %s", page, exc)
            break

        items = data.get("data", [])
        if not items:
            break
        for item in items:
            posts.append(JobPost(
                source="arbeitnow",
                external_id=str(item.get("slug")),
                title=item.get("title", ""),
                company=item.get("company_name", ""),
                location=item.get("location", ""),
                is_remote=bool(item.get("remote")),
                employment_type=", ".join(item.get("job_types", []) or []),
                url=item.get("url", ""),
                description=item.get("description", ""),
                tags=item.get("tags", []) or [],
                posted_at=parse_date(item.get("created_at")),
                visa_flag=item.get("visa_sponsorship"),
            ).finalize())
    return posts


@source("remoteok", "RemoteOK", "High-volume remote board.")
async def fetch_remoteok(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    data = await _get_json(client, "https://remoteok.com/api")
    posts: list[JobPost] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue  # first element is a legal notice, not a job
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        salary = f"${salary_min:,} - ${salary_max:,}" if salary_min and salary_max else ""
        posts.append(JobPost(
            source="remoteok",
            external_id=str(item.get("id")),
            title=item.get("position", "") or item.get("title", ""),
            company=item.get("company", ""),
            location=item.get("location", "") or "Remote",
            is_remote=True,
            salary=salary,
            url=item.get("url", ""),
            apply_url=item.get("apply_url", "") or item.get("url", ""),
            description=item.get("description", ""),
            tags=item.get("tags", []) or [],
            posted_at=parse_date(item.get("epoch") or item.get("date")),
        ).finalize())
    return posts


@source("himalayas", "Himalayas", "Remote jobs with location-restriction metadata.")
async def fetch_himalayas(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    data = await _get_json(
        client, "https://himalayas.app/jobs/api", params={"limit": opts.get("limit", 100)}
    )
    posts: list[JobPost] = []
    for item in data.get("jobs", []):
        restrictions = item.get("locationRestrictions") or []
        posts.append(JobPost(
            source="himalayas",
            external_id=str(item.get("guid") or item.get("title")),
            title=item.get("title", ""),
            company=item.get("companyName", ""),
            location=", ".join(restrictions) if restrictions else "Worldwide",
            is_remote=True,
            employment_type=", ".join(item.get("employmentType", []) or [])
            if isinstance(item.get("employmentType"), list) else (item.get("employmentType") or ""),
            url=item.get("applicationLink", "") or item.get("guid", ""),
            description=item.get("description", "") or item.get("excerpt", ""),
            tags=item.get("categories", []) or [],
            posted_at=parse_date(item.get("pubDate")),
        ).finalize())
    return posts


@source("jobicy", "Jobicy", "Remote board with region filters.")
async def fetch_jobicy(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    params: dict[str, Any] = {"count": min(50, int(opts.get("limit", 50)))}
    if opts.get("query"):
        params["tag"] = opts["query"]

    data = await _get_json(client, "https://jobicy.com/api/v2/remote-jobs", params=params)
    posts: list[JobPost] = []
    for item in data.get("jobs", []):
        salary_min, salary_max = item.get("annualSalaryMin"), item.get("annualSalaryMax")
        currency = item.get("salaryCurrency", "") or ""
        salary = f"{currency} {salary_min:,} - {salary_max:,}" if salary_min and salary_max else ""
        posts.append(JobPost(
            source="jobicy",
            external_id=str(item.get("id")),
            title=item.get("jobTitle", ""),
            company=item.get("companyName", ""),
            location=item.get("jobGeo", "") or "Anywhere",
            is_remote=True,
            employment_type=", ".join(item.get("jobType", []) or [])
            if isinstance(item.get("jobType"), list) else (item.get("jobType") or ""),
            salary=salary.strip(),
            url=item.get("url", ""),
            description=item.get("jobDescription", "") or item.get("jobExcerpt", ""),
            tags=item.get("jobIndustry", []) or [],
            posted_at=parse_date(item.get("pubDate")),
        ).finalize())
    return posts


# Greenhouse / Lever boards are per-company. These defaults skew towards firms
# that hire internationally; edit the list in Settings -> Company boards.
DEFAULT_GREENHOUSE_BOARDS = [
    "gitlab", "elastic", "mongodb", "datadog", "stripe", "airbnb", "dropbox", "cloudflare",
    "figma", "canva", "grafanalabs", "hashicorp", "sourcegraph", "vercel", "airtable",
]
DEFAULT_LEVER_BOARDS = [
    "netflix", "spotify", "revolut", "plaid", "brex", "ramp", "wealthsimple", "hopin",
]


@source("greenhouse", "Greenhouse boards", "Company career boards (edit list in Settings).")
async def fetch_greenhouse(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    boards = opts.get("greenhouse_boards") or DEFAULT_GREENHOUSE_BOARDS
    posts: list[JobPost] = []

    async def one(token: str) -> list[JobPost]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        try:
            data = await _get_json(client, url)
        except httpx.HTTPError as exc:
            log.info("greenhouse board %s unavailable: %s", token, exc)
            return []

        out: list[JobPost] = []
        for item in data.get("jobs", []):
            offices = [o.get("name", "") for o in (item.get("offices") or [])]
            out.append(JobPost(
                source="greenhouse",
                external_id=f"{token}:{item.get('id')}",
                title=item.get("title", ""),
                company=token.replace("-", " ").title(),
                location=(item.get("location") or {}).get("name", "") or ", ".join(offices),
                url=item.get("absolute_url", ""),
                description=item.get("content", ""),
                posted_at=parse_date(item.get("updated_at")),
            ).finalize())
        return out

    results = await asyncio.gather(*(one(t) for t in boards), return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            posts.extend(result)
    return posts


@source("lever", "Lever boards", "Company career boards (edit list in Settings).", default_on=False)
async def fetch_lever(client: httpx.AsyncClient, opts: dict) -> list[JobPost]:
    boards = opts.get("lever_boards") or DEFAULT_LEVER_BOARDS
    posts: list[JobPost] = []

    async def one(token: str) -> list[JobPost]:
        url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        try:
            data = await _get_json(client, url)
        except httpx.HTTPError as exc:
            log.info("lever board %s unavailable: %s", token, exc)
            return []

        out: list[JobPost] = []
        for item in data:
            categories = item.get("categories") or {}
            out.append(JobPost(
                source="lever",
                external_id=f"{token}:{item.get('id')}",
                title=item.get("text", ""),
                company=token.replace("-", " ").title(),
                location=categories.get("location", "") or "",
                employment_type=categories.get("commitment", "") or "",
                url=item.get("hostedUrl", ""),
                apply_url=item.get("applyUrl", "") or item.get("hostedUrl", ""),
                description=item.get("descriptionPlain", "") or item.get("description", ""),
                tags=[categories.get("team", "")] if categories.get("team") else [],
                posted_at=parse_date(item.get("createdAt")),
            ).finalize())
        return out

    results = await asyncio.gather(*(one(t) for t in boards), return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            posts.extend(result)
    return posts


# --------------------------------------------------------------------------- #
async def fetch_all(keys: list[str], opts: dict | None = None) -> tuple[list[JobPost], list[str]]:
    """Run the selected sources concurrently. Returns (posts, per-source notes)."""
    opts = opts or {}
    keys = [k for k in keys if k in REGISTRY] or [
        k for k, v in REGISTRY.items() if v["default_on"]
    ]
    notes: list[str] = []
    posts: list[JobPost] = []

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(45.0, connect=15.0), headers=headers,
        follow_redirects=True, limits=limits,
    ) as client:
        results = await asyncio.gather(
            *(REGISTRY[key]["fetch"](client, opts) for key in keys), return_exceptions=True
        )

    for key, result in zip(keys, results):
        label = REGISTRY[key]["label"]
        if isinstance(result, Exception):
            notes.append(f"{label}: failed ({type(result).__name__}: {result})")
            log.warning("source %s failed: %s", key, result)
            continue
        notes.append(f"{label}: {len(result)} postings")
        posts.extend(result)

    return posts, notes


def list_sources() -> list[dict]:
    return [
        {k: v for k, v in meta.items() if k != "fetch"}
        for meta in REGISTRY.values()
    ]
