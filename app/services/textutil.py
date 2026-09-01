"""Pure-Python text utilities: tokenizing, TF-IDF and cosine similarity.

Deliberately dependency-free (no scikit-learn / numpy) so the app installs fast
and runs entirely offline on any machine.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")

STOPWORDS: frozenset[str] = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being
below between both but by can cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further had hadn't has hasn't have haven't having he her here hers herself
him himself his how i if in into is isn't it its itself let's me more most mustn't my myself no nor
not of off on once only or other ought our ours ourselves out over own same shan't she should
shouldn't so some such than that the their theirs them themselves then there these they this those
through to too under until up very was wasn't we were weren't what when where which while who whom
why with won't would wouldn't you your yours yourself yourselves will shall may might must also
role job position company team work working experience years year candidate candidates applicant
applicants please apply application requirements responsibilities qualifications preferred plus
strong ability able etc across within using use used help helping join joining looking seeking
opportunity opportunities great good excellent well new like well-being benefits offer offers
""".split())


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace/HTML entities."""
    text = (text or "").replace(" ", " ").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    tokens = _WORD_RE.findall((text or "").lower())
    cleaned: list[str] = []
    for tok in tokens:
        tok = tok.strip(".-")
        if len(tok) < 2 and tok not in {"r", "c"}:
            continue
        if not keep_stopwords and tok in STOPWORDS:
            continue
        cleaned.append(tok)
    return cleaned


def ngrams(tokens: list[str], n: int) -> list[str]:
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _tf(tokens: Iterable[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = sum(counts.values()) or 1
    # sub-linear tf damping keeps long job descriptions from dominating
    return {term: (1 + math.log(c)) / math.sqrt(total) for term, c in counts.items()}


def cosine_tfidf(text_a: str, text_b: str) -> float:
    """Cosine similarity of two documents using a 2-document IDF.

    With only two documents the IDF term mostly acts as a shared-vocabulary
    weighting, which is exactly what we want when comparing one resume against
    one job description.
    """
    tokens_a, tokens_b = tokenize(text_a), tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0

    tf_a, tf_b = _tf(tokens_a), _tf(tokens_b)
    vocab = set(tf_a) | set(tf_b)
    idf = {
        term: math.log(1 + 2 / (1 + int(term in tf_a) + int(term in tf_b))) + 1.0
        for term in vocab
    }

    dot = sum(tf_a.get(t, 0.0) * tf_b.get(t, 0.0) * idf[t] ** 2 for t in vocab)
    norm_a = math.sqrt(sum((tf_a.get(t, 0.0) * idf[t]) ** 2 for t in vocab))
    norm_b = math.sqrt(sum((tf_b.get(t, 0.0) * idf[t]) ** 2 for t in vocab))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def html_to_text(html: str) -> str:
    """Strip HTML down to readable text, preserving list/paragraph breaks."""
    if not html:
        return ""

    # Some boards publish HTML that was escaped twice, so the payload arrives as
    # "&lt;strong&gt;...". One decode pass leaves visible tag soup in the text
    # and in any evidence snippet we quote back to the user.
    if "&lt;" in html or "&amp;lt;" in html:
        import html as _html

        for _ in range(2):
            unescaped = _html.unescape(html)
            if unescaped == html:
                break
            html = unescaped

    if "<" not in html:
        return normalize(html)
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        for li in soup.find_all("li"):
            li.insert_before("\n• ")
        for br in soup.find_all(["br", "p", "div", "h1", "h2", "h3", "h4"]):
            br.insert_before("\n")
        text = soup.get_text(" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)

    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"
