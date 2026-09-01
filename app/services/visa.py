"""Visa-sponsorship detection for job postings.

Returns one of four states plus the exact phrases that drove the decision, so
you can sanity-check any call the app makes:

    'yes'      the posting actually offers visa sponsorship or relocation help
    'global'   no sponsorship language, but the employer hires worldwide /
               through an employer of record -- the visa barrier does not apply
               to a remote role, which is a different thing from sponsorship
    'no'       the posting explicitly rules sponsorship out
    'unknown'  the posting says nothing either way (the common case -- roughly
               nine in ten postings never mention it)

Keeping 'global' separate matters. "Work from anywhere" is a payroll statement,
not an immigration one; folding it into 'yes' would fill your sponsorship filter
with roles that cannot actually get you a visa.

Negative signals outrank positive ones: "we do not offer visa sponsorship"
contains the phrase "visa sponsorship", so ordering matters.
"""
from __future__ import annotations

import re

# Phrases that mean sponsorship is NOT available. Checked first.
NEGATIVE_PATTERNS: list[tuple[str, float]] = [
    (r"(?:can|will|do|does)\s*(?:not|n't)\s+(?:be\s+able\s+to\s+)?(?:offer|provide|sponsor|support)[^.]{0,40}(?:visa|sponsorship|work permit)", 3.0),
    (r"(?:no|without|unable to (?:offer|provide))\s+(?:visa\s+)?sponsorship", 3.0),
    (r"not\s+(?:able|in a position)\s+to\s+sponsor", 3.0),
    (r"sponsorship\s+is\s+not\s+(?:available|offered|provided)", 3.0),
    (r"(?:must|should)\s+(?:be|have)\s+(?:legally\s+)?(?:authori[sz]ed|eligible)\s+to\s+work[^.]{0,60}without\s+(?:the\s+need\s+for\s+)?sponsorship", 3.0),
    (r"without\s+(?:current\s+or\s+future\s+)?(?:visa\s+)?sponsorship", 2.5),
    (r"we\s+are\s+(?:currently\s+)?unable\s+to\s+sponsor", 3.0),
    (r"(?:us|u\.s\.)\s+citizens?\s+(?:or|and)\s+(?:permanent residents?|green card)[^.]{0,30}only", 2.0),
    (r"security clearance\s+(?:is\s+)?required", 1.5),
    (r"must\s+(?:currently\s+)?(?:reside|be located|be based)\s+in\s+the\s+(?:us|u\.s\.|united states)\b", 1.0),
    (r"no\s+c2c|no\s+corp\s*to\s*corp", 0.5),
    # "NO VISA SUPPORT AND RELOCATION SUPPORT" -- the positive rules look for
    # "visa support", so the refusal has to be matched explicitly or the ad
    # reads as an offer of exactly what it is denying.
    (r"\bno\s+(?:visa|work\s+permit|immigration)\s+(?:support|sponsorship|assistance)", 3.0),
    (r"\bno\s+relocation\s+(?:support|assistance|package|help)", 1.5),
    (r"\bno\s+(?:visa|sponsorship|relocation)\b", 2.0),
    (r"(?:visa|sponsorship|relocation)\s+(?:is\s+)?not\s+(?:supported|available|offered|provided)", 3.0),
    # German phrasing for "you must already hold the right to work"
    (r"(g[üu]ltige\s+)?arbeitserlaubnis\s+(ist\s+)?(erforderlich|vorausgesetzt|notwendig)", 2.5),
    (r"keine\s+visa[\s-]*unterst[üu]tzung|kein\s+visum[\s-]*sponsoring", 3.0),
    (r"aufenthaltstitel\s+(ist\s+)?erforderlich", 2.5),
    (r"deutschkenntnisse\s+(auf\s+)?(mutter|c1|c2)", 0.8),
]

# Phrases that mean sponsorship IS available.
POSITIVE_PATTERNS: list[tuple[str, float]] = [
    (r"(?:visa|work permit)\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)", 3.0),
    (r"(?:we|company)\s+(?:will\s+|can\s+|do\s+|does\s+|happily\s+)?sponsor(?:s|ship)?\s+(?:your\s+)?(?:visa|work permit|work authorization)", 3.0),
    (r"we\s+(?:offer|provide)\s+(?:visa\s+)?sponsorship", 3.0),
    (r"sponsorship\s+(?:available|offered|provided)", 2.5),
    (r"visa\s+support", 2.5),
    # German / EU phrasing — Arbeitnow and other EU boards post in German
    (r"visum[\s-]*(unterst[üu]tzung|sponsoring)|unterst[üu]tzung\s+beim\s+visum", 2.5),
    (r"blaue\s+karte|blue\s+card\s+eu", 2.5),
    (r"unterst[üu]tzen\s+(wir\s+)?(dich|sie)\s+bei\s+(dem|der)?\s*(visum|umzug|relocation)", 2.5),
    (r"umzugs(unterst[üu]tzung|hilfe|pauschale)|relocation\s*paket", 1.5),
    (r"internationale\s+bewerber|bewerber\s+aus\s+dem\s+ausland", 1.5),
    (r"fachkr[äa]ftein?wanderung|fachkr[äa]fteeinwanderungsgesetz", 2.0),
    (r"open\s+to\s+sponsoring", 2.5),
    (r"willing\s+to\s+sponsor", 2.5),
    (r"eligible\s+for\s+(?:visa\s+)?sponsorship", 2.0),
    (r"relocation\s+(?:package|support|assistance|bonus)", 1.5),
    (r"we\s+(?:help|assist)\s+with\s+(?:the\s+)?(?:visa|relocation|immigration)", 2.5),
    (r"\bh-?1b\b", 1.5),
    (r"\bh1-?b\s+transfer", 2.0),
    (r"\bo-?1\s+visa", 1.5),
    (r"\btn\s+visa", 1.5),
    (r"blue\s+card", 2.0),
    (r"skilled\s+worker\s+visa", 2.5),
    (r"tier\s*2\s+(?:visa|sponsor)", 2.5),
    (r"certificate\s+of\s+sponsorship", 2.5),
    (r"(?:uk|home office)\s+(?:visa\s+)?sponsor(?:ship)?\s+licen[cs]e", 2.5),
    (r"work\s+(?:permit|authorization)\s+(?:assistance|support|sponsorship)", 2.0),
    (r"immigration\s+(?:support|assistance|lawyer|attorney)", 2.0),
    (r"global\s+mobility\s+(?:team|support|program)", 1.5),
]

# Employer-of-record / hire-anywhere signals. These are NOT sponsorship: they
# mean the company can pay you where you already live. For a remote role that
# removes the visa problem entirely, which is worth surfacing -- but under its
# own label, never as a sponsorship promise.
GLOBAL_HIRE_PATTERNS: list[tuple[str, float]] = [
    (r"\b(?:deel|oyster hr|velocity global|globalization partners|papaya global|remofirst)\b", 2.0),
    (r"employer\s+of\s+record|\beor\b", 2.0),
    (r"(?:hire|employ)\s+(?:from\s+)?anywhere\s+in\s+the\s+world", 2.0),
    (r"hire\s+globally|globally\s+distributed|remote\s+worldwide|worldwide\s+remote", 1.5),
    (r"work\s+from\s+anywhere", 1.5),
    (r"fully\s+(?:remote|distributed)\s+(?:company|team)[^.]{0,40}(?:globally|worldwide|any country)",
     1.5),
    (r"anywhere\s+in\s+(?:europe|emea|apac|latam|the\s+world)", 1.5),
    (r"no\s+matter\s+where\s+you\s+(?:are|live)", 1.0),
]


def _collect(text: str, patterns: list[tuple[str, float]]) -> tuple[float, list[str]]:
    score = 0.0
    evidence: list[str] = []
    for pattern, weight in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            score += weight
            snippet = _context(text, match.start(), match.end())
            if snippet not in evidence:
                evidence.append(snippet)
    return score, evidence


def _context(text: str, start: int, end: int, pad: int = 60) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def analyze(
    description: str,
    title: str = "",
    tags: list[str] | None = None,
    explicit: bool | None = None,
) -> tuple[str, float, list[str]]:
    """Classify sponsorship availability.

    `explicit` is for sources that publish a real boolean flag (Arbeitnow does);
    it wins over any text heuristic.
    """
    haystack = " ".join(filter(None, [title or "", " ".join(tags or []), description or ""]))

    if explicit is True:
        return "yes", 5.0, ["Source publishes an explicit visa-sponsorship flag"]
    if explicit is False:
        neg_score, neg_evidence = _collect(haystack, NEGATIVE_PATTERNS)
        return "no", -max(3.0, neg_score), neg_evidence or [
            "Source flags this posting as no visa sponsorship"
        ]

    if not haystack.strip():
        return "unknown", 0.0, []

    neg_score, neg_evidence = _collect(haystack, NEGATIVE_PATTERNS)
    pos_score, pos_evidence = _collect(haystack, POSITIVE_PATTERNS)
    glob_score, glob_evidence = _collect(haystack, GLOBAL_HIRE_PATTERNS)

    sponsorship = pos_score - (neg_score * 1.3)

    # An explicit refusal beats everything, including hire-anywhere language:
    # a company can hire globally and still refuse to sponsor a specific role.
    if neg_score >= 2.5 and pos_score < neg_score:
        return "no", round(sponsorship, 2), neg_evidence[:4]
    if sponsorship >= 2.0:
        return "yes", round(sponsorship, 2), pos_evidence[:4]
    if sponsorship <= -1.5:
        return "no", round(sponsorship, 2), neg_evidence[:4]
    if glob_score >= 2.0 and neg_score < 1.0:
        return "global", round(glob_score, 2), glob_evidence[:4]
    return "unknown", round(sponsorship, 2), (pos_evidence + glob_evidence + neg_evidence)[:3]


# Companies with a public, repeated track record of sponsoring work visas.
# Used as a tie-breaker when the posting text itself says nothing.
KNOWN_SPONSORS = {
    "google", "microsoft", "amazon", "meta", "apple", "netflix", "nvidia", "intel", "ibm",
    "oracle", "salesforce", "adobe", "sap", "cisco", "qualcomm", "uber", "lyft", "airbnb",
    "stripe", "shopify", "spotify", "booking.com", "booking holdings", "adyen", "klarna",
    "revolut", "monzo", "wise", "n26", "zalando", "delivery hero", "hellofresh", "gitlab",
    "elastic", "mongodb", "datadog", "snowflake", "databricks", "confluent", "hashicorp",
    "canonical", "red hat", "vmware", "atlassian", "canva", "palantir", "bloomberg",
    "goldman sachs", "jp morgan", "jpmorgan", "morgan stanley", "deutsche bank", "barclays",
    "arm", "asml", "philips", "siemens", "bosch", "ericsson", "nokia", "spotify", "criteo",
    "doctolib", "algolia", "datadog", "contentful", "personio", "celonis", "sumup", "trivago",
    "thoughtworks", "epam", "globant", "infosys", "tcs", "wipro", "cognizant", "accenture",
    "capgemini", "deloitte", "pwc", "kpmg", "ey", "mckinsey",
}


def company_is_known_sponsor(company: str) -> bool:
    name = (company or "").strip().lower()
    if not name:
        return False
    return any(known in name for known in KNOWN_SPONSORS)
