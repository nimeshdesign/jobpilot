"""Per-job resume tailoring + cover letter generation.

Two engines:
  * LLM (needs ANTHROPIC_API_KEY) -- rewrites the summary and re-angles existing
    bullets towards the job description.
  * Rule-based (always available) -- re-ranks and re-orders what is already in
    your resume by relevance to the JD, and rewrites the summary from a template.

Both are constrained to *your* material. Neither invents employers, dates,
degrees, or skills you do not have -- an ATS resume that lies is a liability,
and inflated claims fall apart in the first interview.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from . import llm
from .textutil import cosine_tfidf, truncate

SYSTEM_PROMPT = """You are an expert technical recruiter and resume writer helping a \
candidate tailor their existing resume to one specific job posting.

HARD RULES — these are non-negotiable:
1. Use ONLY facts present in the candidate's resume. Never invent or imply an employer, \
job title, date, degree, certification, metric, or technology the resume does not contain.
2. Never claim experience with a technology that is missing from the resume. If the job \
requires something the candidate lacks, list it under `gaps` instead of fabricating it.
3. You may rephrase, reorder, re-emphasise, merge, and re-angle existing bullet points \
towards the job's priorities. You may surface a genuinely-present skill that was buried.
4. Keep every rewritten bullet concrete and results-oriented. Start with a strong verb. \
Preserve any real numbers from the original; never invent new ones.
5. Mirror the job posting's own vocabulary where it honestly describes the candidate's \
work — this is what gets past keyword-filtering ATS software.
6. The summary is 2-4 sentences, first person implied (no "I"), no clichés like \
"results-driven professional" or "proven track record".

Write in clear, plain, professional English."""


class TailoredRole(BaseModel):
    title: str = Field(description="Job title, copied verbatim from the resume")
    company: str = Field(description="Employer name, copied verbatim from the resume")
    dates: str = Field(default="", description="Date range as it appears in the resume")
    bullets: list[str] = Field(
        default_factory=list,
        description="3-5 rewritten bullets, re-angled towards this job. Facts unchanged.",
    )


class TailoredResume(BaseModel):
    headline: str = Field(
        description="Short professional headline aligned to the target job title"
    )
    summary: str = Field(description="2-4 sentence professional summary tailored to this job")
    top_skills: list[str] = Field(
        default_factory=list,
        description="8-14 skills the candidate genuinely has, ordered by relevance to this job",
    )
    experience: list[TailoredRole] = Field(
        default_factory=list, description="Roles in reverse-chronological order"
    )
    keywords_added: list[str] = Field(
        default_factory=list,
        description="Job-posting keywords surfaced because the candidate genuinely has them",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Requirements the candidate does NOT meet. Be honest here.",
    )
    cover_letter: str = Field(
        default="",
        description=(
            "180-260 word cover letter. Specific to this company and role, no flattery, "
            "no invented facts. Plain paragraphs, no greeting placeholders like [Company]."
        ),
    )
    fit_notes: str = Field(
        default="", description="One or two sentences on why this candidate fits, for your own eyes"
    )


# --------------------------------------------------------------------------- #
# LLM engine
# --------------------------------------------------------------------------- #
def _build_prompt(resume, job, match_result, profile) -> str:
    parsed = resume.parsed or {}
    contact = parsed.get("contact", {})

    roles_text = []
    for role in (parsed.get("experience") or [])[:6]:
        bullets = "\n".join(f"    - {b}" for b in (role.get("bullets") or [])[:8])
        roles_text.append(
            f"  * {role.get('title', '')} | {role.get('company', '')} "
            f"| {role.get('start', '')} - {role.get('end', '')}\n{bullets}"
        )

    education = "\n".join(f"  - {e.get('text', '')}" for e in (parsed.get("education") or [])[:4])

    return f"""## TARGET JOB

Title: {job.title}
Company: {job.company}
Location: {job.location or "not stated"} {"(remote)" if job.is_remote else ""}
Visa sponsorship signal: {job.visa_status}

Job description:
\"\"\"
{truncate(job.description, 9000)}
\"\"\"

## CANDIDATE RESUME

Name: {contact.get("name") or profile.full_name}
Headline seniority: {parsed.get("seniority", "mid")}
Approximate years of experience: {profile.years_experience or parsed.get("years_experience", 0)}

Current summary:
{parsed.get("summary") or "(none in resume)"}

Skills listed on the resume:
{", ".join(resume.skills or []) or "(none detected)"}

Experience:
{chr(10).join(roles_text) or "(none parsed)"}

Education:
{education or "(none parsed)"}

Full resume text (authoritative — the sections above are a parse of this):
\"\"\"
{truncate(resume.raw_text or "", 9000)}
\"\"\"

## MATCH ANALYSIS (computed locally)

Overall match score: {match_result.get("score", 0)}/100
Skills the job asks for that the candidate HAS: {", ".join(match_result.get("matched_skills", [])) or "none"}
Skills the job asks for that are MISSING: {", ".join(match_result.get("missing_skills", [])) or "none"}

## TASK

Tailor the resume to this posting and draft a cover letter. Surface the matched skills \
prominently. Do not claim any of the missing skills — put them in `gaps` instead. \
{"Mention willingness to relocate and the need for visa sponsorship in the cover letter." if profile.needs_sponsorship else ""}
"""


def tailor_with_llm(resume, job, match_result, profile) -> TailoredResume | None:
    return llm.structured(
        system=SYSTEM_PROMPT,
        prompt=_build_prompt(resume, job, match_result, profile),
        schema=TailoredResume,
        max_tokens=16000,
    )


# --------------------------------------------------------------------------- #
# rule-based engine (offline fallback)
# --------------------------------------------------------------------------- #
def clean_title(title: str) -> str:
    """Strip the internal team names employers bolt onto job titles.

    "Senior Frontend Engineer (Vue), Create: Repository Miscellaneous" is a
    routing label, not a headline. Keeping it makes the resume read as though it
    were written by a machine, which is exactly what we are trying to avoid.
    """
    import re

    cleaned = (title or "").strip()
    cleaned = re.split(r"\s*[-–—|]\s*(?:req|job)?\s*\d{3,}", cleaned)[0]  # trailing req IDs
    cleaned = cleaned.split(",")[0]                       # ", Home Experience"
    # Parentheticals go first: stripping "- Remote" while it sits inside
    # "(USA Only - 100% Remote)" would leave a dangling "(USA Only".
    cleaned = re.sub(r"\s*\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\s*\(.*$", "", cleaned)            # unterminated "(..."
    cleaned = re.sub(
        r"\s*[-–—|]\s*(remote|hybrid|onsite|on-site|contract|full[- ]time|part[- ]time"
        r"|permanent|freelance|\d+%).*$",
        "", cleaned, flags=re.IGNORECASE,
    )

    # Drop a trailing "- something" when that something contains no role words:
    # it is a team name, a tagline or a posting qualifier, not part of the job
    # title. "Backend Software Engineer Senior - Vaga afirmativa para mulheres"
    # should not be the sentence in your cover letter. A suffix that does carry
    # a role word ("Software Engineer - Backend") is kept.
    from .resume_parser import TITLE_WORDS

    parts = re.split(r"\s+[-–—|]\s+", cleaned)
    if len(parts) > 1 and parts[0].strip():
        head = parts[0]
        tail_words = {re.sub(r"[^a-z\-]", "", w.lower()) for w in " ".join(parts[1:]).split()}
        if not (tail_words & TITLE_WORDS):
            cleaned = head

    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -–—|:") or (title or "").strip()


def tailor_with_rules(resume, job, match_result, profile) -> TailoredResume:
    parsed = resume.parsed or {}
    contact = parsed.get("contact", {})
    job_text = f"{job.title}\n{job.description}"

    matched = match_result.get("matched_skills", [])
    missing = match_result.get("missing_skills", [])

    # Order by relevance, but hard skills before ways-of-working ones. A resume
    # that opens with "Leadership, Collaboration" buries the reason to hire you;
    # "React, TypeScript, Vue.js" is what a screener is scanning for.
    from .skills import skill_weight

    def by_value(skills: list[str]) -> list[str]:
        return sorted(skills, key=lambda s: -skill_weight(s))

    # Re-bind `matched` itself so the summary sentence, the skills line and the
    # cover letter all inherit the ordering. Sorting only at the point of use
    # meant fixing this in one place and leaving it broken in two others.
    matched = by_value(matched)

    # Hard skills before soft ones across the WHOLE list, then JD-matched first
    # within each tier. Ordering matched-first alone meant a job whose only
    # overlap was "Leadership" put Leadership at the top of the resume, ahead of
    # React and TypeScript — the opposite of what a screener needs to see.
    resume_skills = list(resume.skills or [])
    matched_set = set(matched)
    ordered = sorted(
        matched + [s for s in resume_skills if s not in matched_set],
        key=lambda s: (-skill_weight(s), 0 if s in matched_set else 1),
    )

    # Re-rank each role's bullets by similarity to the job description.
    roles: list[TailoredRole] = []
    for role in (parsed.get("experience") or [])[:5]:
        bullets = list(role.get("bullets") or [])
        ranked = sorted(bullets, key=lambda b: cosine_tfidf(b, job_text), reverse=True)
        roles.append(TailoredRole(
            title=role.get("title", ""),
            company=role.get("company", ""),
            dates=" - ".join(x for x in [role.get("start", ""), role.get("end", "")] if x),
            bullets=ranked[:5],
        ))

    years = profile.years_experience or parsed.get("years_experience") or 0
    headline = clean_title(job.title) or (
        roles[0].title if roles else "Software Professional"
    )

    skill_phrase = ", ".join(ordered[:6]) or "a broad technical toolkit"
    summary_bits = [
        f"{headline} with {years:.0f}+ years of experience" if years
        else f"{headline} with hands-on delivery experience",
        f"working across {skill_phrase}.",
    ]
    if matched:
        summary_bits.append(
            f"Directly relevant to this role: {', '.join(matched[:5])}."
        )
    if job.company:
        summary_bits.append(f"Looking to bring that experience to {job.company}.")
    summary = " ".join(summary_bits)

    name = contact.get("name") or profile.full_name or "the candidate"
    # Give the letter the hard skills to talk about. If the only thing this job
    # matched was "Collaboration", naming it is worse than naming the stack you
    # actually work in.
    letter_skills = [s for s in ordered if skill_weight(s) >= 1.0][:6] or ordered[:6]
    letter = _rule_cover_letter(name, job, letter_skills, years, profile)

    return TailoredResume(
        headline=headline,
        summary=summary,
        top_skills=ordered[:14],
        experience=roles,
        keywords_added=matched[:12],
        gaps=missing[:10],
        cover_letter=letter,
        fit_notes=(
            f"Rule-based tailoring. {len(matched)} of {len(matched) + len(missing)} "
            f"required skills present."
        ),
    )


def _rule_cover_letter(name, job, matched, years, profile) -> str:
    company = job.company or "your team"

    # Sort here rather than trusting callers. The resume path was already
    # ordering hard skills first, but the letter was not, so cover letters went
    # out opening with "Leadership, Collaboration" instead of the stack.
    from .skills import skill_weight

    ranked = sorted(matched or [], key=lambda s: -skill_weight(s))
    skills = ", ".join(ranked[:4]) if ranked else "the areas this role covers"

    paragraphs = [
        f"I am writing to apply for the {clean_title(job.title)} role at {company}.",
        (
            f"I have {years:.0f}+ years of experience"
            if years
            else "I have hands-on delivery experience"
        )
        + f" working with {skills}, which maps directly to what this role calls for. "
        "My attached resume sets out the specific projects and the results they produced.",
    ]
    if job.is_remote:
        paragraphs.append(
            "I have worked effectively in distributed teams and am comfortable with "
            "asynchronous collaboration across time zones."
        )
    if profile.needs_sponsorship:
        # Only name a status if one was actually given. The old fallback emitted
        # "On work authorisation: my current work authorisation." into real
        # cover letters, which reads as an unfilled template.
        status = (profile.current_visa_status or "").strip()
        sentence = f"On work authorisation: {status}. " if status else ""
        sentence += "I would require visa sponsorship for this position"
        sentence += " and am open to relocation." if profile.willing_to_relocate else "."
        paragraphs.append(sentence)
    paragraphs.append(
        "I would welcome the chance to discuss how I can contribute. Thank you for your time."
    )
    signoff = f"\n\nBest regards,\n{name}"
    return "\n\n".join(paragraphs) + signoff


# --------------------------------------------------------------------------- #
def tailor(resume, job, match_result, profile) -> tuple[TailoredResume, str]:
    """Tailor with the best engine available. Returns (result, engine_name)."""
    if profile.use_llm and llm.available():
        result = tailor_with_llm(resume, job, match_result, profile)
        if result is not None:
            if not result.cover_letter and profile.generate_cover_letter:
                result.cover_letter = _rule_cover_letter(
                    (resume.parsed or {}).get("contact", {}).get("name") or profile.full_name,
                    job,
                    match_result.get("matched_skills", []),
                    profile.years_experience or 0,
                    profile,
                )
            return result, "llm"
    return tailor_with_rules(resume, job, match_result, profile), "rules"
