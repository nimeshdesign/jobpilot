"""Country-aware salary expectations.

One "desired salary" figure cannot serve applications to Berlin, London,
Toronto and a remote role at once -- the same job is priced in different
currencies and on different scales. This resolves an expectation from the
country of the posting, scaled by seniority.

The built-in numbers are ROUGH STARTING ESTIMATES for software / front-end
roles, meant to be edited, not trusted. They are annual gross figures in local
currency for a mid-level engineer (roughly 3-6 years). Override any of them per
country in Settings -- your own research on levels.fyi, Glassdoor or a local
salary survey will always beat a table baked into an app.
"""
from __future__ import annotations

# country -> (low, high, currency symbol, thousands style)
# "mid" band; other bands are derived with SENIORITY_MULTIPLIER below.
BASE_RANGES: dict[str, tuple[int, int, str]] = {
    # --- Europe ---
    "Germany": (62_000, 78_000, "€"),
    "Netherlands": (60_000, 75_000, "€"),
    "Ireland": (60_000, 78_000, "€"),
    "Austria": (55_000, 68_000, "€"),
    "Belgium": (55_000, 68_000, "€"),
    "France": (48_000, 62_000, "€"),
    "Spain": (40_000, 55_000, "€"),
    "Portugal": (35_000, 48_000, "€"),
    "Italy": (38_000, 50_000, "€"),
    "Poland": (150_000, 210_000, "PLN "),
    "Sweden": (550_000, 700_000, "SEK "),
    "Denmark": (500_000, 650_000, "DKK "),
    "Norway": (650_000, 800_000, "NOK "),
    "Switzerland": (110_000, 135_000, "CHF "),
    "Finland": (55_000, 68_000, "€"),
    "Czechia": (1_200_000, 1_700_000, "CZK "),

    # --- UK & Ireland ---
    "United Kingdom": (55_000, 70_000, "£"),

    # --- North America ---
    "United States": (110_000, 140_000, "$"),
    "Canada": (90_000, 115_000, "CA$"),

    # --- APAC ---
    "Australia": (110_000, 135_000, "AU$"),
    "New Zealand": (100_000, 125_000, "NZ$"),
    "Singapore": (90_000, 120_000, "SGD "),
    "Japan": (7_000_000, 9_500_000, "¥"),
    "United Arab Emirates": (240_000, 330_000, "AED "),

    # --- South Asia ---
    "India": (1_800_000, 2_800_000, "₹"),

    # --- fully remote / employer of record, usually quoted in USD ---
    "Remote / Global": (55_000, 80_000, "$"),
}

DEFAULT_COUNTRY = "Remote / Global"

# Applied to the mid-level base range.
SENIORITY_MULTIPLIER: dict[str, float] = {
    "intern": 0.35,
    "junior": 0.68,
    "mid": 1.0,
    "senior": 1.32,
    "lead": 1.65,
}


def band_for(years: float, resume_seniority: str = "") -> str:
    """Seniority band from years of experience, respecting the resume's own claim."""
    if years >= 9:
        by_years = "lead"
    elif years >= 5:
        by_years = "senior"
    elif years >= 2.5:
        by_years = "mid"
    elif years >= 1:
        by_years = "junior"
    else:
        by_years = "intern"

    order = ["intern", "junior", "mid", "senior", "lead"]
    stated = (resume_seniority or "").lower()
    if stated in order:
        # Trust whichever is higher: a "Senior" title with 5 years is credible,
        # and undercutting yourself in the salary box costs real money.
        return max(by_years, stated, key=order.index)
    return by_years


def _round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


def format_range(low: int, high: int, currency: str) -> str:
    step = 100_000 if max(low, high) > 1_000_000 else 1_000
    return f"{currency}{_round_to(low, step):,} - {currency}{_round_to(high, step):,}"


def expected(
    country: str,
    years: float,
    resume_seniority: str = "",
    overrides: dict | None = None,
    fallback: str = "",
) -> str:
    """Salary expectation for one posting's country. '' if nothing is known."""
    overrides = overrides or {}
    country = (country or "").strip() or DEFAULT_COUNTRY

    # An explicit override always wins, matched case-insensitively.
    for key, value in overrides.items():
        if key.strip().lower() == country.lower() and str(value).strip():
            return str(value).strip()

    entry = BASE_RANGES.get(country)
    if entry is None:
        for key, value in overrides.items():
            if key.strip().lower() in ("default", "other", "*") and str(value).strip():
                return str(value).strip()
        entry = BASE_RANGES.get(DEFAULT_COUNTRY) if not fallback else None
        if entry is None:
            return fallback

    low, high, currency = entry
    multiplier = SENIORITY_MULTIPLIER.get(band_for(years, resume_seniority), 1.0)
    return format_range(low * multiplier, high * multiplier, currency)


def preview(years: float, resume_seniority: str = "", overrides: dict | None = None) -> list[dict]:
    """Every country's resolved figure, for showing in Settings."""
    return [
        {
            "country": country,
            "salary": expected(country, years, resume_seniority, overrides),
            "overridden": any(
                k.strip().lower() == country.lower() and str(v).strip()
                for k, v in (overrides or {}).items()
            ),
        }
        for country in BASE_RANGES
    ]
