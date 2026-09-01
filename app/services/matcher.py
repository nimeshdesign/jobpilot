"""Resume <-> job-description matching.

Produces a 0-100 score plus a breakdown you can actually read, so a bad match is
explainable rather than a black box.

Components
    skills   40%  weighted overlap of hard skills the JD asks for
    content  25%  TF-IDF cosine of resume text vs. job description
    title    15%  overlap between your target titles and the job title
    prefs    20%  remote / visa / country / keyword preferences

Then modifiers: seniority distance, years-of-experience gap, exclusion keywords.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .skills import (
    TECH_CLUSTER,
    detect_seniority,
    extract_skills,
    families_for,
    job_family,
    skill_weight,
    years_required,
)
from .textutil import cosine_tfidf, tokenize

WEIGHTS = {"skills": 0.40, "content": 0.25, "title": 0.15, "prefs": 0.20}

# How many distinct skills a job description must name before its skill-coverage
# percentage is taken at face value. Below this the score is blended towards
# neutral in proportion to how little the posting actually said.
MIN_SKILLS_FOR_CONFIDENCE = 6


@dataclass
class MatchResult:
    score: float
    breakdown: dict[str, float]
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    disqualified: bool = False


def _title_similarity(job_title: str, target_titles: list[str], resume_titles: list[str]) -> float:
    job_tokens = set(tokenize(job_title))
    if not job_tokens:
        return 0.0

    best = 0.0
    for candidate in [*(target_titles or []), *(resume_titles or [])]:
        cand_tokens = set(tokenize(candidate))
        if not cand_tokens:
            continue
        overlap = len(job_tokens & cand_tokens) / len(cand_tokens)
        best = max(best, overlap)
    return min(1.0, best)


def _preference_score(job, profile, reasons: list[str]) -> float:
    score, total = 0.0, 0.0

    # remote
    total += 1.0
    if job.is_remote:
        score += 1.0
        reasons.append("Remote role")
    elif profile.remote_only:
        reasons.append("Not marked remote")

    # visa sponsorship
    total += 1.2
    if job.visa_status == "yes":
        score += 1.2
        reasons.append("Visa sponsorship signalled in the posting")
    elif job.visa_status == "global":
        score += 1.0
        reasons.append("Employer hires globally — no visa needed for a remote role")
    elif job.visa_status == "likely":
        score += 0.9
        reasons.append("Employer is a known visa sponsor, though this ad is silent on it")
    elif job.visa_status == "unknown":
        score += 0.45
    else:
        reasons.append("Posting says no sponsorship")

    # preferred countries
    preferred = [c.strip().lower() for c in (profile.preferred_countries or []) if c.strip()]
    if preferred:
        total += 0.6
        haystack = f"{job.country} {job.location}".lower()
        if any(country in haystack for country in preferred):
            score += 0.6
            reasons.append(f"Location matches your preferences ({job.location or job.country})")
        elif job.is_remote:
            score += 0.3

    # must-have keywords
    must_have = [k.strip().lower() for k in (profile.must_have_keywords or []) if k.strip()]
    if must_have:
        total += 0.8
        blob = f"{job.title} {job.description}".lower()
        hits = [k for k in must_have if k in blob]
        if hits:
            score += 0.8 * (len(hits) / len(must_have))
            reasons.append(f"Mentions your keywords: {', '.join(hits[:4])}")

    return score / total if total else 0.5


def match(job, resume, profile) -> MatchResult:
    """Score one job against one resume under the given profile preferences."""
    reasons: list[str] = []
    parsed = resume.parsed or {}

    job_text = f"{job.title}\n{job.description}"
    job_skills = list(job.job_skills or []) or extract_skills(job_text)
    resume_skills = list(resume.skills or []) or extract_skills(resume.raw_text or "")

    resume_lookup = {s.lower() for s in resume_skills}
    matched = [s for s in job_skills if s.lower() in resume_lookup]
    missing = [s for s in job_skills if s.lower() not in resume_lookup]

    # --- skills component (weighted so hard skills dominate) ---
    demanded = sum(skill_weight(s) for s in job_skills)
    covered = sum(skill_weight(s) for s in matched)
    skills_score = covered / demanded if demanded > 0 else 0.5

    # Coverage over a two-skill job description is not evidence of anything:
    # "1 of 1 skills matched" is 100% and means nothing. Blend towards neutral
    # when the posting is too vague to judge, so thin ads stop out-ranking
    # detailed ones that list ten requirements and you meet eight.
    confidence = min(1.0, len(job_skills) / MIN_SKILLS_FOR_CONFIDENCE)
    skills_score = skills_score * confidence + 0.5 * (1 - confidence)
    if confidence < 0.6:
        reasons.append(
            f"Vague posting — only {len(job_skills)} concrete skill(s) named, "
            "so this score is a weak signal"
        )

    # --- content similarity ---
    content_score = cosine_tfidf(resume.raw_text or "", job_text)

    # --- title fit ---
    resume_titles = [
        role.get("title", "") for role in (parsed.get("experience") or [])[:4] if role.get("title")
    ]
    title_score = _title_similarity(job.title, profile.target_titles or [], resume_titles)

    # --- preferences ---
    prefs_score = _preference_score(job, profile, reasons)

    base = (
        WEIGHTS["skills"] * skills_score
        + WEIGHTS["content"] * content_score
        + WEIGHTS["title"] * title_score
        + WEIGHTS["prefs"] * prefs_score
    )

    # ------------------------------------------------------------------ #
    # modifiers
    # ------------------------------------------------------------------ #
    modifier = 1.0
    disqualified = False

    # Profession check. Large employers reuse one boilerplate template across
    # every opening, so a Legal Counsel or Benefits Analyst ad at a software
    # company still matches Git, CSS and "collaboration". Without this, those
    # roles crowd out the actual engineering jobs.
    target_family = job_family(job.title or "")
    my_families = families_for(
        list(profile.target_titles or []) + resume_titles
    )
    if target_family and my_families and target_family not in my_families:
        if target_family in TECH_CLUSTER and my_families & TECH_CLUSTER:
            modifier -= 0.08          # adjacent tech discipline: a real option
            reasons.append(f"Different discipline ({target_family}) but still in tech")
        else:
            modifier -= 0.40          # different profession entirely
            reasons.append(
                f"This is a {target_family} role — your background is "
                f"{'/'.join(sorted(my_families))}"
            )

    job_seniority, job_rank = detect_seniority(job.title or "")
    resume_rank = int(parsed.get("seniority_rank", 2))
    gap = job_rank - resume_rank
    if gap >= 2:
        modifier -= 0.18
        reasons.append(f"Role is more senior than your profile ({job_seniority})")
    elif gap <= -2:
        modifier -= 0.12
        reasons.append(f"Role is more junior than your profile ({job_seniority})")

    needed_years = years_required(job.description or "")
    have_years = float(profile.years_experience or parsed.get("years_experience") or 0)
    if needed_years and have_years:
        shortfall = needed_years - have_years
        if shortfall >= 4:
            modifier -= 0.20
            reasons.append(f"Asks for {needed_years:.0f}+ years, your resume shows ~{have_years:.0f}")
        elif shortfall >= 2:
            modifier -= 0.08

    blob = f"{job.title} {job.company} {job.description}".lower()
    for excluded in (profile.exclude_keywords or []):
        term = excluded.strip().lower()
        if term and term in blob:
            disqualified = True
            reasons.append(f"Excluded by your keyword: '{excluded}'")
            break

    if profile.remote_only and not job.is_remote:
        modifier -= 0.25
    if profile.visa_only and job.visa_status not in ("yes", "likely", "global"):
        modifier -= 0.30 if job.visa_status == "unknown" else 0.60

    score = max(0.0, min(100.0, base * modifier * 100))
    if disqualified:
        score = 0.0

    if matched:
        reasons.insert(0, f"{len(matched)}/{len(job_skills)} required skills matched")

    return MatchResult(
        score=round(score, 1),
        breakdown={
            "skills": round(skills_score * 100, 1),
            "skills_raw": round((covered / demanded * 100) if demanded else 0.0, 1),
            "skills_named": len(job_skills),
            "content": round(content_score * 100, 1),
            "title": round(title_score * 100, 1),
            "preferences": round(prefs_score * 100, 1),
            "modifier": round(modifier, 2),
        },
        matched_skills=matched,
        missing_skills=missing,
        reasons=reasons[:8],
        disqualified=disqualified,
    )
