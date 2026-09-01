"""Resume ingestion: PDF/DOCX/TXT -> raw text -> structured sections.

Heuristics do the whole job offline. If an Anthropic API key is configured the
structure is refined by the model (better at messy multi-column layouts), but
the rule-based result is always kept as a fallback.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .skills import detect_seniority, extract_skills
from .textutil import normalize

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{3,5}[\s.-]?\d{3,4}[\s.-]?\d{0,4}")
# Resumes very often print profile links bare -- "linkedin.com/in/jane" with no
# scheme and no www -- so those have to match too, not just full URLs.
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;)\]|]+"
    r"|(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|medium\.com"
    r"|stackoverflow\.com)/[^\s,;)\]|]+",
    re.IGNORECASE,
)

# Words that mark a line as a job title rather than an employer name. Used to
# decide which of two adjacent lines is the role and which is the company.
TITLE_WORDS = frozenset("""
developer engineer designer manager analyst consultant architect lead specialist
administrator scientist researcher director officer intern trainee associate
programmer coordinator strategist marketer writer editor accountant executive
head chief president founder cto cpo cio vp qa sre devops fullstack full-stack
frontend front-end backend back-end
""".split())

COMPANY_MARKERS = re.compile(
    r"\b(inc|llc|ltd|limited|pvt|gmbh|bv|nv|plc|corp|corporation|company|co|"
    r"solutions|technologies|technology|systems|labs|studio|studios|group|"
    r"consulting|services|software|infosolutions|agency|media|digital)\b\.?",
    re.IGNORECASE,
)

# "Ahmedabad, India" / "San Francisco, CA" -- a trailing place, on its own.
LOCATION_TAIL_RE = re.compile(
    r"^[A-Z][\w.\-]+(?:[ \-][A-Z][\w.\-]+)*,\s*[A-Z][\w.\- ]+$"
)

SECTION_PATTERNS: dict[str, list[str]] = {
    "summary": ["professional summary", "career summary", "summary", "profile", "objective",
                "about me", "career objective", "professional profile"],
    "experience": ["work experience", "professional experience", "employment history",
                   "experience", "work history", "career history", "relevant experience"],
    "education": ["education", "academic background", "academic qualifications", "qualifications"],
    "skills": ["technical skills", "core skills", "skills & expertise", "skills", "competencies",
               "core competencies", "technologies", "tech stack", "expertise"],
    "projects": ["projects", "personal projects", "key projects", "selected projects",
                 "side projects"],
    "certifications": ["certifications", "certificates", "licenses", "courses",
                       "professional development"],
    "awards": ["awards", "achievements", "honors", "honours", "recognition"],
    "languages": ["languages", "language proficiency"],
}

BULLET_PREFIX = re.compile(r"^\s*[•●▪‣⁃◦‧∙·\-–—\*o]\s+")
DATE_RANGE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|–|—|to|until)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4}|present|current|now|ongoing)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# text extraction
# --------------------------------------------------------------------------- #
class ResumeReadError(ValueError):
    """A problem with the uploaded file that the user can act on."""


def sniff_kind(path: Path) -> str:
    """Identify the real file type from its first bytes.

    Extensions lie constantly on resumes -- people rename a .doc to .docx, or
    export a .pages and call it a PDF. Trusting the name produces a stack trace
    from deep inside a parser; reading four bytes produces a useful sentence.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return "unknown"

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"          # .docx, .odt, .pages are all zip containers
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"          # legacy .doc / .xls binary format
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return "text"


def extract_text(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    kind = sniff_kind(path)

    if kind == "ole":
        raise ResumeReadError(
            "This is a legacy Word .doc file, which cannot be read reliably. "
            "Open it in Word or Google Docs and save as .docx or PDF, then "
            "upload that."
        )
    if kind == "rtf":
        raise ResumeReadError(
            "This is an RTF file. Save it as .docx or PDF and upload that."
        )

    if kind == "pdf" or (suffix == ".pdf" and kind != "zip"):
        return _extract_pdf(path)
    if kind == "zip" or suffix in {".docx", ".doc"}:
        return _extract_docx(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pdfplumber is required to read PDF resumes") from exc

    pages: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
    except ResumeReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface something a human can act on
        raise ResumeReadError(
            "This PDF could not be opened — it may be corrupted, password "
            "protected, or not really a PDF. Try re-exporting it, or upload a "
            "DOCX instead."
        ) from exc
    return "\n".join(pages)


def _extract_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx is required to read DOCX resumes") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ResumeReadError(
            "This Word file could not be opened. If it came from Pages, "
            "Google Docs or an older Word version, export it as .docx or PDF "
            "and upload that."
        ) from exc
    parts: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        parts.append(f"• {text}" if "list" in style else text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #
def _canonical_section(line: str) -> str | None:
    """Return the section key if this line looks like a section header."""
    stripped = line.strip().strip(":").strip()
    if not stripped or len(stripped) > 45:
        return None
    if len(stripped.split()) > 4:
        return None
    # headers are usually title case, ALL CAPS, or short + underlined
    lowered = re.sub(r"[^a-z& ]", "", stripped.lower()).strip()
    lowered = re.sub(r"\s+", " ", lowered)
    if not lowered:
        return None

    for key, names in SECTION_PATTERNS.items():
        if lowered in names:
            return key

    # Real resumes decorate their headings: "Certifications & Recognition",
    # "Work Experience & Achievements". Accept a known heading plus a trailing
    # flourish, longest name first so "technical skills" beats "skills".
    for key, names in sorted(
        SECTION_PATTERNS.items(),
        key=lambda kv: -max(len(n) for n in kv[1]),
    ):
        for name in sorted(names, key=len, reverse=True):
            if len(name) < 6:
                continue
            if lowered.startswith(name) and re.match(r"^[\s&]", lowered[len(name):] or " "):
                return key
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        key = _canonical_section(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _parse_contact(text: str, header_lines: list[str]) -> dict[str, str]:
    head = "\n".join(header_lines[:12]) or text[:800]

    email_match = EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else ""

    phone = ""
    for candidate in PHONE_RE.findall(head.replace(email, " ")):
        digits = re.sub(r"\D", "", candidate)
        if 9 <= len(digits) <= 15:
            phone = candidate.strip()
            break

    urls = URL_RE.findall(text[:2500])
    linkedin = next((u for u in urls if "linkedin." in u.lower()), "")
    github = next((u for u in urls if "github." in u.lower()), "")
    portfolio = next(
        (u for u in urls if not any(s in u.lower() for s in ("linkedin.", "github.", "mailto"))),
        "",
    )

    name = ""
    for line in header_lines[:6]:
        candidate = line.strip()
        if not candidate or EMAIL_RE.search(candidate) or URL_RE.search(candidate):
            continue
        if any(ch.isdigit() for ch in candidate):
            continue
        words = candidate.split()
        if 1 < len(words) <= 5 and len(candidate) < 60:
            name = candidate.title() if candidate.isupper() else candidate
            break

    # Contact lines are usually one row of pipe- or bullet-separated fields
    # ("Ahmedabad, Gujarat, India | +91 ... | me@example.com"), so test each
    # field rather than requiring the whole line to be a location.
    location = ""
    loc_pattern = re.compile(
        r"^([A-Z][a-zA-Z.\- ]{2,30},\s*[A-Z][a-zA-Z.\- ]{1,30}(?:,\s*[A-Z][a-zA-Z.\- ]{1,30})?)$"
    )
    for line in header_lines[:10]:
        for part in re.split(r"[|•·]|\s{3,}", line):
            part = part.strip(" ,;")
            if not part or EMAIL_RE.search(part) or URL_RE.search(part):
                continue
            if any(ch.isdigit() for ch in part):
                continue
            match = loc_pattern.match(part)
            if match:
                location = match.group(1)
                break
        if location:
            break

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "location": location,
        "linkedin": linkedin,
        "github": github,
        "portfolio": portfolio,
    }


def looks_like_job_title(text: str) -> bool:
    words = re.findall(r"[a-z\-]+", (text or "").lower())
    return any(word in TITLE_WORDS for word in words)


def looks_like_company(text: str) -> bool:
    return bool(COMPANY_MARKERS.search(text or ""))


def _split_title_location(text: str) -> tuple[str, str]:
    """'Front-End Developer Ahmedabad, India' -> role, location.

    Tried at every word boundary rather than by one regex, because a single
    pattern gets it wrong from both ends: non-greedy yields
    ("Front-End", "Developer Ahmedabad, India") and greedy yields
    ("Senior Engineer San", "Francisco, CA"). The reliable signal is that the
    role half ends on a role word.
    """
    text = text.strip()
    for sep in (" | ", " — ", " – ", " · "):
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip(), right.strip()

    words = text.split()
    fallback: tuple[str, str] | None = None
    # Longest possible title first, so the location stays as short as it can be.
    for cut in range(len(words) - 1, 0, -1):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        if not LOCATION_TAIL_RE.match(tail):
            continue
        if fallback is None:
            fallback = (head, tail)
        last_word = re.sub(r"[^a-z\-]", "", words[cut - 1].lower())
        if last_word in TITLE_WORDS:
            return head, tail

    return fallback if fallback else (text, "")


def _merge_wrapped(chunks: list[tuple[bool, str]]) -> list[str]:
    """Rejoin bullet text that the PDF broke across visual lines.

    A PDF has no idea what a sentence is -- it emits one line per rendered row.
    Without this, a single achievement becomes four half-sentences, and the
    tailored resume goes out with fragments like "Blade, serving 10K+ users".
    """
    uses_markers = any(had_marker for had_marker, _ in chunks)
    merged: list[str] = []

    for had_marker, text in chunks:
        text = text.strip()
        if not text:
            continue

        if not merged:
            merged.append(text)
            continue

        if uses_markers:
            # Every real bullet in this role is marked, so an unmarked line is
            # always the tail of the previous one.
            continuation = not had_marker
        else:
            previous = merged[-1]
            continuation = (
                not previous.endswith((".", "!", "?", ":", ";"))
                or text[:1].islower()
            )

        if continuation:
            merged[-1] = previous_join(merged[-1], text)
        else:
            merged.append(text)

    return [b for b in merged if len(b) > 2]


def previous_join(head: str, tail: str) -> str:
    if head.endswith("-"):          # hyphenated word split across lines
        return head[:-1] + tail
    return head.rstrip() + " " + tail.lstrip()


def _parse_experience(lines: list[str]) -> list[dict]:
    """Group experience lines into roles. A role starts on a line with dates."""
    roles: list[dict] = []
    current: dict | None = None
    chunks: list[tuple[bool, str]] = []
    awaiting_title = False

    def close() -> None:
        nonlocal current, chunks
        if current is not None:
            current["bullets"] = _merge_wrapped(chunks)
            roles.append(current)
        current, chunks = None, []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        is_bullet = bool(BULLET_PREFIX.match(line))
        date_match = DATE_RANGE_RE.search(stripped)

        if date_match and not is_bullet:
            close()
            heading = DATE_RANGE_RE.sub("", stripped).strip(" |,·–—-\t")
            title, company = _split_title_company(heading)
            current = {
                "title": title,
                "company": company,
                "start": date_match.group(1).strip(),
                "end": date_match.group(2).strip(),
                "location": "",
                "bullets": [],
            }
            chunks = []
            # Many resumes print the employer on the dated line and the role on
            # the line below ("TOPS Infosolutions ... May 2025 - Present" /
            # "Front-End Developer  Ahmedabad, India"). If what we just captured
            # is clearly not a job title, expect the title next.
            awaiting_title = not company and not looks_like_job_title(title)
            continue

        if current is None:
            if len(stripped) < 120 and not is_bullet:
                title, company = _split_title_company(stripped)
                current = {
                    "title": title, "company": company, "start": "", "end": "",
                    "location": "", "bullets": [],
                }
                chunks = []
            continue

        if awaiting_title and not is_bullet and len(stripped) < 120:
            awaiting_title = False
            candidate, location = _split_title_location(stripped)
            if looks_like_job_title(candidate) or looks_like_company(current["title"]):
                current["company"] = current["title"]
                current["title"] = candidate
                current["location"] = location
                continue

        awaiting_title = False
        text = BULLET_PREFIX.sub("", line).strip()
        if not text:
            continue
        if is_bullet or len(text) > 60 or chunks:
            chunks.append((is_bullet, text))
        elif not current["company"]:
            current["company"] = text

    close()
    return [r for r in roles if r.get("title") or r.get("company") or r.get("bullets")]


def _split_title_company(heading: str) -> tuple[str, str]:
    for sep in (" | ", " – ", " — ", " - ", " at ", ", ", " @ "):
        if sep in heading:
            left, right = heading.split(sep, 1)
            return left.strip(), right.strip()
    return heading.strip(), ""


def _parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for line in lines:
        text = BULLET_PREFIX.sub("", line).strip()
        if len(text) < 4:
            continue
        year_match = re.search(r"(19|20)\d{2}", text)
        entries.append({
            "text": text,
            "year": year_match.group(0) if year_match else "",
        })
    return entries[:8]


def _estimate_years(roles: list[dict], text: str) -> float:
    """Total career length from the earliest start year to the latest end year."""
    years: list[int] = []
    for role in roles:
        for field in ("start", "end"):
            match = re.search(r"(19|20)\d{2}", role.get(field, "") or "")
            if match:
                years.append(int(match.group(0)))
        if re.search(r"present|current|now|ongoing", role.get("end", ""), re.IGNORECASE):
            years.append(date.today().year)
    if len(years) >= 2:
        span = max(years) - min(years)
        if 0 < span <= 45:
            return float(span)

    stated = re.search(r"(\d{1,2})\+?\s*years?\s+of\s+(?:professional\s+)?experience",
                       text, re.IGNORECASE)
    if stated:
        return float(stated.group(1))
    return 0.0


def parse_resume(text: str) -> dict:
    """Rule-based structuring of resume text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sections = split_sections(text)

    contact = _parse_contact(text, sections.get("header", []))
    experience = _parse_experience(sections.get("experience", []))

    summary_lines = sections.get("summary", [])
    summary = normalize(" ".join(BULLET_PREFIX.sub("", ln).strip() for ln in summary_lines))[:1200]
    if not summary and sections.get("header"):
        tail = [ln for ln in sections["header"] if len(ln.strip()) > 90]
        summary = normalize(" ".join(tail))[:1200]

    skills_text = "\n".join(sections.get("skills", []))
    skills = extract_skills(skills_text) if skills_text else []
    # anything mentioned anywhere in the resume also counts as a skill
    for skill in extract_skills(text):
        if skill not in skills:
            skills.append(skill)

    seniority, seniority_rank = detect_seniority(
        " ".join([contact.get("name", "")] + [r.get("title", "") for r in experience[:2]])
        or text[:600]
    )

    return {
        "contact": contact,
        "summary": summary,
        "skills": skills,
        "experience": experience,
        "education": _parse_education(sections.get("education", [])),
        "projects": [
            BULLET_PREFIX.sub("", ln).strip() for ln in sections.get("projects", [])[:12]
        ],
        "certifications": [
            BULLET_PREFIX.sub("", ln).strip() for ln in sections.get("certifications", [])[:12]
        ],
        "languages": [
            BULLET_PREFIX.sub("", ln).strip() for ln in sections.get("languages", [])[:8]
        ],
        "seniority": seniority,
        "seniority_rank": seniority_rank,
        "years_experience": _estimate_years(experience, text),
        "sections_found": sorted(k for k in sections if k != "header" and sections[k]),
    }


def ingest(path: str | Path) -> tuple[str, dict]:
    """Read a resume file and return (raw_text, parsed_structure)."""
    raw = extract_text(path)
    if len(raw.strip()) < 50:
        raise ResumeReadError(
            "No readable text was found in this file. If it is a scanned or "
            "image-only PDF, the text is a picture — export a text-based PDF "
            "from your original document, or upload a DOCX instead."
        )
    return raw, parse_resume(raw)
