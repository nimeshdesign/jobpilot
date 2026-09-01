"""Unit tests for the parts that decide what gets applied to.

No network, no browser, no database. Run with:
    .venv\\Scripts\\python.exe tests\\test_units.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services import salary, visa                      # noqa: E402
from app.services.ats import fields as F                   # noqa: E402
from app.services.ats.autofill import (                    # noqa: E402
    detect_ats, is_aggregator, resolve_apply_url,
)
from app.services.resume_parser import (                   # noqa: E402
    _split_title_location, parse_resume,
)
from app.services.skills import extract_skills, job_family  # noqa: E402
from app.services.tailor import clean_title                 # noqa: E402
from app.services.textutil import html_to_text              # noqa: E402

FAIL = 0


def check(label, cond, detail=""):
    global FAIL
    if not cond:
        FAIL += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  --> {detail}" if detail else ""))


def section(name):
    print(f"\n=== {name} ===")


# --------------------------------------------------------------------------- #
section("resume parsing")
RESUME = """Nimesh Example
Ahmedabad, Gujarat, India | +91 90000 00000 | test@example.com
linkedin.com/in/example

Professional Summary
Senior Front-End Developer with 5+ years building responsive web applications.

Technical Skills
Languages: JavaScript, TypeScript, HTML5, CSS3
Frameworks: React.js, Vue.js, Angular, Nuxt.js, Tailwind CSS
Concepts: Design Systems, Responsive / Mobile-First Design, REST API Integration

Experience
Acme Solutions Pvt. Ltd. May 2025 - Present
Front-End Developer Ahmedabad, India
- Delivered 15+ front-end projects using React.js, Nuxt.js
and Laravel Blade, serving 10K+ users.
- Architected reusable component libraries with Tailwind CSS.

Education
Bachelor of Computer Applications (BCA) 2019

Certifications & Recognition
Certificate of Appreciation - Acme (2025)
"""
parsed = parse_resume(RESUME)
contact = parsed["contact"]
check("name", contact["name"] == "Nimesh Example", contact["name"])
check("email", contact["email"] == "test@example.com", contact["email"])
check("phone found", bool(contact["phone"]), contact["phone"])
check("location from a pipe-separated line",
      contact["location"] == "Ahmedabad, Gujarat, India", contact["location"])
check("bare linkedin URL found", "linkedin" in contact["linkedin"], contact["linkedin"])

role = parsed["experience"][0]
check("company from the dated line", role["company"] == "Acme Solutions Pvt. Ltd.",
      role["company"])
check("title from the line below", role["title"] == "Front-End Developer", role["title"])
check("role location split out", role["location"] == "Ahmedabad, India", role["location"])
check("wrapped bullets rejoined",
      any("Laravel Blade" in b and "Delivered" in b for b in role["bullets"]),
      str(role["bullets"])[:90])
check("certifications is its own section", parsed["certifications"], str(parsed["certifications"]))

check("hyphen/space skill variants",
      "Responsive Design" in parsed["skills"], str(parsed["skills"])[:80])
for want in ("React", "Vue.js", "TypeScript", "Tailwind CSS", "Design Systems"):
    check(f"skill {want}", want in parsed["skills"])

section("title/location splitting")
for text, want in [
    ("Front-End Developer Ahmedabad, India", ("Front-End Developer", "Ahmedabad, India")),
    ("Senior Engineer San Francisco, CA", ("Senior Engineer", "San Francisco, CA")),
    ("Data Analyst | Remote", ("Data Analyst", "Remote")),
]:
    check(f"{text!r}", _split_title_location(text) == want, str(_split_title_location(text)))

# --------------------------------------------------------------------------- #
section("skill extraction precision")
check("bare 'r' is not the R language",
      "R" not in extract_skills("wir sind der beste arbeitgeber der welt"))
check("bare 'go' is not Golang", "Go" not in extract_skills("we go to market fast"))
check("golang is", "Go" in extract_skills("strong golang experience"))
check("java is not javascript",
      extract_skills("expert in javascript") == ["JavaScript"],
      str(extract_skills("expert in javascript")))

section("job families")
for title, want in [
    ("Senior Frontend Engineer (Vue)", "engineering"),
    ("Senior Commercial Legal Counsel", "legal"),
    ("Senior FP&A Analyst", "finance"),
    ("Senior Benefits Analyst", "people"),
    ("Senior Product Designer", "design"),
    ("Account Executive DACH", "sales"),
]:
    check(f"{title[:34]!r}", job_family(title) == want, job_family(title))

# --------------------------------------------------------------------------- #
section("visa detection")
for text, want in [
    ("We can sponsor visas and help you relocate to the UK", "yes"),
    ("Relocation package with visa support for those who need it", "yes"),
    ("NO VISA SUPPORT AND RELOCATION SUPPORT", "no"),
    ("We are unable to offer visa sponsorship for this role", "no"),
    ("This role does not support Visa sponsorship", "no"),
    ("We hire globally through an employer of record", "global"),
    ("Work from anywhere in the world", "global"),
    ("We build great software. Join our team.", "unknown"),
]:
    got, _, _ = visa.analyze(text, "Engineer")
    check(f"{text[:46]!r}", got == want, f"{got} (want {want})")

check("double-encoded HTML decoded",
      "NO VISA SUPPORT" in html_to_text("&lt;p&gt;NO VISA SUPPORT&lt;/p&gt;"))

# --------------------------------------------------------------------------- #
section("apply-URL resolution")
for url, want in [
    ("https://careers.airbnb.com/positions/8138002?gh_jid=8138002",
     "https://boards.greenhouse.io/embed/job_app?token=8138002"),
    ("https://job-boards.greenhouse.io/gitlab/jobs/1",
     "https://job-boards.greenhouse.io/gitlab/jobs/1"),
    ("https://jobs.lever.co/aircall/abc", "https://jobs.lever.co/aircall/abc/apply"),
    ("https://jobs.ashbyhq.com/acme/x", "https://jobs.ashbyhq.com/acme/x/application"),
]:
    got, _ = resolve_apply_url(url)
    check(f"{url[:48]!r}", got == want, got)

check("aggregator listing detected", is_aggregator("https://jobicy.com/jobs/1"))
check("real ATS not flagged as aggregator",
      not is_aggregator("https://job-boards.greenhouse.io/x/jobs/1"))
check("greenhouse detected", detect_ats("https://boards.greenhouse.io/a/jobs/1") == "greenhouse")

# --------------------------------------------------------------------------- #
section("form field mapping")
for label, want in [
    ("First Name *", "first_name"),
    ("Email Address", "email"),
    ("Phone country code", "country"),
    ("Will you now or in the future require sponsorship for a visa to remain "
     "in your current location?", "needs_sponsorship_yn"),
    ("Are you legally authorized to work in the United States?", "work_authorized_yn"),
    ("What is your current country of residence?", "country"),
    ("Gender", "eeo_decline"),
    ("Attach your resume/CV", "resume_file"),
]:
    got = F.classify(F.normalize_label(label))
    check(f"{label[:44]!r}", got == want, str(got))

check("password never touched", F.classify(F.normalize_label("Password")) is None)
check("India beats British Indian Ocean Territory",
      F.option_matches("India +91", "India") > F.option_matches(
          "British Indian Ocean Territory +246", "India"))
check("opposite answer rejected", F.option_matches("No, I am not authorized", "yes") == 0)
check("decline synonyms", F.option_matches("I do not want to answer", "decline") >= 90)

# --------------------------------------------------------------------------- #
section("job title cleaning")
for raw, want in [
    ("Senior Frontend Software Engineer, Home Experience", "Senior Frontend Software Engineer"),
    ("Backend Software Engineer Senior - Vaga afirmativa para mulheres",
     "Backend Software Engineer Senior"),
    ("Software Engineer - Backend", "Software Engineer - Backend"),
    ("Senior Software Engineer - Remote", "Senior Software Engineer"),
]:
    check(f"{raw[:46]!r}", clean_title(raw) == want, clean_title(raw))

# --------------------------------------------------------------------------- #
section("salary by country")
check("senior band from 5 years", salary.band_for(5, "mid") == "senior",
      salary.band_for(5, "mid"))
check("resume seniority wins when higher", salary.band_for(3, "senior") == "senior")
de = salary.expected("Germany", 5, "mid")
uk = salary.expected("United Kingdom", 5, "mid")
check("Germany quoted in euros", de.startswith("€"), de)
check("UK quoted in pounds", uk.startswith("£"), uk)
check("different countries differ", de != uk, f"{de} vs {uk}")
check("override wins",
      salary.expected("Germany", 5, "mid", {"Germany": "€99,999"}) == "€99,999")
check("unknown country falls back", bool(salary.expected("Atlantis", 5, "mid")))

print(f"\nFAILURES: {FAIL}")
sys.exit(1 if FAIL else 0)
