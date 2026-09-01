"""Shared plumbing for job sources."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..services.textutil import html_to_text, normalize

COUNTRY_HINTS: dict[str, list[str]] = {
    "United States": ["united states", "usa", "u.s.", " us ", "us-", "america", "new york",
                      "san francisco", "california", "texas", "seattle", "boston", "austin"],
    "United Kingdom": ["united kingdom", " uk ", "uk-", "england", "london", "scotland",
                       "manchester", "britain"],
    "Canada": ["canada", "toronto", "vancouver", "montreal", "ontario"],
    "Germany": ["germany", "berlin", "munich", "münchen", "hamburg", "deutschland", "frankfurt"],
    "Netherlands": ["netherlands", "amsterdam", "holland", "utrecht", "rotterdam"],
    "Ireland": ["ireland", "dublin"],
    "Australia": ["australia", "sydney", "melbourne", "brisbane"],
    "New Zealand": ["new zealand", "auckland", "wellington"],
    "Singapore": ["singapore"],
    "India": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune",
              "ahmedabad", "chennai", "gurgaon", "noida"],
    "United Arab Emirates": ["uae", "dubai", "abu dhabi", "united arab emirates"],
    "Spain": ["spain", "madrid", "barcelona"],
    "France": ["france", "paris"],
    "Portugal": ["portugal", "lisbon", "porto"],
    "Poland": ["poland", "warsaw", "krakow", "kraków"],
    "Sweden": ["sweden", "stockholm"],
    "Switzerland": ["switzerland", "zurich", "zürich", "geneva"],
    "Japan": ["japan", "tokyo"],
    "Remote / Global": ["worldwide", "anywhere", "global", "remote"],
}

REMOTE_HINTS = re.compile(
    r"\b(remote|work from home|wfh|distributed|anywhere|telecommute|home[- ]based)\b",
    re.IGNORECASE,
)
ONSITE_HINTS = re.compile(r"\b(on[- ]?site|in[- ]office|hybrid required|must relocate)\b",
                          re.IGNORECASE)


@dataclass
class JobPost:
    """Normalised posting, independent of which board it came from."""

    source: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    country: str = ""
    is_remote: bool = False
    employment_type: str = ""
    salary: str = ""
    url: str = ""
    apply_url: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    # explicit sponsorship flag when the board publishes one (Arbeitnow does)
    visa_flag: bool | None = None

    def finalize(self) -> "JobPost":
        self.title = normalize(self.title)[:390]
        self.company = normalize(self.company)[:290]
        self.location = normalize(self.location)[:290]
        self.description = html_to_text(self.description)
        self.apply_url = self.apply_url or self.url
        if not self.country:
            self.country = guess_country(f"{self.location} {self.title}")
        if not self.is_remote:
            self.is_remote = detect_remote(
                f"{self.title} {self.location} {' '.join(self.tags)}"
            )
        self.tags = [normalize(t)[:60] for t in (self.tags or []) if t][:25]
        return self


def guess_country(text: str) -> str:
    lowered = f" {(text or '').lower()} "
    for country, hints in COUNTRY_HINTS.items():
        if country == "Remote / Global":
            continue
        if any(hint in lowered for hint in hints):
            return country
    if any(hint in lowered for hint in COUNTRY_HINTS["Remote / Global"]):
        return "Remote / Global"
    return ""


def detect_remote(text: str) -> bool:
    text = text or ""
    if ONSITE_HINTS.search(text) and not REMOTE_HINTS.search(text):
        return False
    return bool(REMOTE_HINTS.search(text))


def parse_date(value) -> datetime | None:
    """Best-effort parse of the many date shapes job boards emit."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            return None

    text = str(value).strip()
    if text.isdigit() and len(text) >= 9:
        return parse_date(int(text))

    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S GMT", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            continue
    return None
