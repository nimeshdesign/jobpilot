"""Map an arbitrary application-form field to a value from your profile.

Job boards all invent their own field names, so matching is done on a normalised
blob of every label-ish attribute we can find (label text, aria-label, name, id,
placeholder). Patterns are ordered: the first match wins, so put specific
patterns above generic ones.
"""
from __future__ import annotations

import re

# (regex, profile key). Order matters.
FIELD_PATTERNS: list[tuple[str, str]] = [
    # --- name ---
    (r"\b(first|given|fore)\s*name\b|\bfname\b", "first_name"),
    (r"\b(last|family|sur)\s*name\b|\blname\b|\bsurname\b", "last_name"),
    (r"\bpreferred\s*name\b|\bnick\s*name\b", "first_name"),
    (r"\bmiddle\s*name\b", "middle_name"),
    (r"\b(full|legal|your)\s*name\b|^name$|\bcandidate name\b|\bapplicant name\b", "full_name"),

    # --- contact ---
    (r"\be-?mail\b", "email"),
    # The country selector attached to a phone widget must be matched before the
    # generic phone rule, or it gets the phone number typed into it.
    (r"(phone|dial(l?ing)?|calling)[\s-]*(country|code)|country[\s-]*(code|calling)", "country"),
    (r"\b(phone|mobile|cell|telephone|contact number)\b", "phone"),

    # --- links ---
    (r"linked\s*-?\s*in", "linkedin"),
    (r"\bgit\s*hub\b", "github"),
    (r"\bportfolio\b|\bpersonal (web)?site\b|\bwebsite\b|\bblog\b|\bweb ?site url\b", "portfolio"),
    (r"\bother (website|url|link)\b", "portfolio"),

    # --- work authorisation / visa ---
    # These come BEFORE the location rules on purpose. Sponsorship questions are
    # routinely phrased "...require sponsorship for a visa to remain in your
    # current location", and a location rule matching first would type a city
    # into a yes/no immigration question.
    (r"require.*(sponsor|visa)|need.*(sponsor|visa)|sponsorship.*(require|need)", "needs_sponsorship_yn"),
    (r"(will|would) you (now or in the future )?require", "needs_sponsorship_yn"),
    (r"\bsponsorship\b", "needs_sponsorship_yn"),
    (r"legally (authori[sz]ed|entitled|eligible) to work|authori[sz]ed to work|right to work|"
     r"work authori[sz]ation|eligible to work|permission to work", "work_authorized_yn"),
    (r"\bvisa\b.*\bstatus\b|\bimmigration status\b|\bcurrent visa\b|\bwork permit\b", "visa_status"),
    (r"\bcitizenship\b|\bare you a citizen\b|\bnationality\b", "nationality"),
    (r"\b(willing|open) to relocat|\brelocation\b", "relocate_yn"),
    (r"\bsecurity clearance\b", "clearance"),

    # --- location ---
    (r"\b(current )?(city|town)\b|\bcity of residence\b", "city"),
    (r"\b(country|nation)\b(?!.*citizen)", "country"),
    (r"\b(current )?(location|based|residence|where.*located|where do you live)\b", "location"),
    (r"\b(street )?address\b|\baddress line\b", "location"),
    (r"\b(post(al)? ?code|zip)\b", "postal_code"),
    (r"\btime ?zone\b", "timezone"),

    # --- role logistics ---
    (r"\b(notice period|availability|available (to )?(start|from)|start date|earliest start)\b",
     "notice_period"),
    (r"\b(desired|expected|target)?\s*(salary|compensation|pay|rate)\b|\bsalary expectation",
     "desired_salary"),
    (r"\byears? of (relevant |professional |total )?experience\b|\bhow many years\b|"
     r"\bexperience \(years\)", "years_experience"),
    (r"\bpronoun", "pronouns"),
    (r"\b(how did you hear|referral source|where did you (hear|find)|source)\b", "how_heard"),
    (r"\breferred by\b|\breferral name\b", "referrer"),

    # --- long text ---
    (r"\bcover ?letter\b|\bwhy (do you want|are you interested)\b|"
     r"\btell us (about yourself|why)\b|\bmotivation\b|\bwhy this (role|company)\b|"
     r"\badditional information\b|\banything else\b|\bmessage\b|\bnote to\b", "cover_letter"),
    (r"\bsummary\b|\babout you\b|\bbio\b", "summary"),

    # --- files ---
    (r"\bresume\b|\bcv\b|\bcurriculum vitae\b", "resume_file"),
    (r"\bcover ?letter\b.*(file|upload|attach)|attach.*cover", "cover_letter_file"),

    # --- demographic / EEO ---
    (r"\bgender\b|\bsex\b", "eeo_decline"),
    (r"\brace\b|\bethnic", "eeo_decline"),
    (r"\bveteran\b|\bmilitary\b", "eeo_decline"),
    (r"\bdisabilit", "eeo_decline"),
    (r"\bhispanic\b|\blatino\b", "eeo_decline"),
    (r"\bsexual orientation\b|\btransgender\b", "eeo_decline"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), key) for pattern, key in FIELD_PATTERNS]

# Fields we must never touch.
SKIP_PATTERNS = re.compile(
    r"\b(password|captcha|search|newsletter|subscribe|promo ?code|coupon|"
    r"credit card|card number|cvv|ssn|social security|bank|iban|routing)\b",
    re.IGNORECASE,
)

# Consent boxes that are required to submit but harmless to accept.
CONSENT_PATTERNS = re.compile(
    r"\b(privacy policy|terms|consent|gdpr|data processing|acknowledge|agree|certify|"
    r"confirm that|i have read|store my (data|information)|retain my)\b",
    re.IGNORECASE,
)


def normalize_label(*parts: str) -> str:
    blob = " ".join(p for p in parts if p)
    blob = re.sub(r"[_\-\[\]{}.]+", " ", blob)
    blob = re.sub(r"([a-z])([A-Z])", r"\1 \2", blob)  # camelCase -> camel Case
    blob = re.sub(r"\s+", " ", blob).strip().lower()
    return blob.rstrip("*: ").strip()


def classify(label_blob: str) -> str | None:
    """Return a profile key for this field, or None if we should leave it alone."""
    if not label_blob or SKIP_PATTERNS.search(label_blob):
        return None
    for pattern, key in _COMPILED:
        if pattern.search(label_blob):
            return key
    return None


def build_values(profile, resume, application, tailored, job=None) -> dict[str, str]:
    """Flatten profile + generated documents into the value table used for filling.

    `job` is what makes the salary answer correct: the expected figure for a
    Berlin role and a Toronto role are different numbers in different
    currencies, so it is resolved per posting rather than stored as one string.
    """
    contact = (resume.parsed or {}).get("contact", {}) if resume else {}
    full_name = contact.get("name") or profile.full_name or ""
    name_parts = full_name.split()
    first = name_parts[0] if name_parts else ""
    last = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    location = ", ".join(filter(None, [profile.city, profile.country])) or contact.get(
        "location", ""
    )

    parsed = (resume.parsed or {}) if resume else {}
    years = profile.years_experience or parsed.get("years_experience", 0) or 0

    from ..salary import expected as expected_salary

    job_country = ""
    if job is not None:
        job_country = job.country or ("Remote / Global" if job.is_remote else "")
    salary = expected_salary(
        job_country,
        float(years),
        parsed.get("seniority", ""),
        overrides=getattr(profile, "salary_expectations", None) or {},
        fallback=profile.desired_salary or "",
    ) or profile.desired_salary

    return {
        "first_name": first,
        "last_name": last,
        "middle_name": "",
        "full_name": full_name,
        "email": profile.email or contact.get("email", ""),
        "phone": profile.phone or contact.get("phone", ""),
        "city": profile.city,
        "country": profile.country,
        "location": location,
        "postal_code": "",
        "timezone": "",
        "linkedin": profile.linkedin or contact.get("linkedin", ""),
        "github": profile.github or contact.get("github", ""),
        "portfolio": profile.portfolio or contact.get("portfolio", ""),
        "visa_status": profile.current_visa_status,
        "nationality": profile.country,
        "needs_sponsorship_yn": "yes" if profile.needs_sponsorship else "no",
        "work_authorized_yn": "no" if profile.needs_sponsorship else "yes",
        "relocate_yn": "yes" if profile.willing_to_relocate else "no",
        "clearance": "no",
        "notice_period": profile.notice_period,
        "desired_salary": salary,
        "years_experience": str(int(years)) if years else "",
        "pronouns": profile.pronouns,
        "how_heard": "Company website",
        "referrer": "",
        "cover_letter": (application.cover_letter if application else "") or "",
        "summary": (tailored.summary if tailored else "") or "",
        "resume_file": (application.resume_pdf or application.resume_docx) if application else "",
        "cover_letter_file": "",
        "eeo_decline": "decline",
    }


# Yes/No answers get matched against option text with these synonyms.
YES_WORDS = ("yes", "y", "true", "i am", "i do", "authorized", "authorised", "eligible")
NO_WORDS = ("no", "n", "false", "i am not", "i do not", "not authorized", "not authorised")
DECLINE_WORDS = (
    "decline", "prefer not", "do not wish", "don't wish", "not specified", "i don't want",
    "i do not want", "don't want to answer", "do not want to answer", "not to answer",
    "prefer not to say", "rather not say", "choose not", "opt out", "self identify",
    "not disclose", "undisclosed", "wish to not answer",
)


def option_matches(option_text: str, desired: str) -> int:
    """Score how well a select/radio option matches the desired value. Higher is better."""
    option = (option_text or "").strip().lower()
    want = (desired or "").strip().lower()
    if not option or not want:
        return 0

    if option == want:
        return 100

    if want in ("yes", "no"):
        words = YES_WORDS if want == "yes" else NO_WORDS
        opposite = NO_WORDS if want == "yes" else YES_WORDS
        if any(option == w for w in words):
            return 95
        if any(option.startswith(w + " ") or option.startswith(w + ",") for w in words):
            return 85
        if any(w in option for w in opposite):
            return 0
        if any(w in option for w in words):
            return 60
        return 0

    if want == "decline":
        if any(w in option for w in DECLINE_WORDS):
            return 90
        return 0

    # Word boundaries matter more than they look. Picking a country by naive
    # substring match sends "India" to "British Indian Ocean Territory", because
    # "india" really is inside "indian". Whole-word hits must outrank that.
    boundary = re.compile(r"(?<![a-z0-9])" + re.escape(want) + r"(?![a-z0-9])")
    if boundary.match(option):
        return 92          # option begins with the value: "India +91"
    if boundary.search(option):
        return 78          # value appears as its own word somewhere
    if want in option or option in want:
        return 52          # bare substring: weakest evidence, still a candidate

    want_tokens = set(re.findall(r"[a-z0-9]+", want))
    option_tokens = set(re.findall(r"[a-z0-9]+", option))
    if want_tokens and option_tokens:
        overlap = len(want_tokens & option_tokens) / len(want_tokens)
        if overlap >= 0.6:
            return int(40 + overlap * 25)
    return 0
