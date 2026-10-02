"""
Personalized, verified technology news pipeline for the CareerPulse Tech News tab.

Pipeline:
    User Profile
      -> dynamic search queries (derived from skills / interests / roles / domains)
      -> Tavily discovery
      -> actual source page retrieval (httpx)
      -> source + recency + article verification
      -> deduplication (canonical URL + headline fingerprint)
      -> Gemini relevance analysis (strict JSON, deterministic fallback)
      -> per-user, per-profile cached response

Guarantees enforced here:
  * URLs, headlines and dates always come from the retrieved source, never from Gemini.
  * Nothing is fabricated or padded; short results are returned as short results.
  * Cached results are scoped to one user AND one profile fingerprint.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
from google import genai
from tavily import TavilyClient

from app.config import settings
from app.services.html_parser import clean_page_html

logger = logging.getLogger("careerpulse.personalized_news")

# -------------------------------------------------------------------
# TUNING
# -------------------------------------------------------------------

MAX_QUERIES = 6                  # hard cap on Tavily calls per refresh
CANDIDATES_PER_QUERY = 8         # raw hits requested per query
MAX_PAGES_FETCHED = 30           # cap on concurrent source-page retrievals
CACHE_TTL_SECONDS = 30 * 60      # 30 minutes of cache reuse
MIN_CACHE_AGE_FOR_REFRESH = 60   # ignore caches younger than this on a forced refresh
DEFAULT_WINDOW_HOURS = 36        # recency gate (24h + timezone buffer)
GEMINI_CONCURRENCY = 4
GEMINI_TIMEOUT_SECONDS = 12.0

TRUSTED_DOMAINS = [
    "reuters.com", "bloomberg.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "technologyreview.com", "wired.com", "nature.com",
    "openai.com", "anthropic.com", "deepmind.google", "blog.google",
    "blogs.microsoft.com", "github.blog", "aws.amazon.com", "developer.nvidia.com",
    "huggingface.co", "wsj.com", "ft.com", "bbc.com", "cnbc.com",
    "microsoft.com", "apple.com", "meta.com", "nvidia.com", "amd.com",
    "intel.com", "cloud.google.com", "arxiv.org", "ieee.org", "acm.org",
]

OFFICIAL_MARKERS = (
    "blog.", "/blog/", "news.", "/news/", "press", "announc", "changelog",
    "developer.", "/engineering", "research",
)

# Low-value / promotional / non-news content
REJECT_MARKERS = [
    "how-to", "how to ", "tutorial", "deal", "discount", "coupon", "best-laptop",
    "best-phone", "review", "opinion", "editorial", "gossip", "horoscope",
    "buying-guide", "top-10", "roundup", "unboxing", "best-", "top-",
    "sponsored", "advertorial", "giveaway", "listicle", "explained:", "what is",
    # Evergreen / retrospective pieces are not news.
    "the year of", "year in review", "a retrospective", "looking back at",
    "anniversary of", "decade of",
    # Calls to action and promotional campaigns.
    "internship challenge", "apply now", "register now", "enroll now",
    "sign up now", "join now", "limited seats", "last date to apply",
    "admissions open", "call for applications", "call for papers",
]

# Categories assigned deterministically from verified page content.
CATEGORY_RULES = [
    ("AI Models & Research", ["ai model", "llm", "large language model", "machine learning",
                              "neural network", "transformer", "gpt", "benchmark", "model release",
                              "frontier model", "open weights", "fine-tuning", "research paper"]),
    ("Cybersecurity", ["vulnerability", "zero-day", "0day", "exploit", "breach", "ransomware",
                       "cyberattack", "cve-", "security flaw", "phishing", "botnet"]),
    ("Developer Platforms & Tools", ["developer platform", "framework release", "sdk",
                                      "open source", "release notes", "version control",
                                      "programming language", "developer tool", "api update"]),
    ("Semiconductors & Hardware", ["semiconductor", "chip", "gpu", "cpu", "silicon", "wafer",
                                   "tsmc", "asml", "nvidia", "quantum chip", "hardware release"]),
    ("Cloud & Infrastructure", ["cloud", "kubernetes", "serverless", "data center", "infrastructure",
                                "database", "distributed systems"]),
    ("Startups & Funding", ["funding round", "series a", "series b", "series c", "seed round",
                            "raises", "valuation", "acquisition", "acquires", "ipo", "venture"]),
    ("Regulation & Policy", ["regulation", "antitrust", "privacy law", "gdpr", "eu ai act",
                             "policy", "legislation", "court", "lawsuit", "government"]),
    ("Robotics & Space", ["robot", "robotics", "autonomous vehicle", "spacex", "satellite",
                          "spacecraft", "lunar", "mars", "rocket"]),
]

# Low-signal topics that are technically "tech" but rarely relevant to a
# software/AI career profile.
OFF_TOPIC_MARKERS = [
    "celebrity", "gadget", "smartphone launch", "tv show", "movie", "netflix series",
    "fashion", "recipe", "nba", "football", "match result", "box office",
]


class NewsUnavailable(Exception):
    """Raised when discovery itself failed (Tavily error, not merely no results)."""


# -------------------------------------------------------------------
# PROFILE HELPERS
# -------------------------------------------------------------------

def build_news_profile(metadata: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
    """Normalizes Supabase user metadata into the structure the pipeline consumes."""
    def clean_list(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        out = []
        for item in value:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s:
                out.append(s)
        # de-duplicate case-insensitively, keep original order
        seen: Set[str] = set()
        result = []
        for s in out:
            key = s.lower()
            if key not in seen:
                seen.add(key)
                result.append(s)
        return result

    headline = (metadata.get("headline") or "").strip()
    interests = clean_list(metadata.get("interests"))
    roles = clean_list(metadata.get("preferred_roles")) or ([headline] if headline else [])

    return {
        "user_id": user_id,
        "skills": clean_list(metadata.get("skills")),
        "interests": interests,
        "roles": roles,
        "domains": clean_list(metadata.get("preferred_domains")),
        "experience_level": (metadata.get("experience_level") or "").strip(),
        "headline": headline,
        "location": (metadata.get("location") or "").strip(),
    }


def profile_fingerprint(profile: Dict[str, Any]) -> str:
    """
    Stable hash of everything that affects personalization.
    A change to any of these fields yields a different fingerprint, which
    naturally invalidates the cached feed.
    """
    parts = [
        ",".join(sorted(s.lower() for s in profile.get("skills", []))),
        ",".join(sorted(s.lower() for s in profile.get("interests", []))),
        ",".join(sorted(s.lower() for s in profile.get("roles", []))),
        ",".join(sorted(s.lower() for s in profile.get("domains", []))),
        (profile.get("experience_level") or "").lower(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def is_profile_empty(profile: Dict[str, Any]) -> bool:
    return not any([
        profile.get("skills"), profile.get("interests"),
        profile.get("roles"), profile.get("domains"),
    ])


# -------------------------------------------------------------------
# DYNAMIC QUERY GENERATION
# -------------------------------------------------------------------

# Broad fallback intent words, never used as a standalone query.
_INTENT_SUFFIXES = [
    "latest technology news",
    "developer ecosystem news",
    "latest releases and announcements",
]


def _topic_terms(profile: Dict[str, Any]) -> List[Tuple[str, float]]:
    """
    Builds (term, weight) pairs from the profile.
    Interests weigh most, then roles/domains, then skills.
    """
    terms: List[Tuple[str, float]] = []
    seen: Set[str] = set()

    def add(raw: Any, weight: float, limit_len: int = 42):
        if not isinstance(raw, str):
            return
        term = re.sub(r"\s+", " ", raw).strip()
        if not term or len(term) < 2:
            return
        # Drop generic career words that make useless queries
        if term.lower() in {"developer", "engineer", "student", "software", "full stack developer"}:
            return
        term = term[:limit_len].strip()
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append((term, weight))

    for v in profile.get("interests", [])[:5]:
        add(v, 1.0)
    for v in profile.get("roles", [])[:2]:
        add(v, 0.85)
    for v in profile.get("domains", [])[:3]:
        add(v, 0.8)
    for v in profile.get("skills", [])[:8]:
        add(v, 0.7)

    return terms


def _normalize_query_tokens(query: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9+#\.]+", query.lower()) if len(t) > 2}


def _near_duplicate(a: str, b: str, threshold: float = 0.75) -> bool:
    ta, tb = _normalize_query_tokens(a), _normalize_query_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if not inter:
        return False
    return inter / min(len(ta), len(tb)) >= threshold


def generate_news_queries(profile: Dict[str, Any], max_queries: int = MAX_QUERIES) -> List[str]:
    """
    Derives queries from the user's actual profile.
    High-weight topics get a broader intent phrase, lower-weight topics get a
    developer-focused phrase. Near-duplicates are removed before searching.
    """
    terms = _topic_terms(profile)
    if not terms:
        return []

    experience = (profile.get("experience_level") or "").lower()
    is_student = bool(experience and ("student" in experience or "fresher" in experience or "entry" in experience))

    candidates: List[str] = []
    for term, weight in terms:
        if weight >= 1.0:
            candidates.append(f"{term} {_INTENT_SUFFIXES[0]}")
        elif weight >= 0.8:
            candidates.append(f"{term} technology news")
        else:
            # Skills: pair with ecosystem/tooling language instead of the bare name
            if is_student:
                candidates.append(f"{term} developer ecosystem and beginner friendly news")
            else:
                candidates.append(f"{term} {_INTENT_SUFFIXES[1]}")

    # Deduplicate near-identical queries, preserving priority order.
    unique: List[str] = []
    for q in candidates:
        if not any(_near_duplicate(q, existing) for existing in unique):
            unique.append(q)
        if len(unique) >= max_queries:
            break

    logger.info("Generated %d personalized news queries for profile", len(unique))
    return unique


# -------------------------------------------------------------------
# DETERMINISTIC FILTERING (used before and as a fallback after Gemini)
# -------------------------------------------------------------------

def _canonicalize_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
        netloc = p.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+$", "", p.path) or "/"
        return urlunparse((p.scheme.lower(), netloc, path, "", "", ""))
    except Exception:
        return url.strip().lower()


def _is_rejected_content(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in REJECT_MARKERS)


def _is_off_topic(text: str) -> bool:
    t = (text or "").lower()
    if not any(m in t for m in OFF_TOPIC_MARKERS):
        return False
    # Only reject if it is off-topic AND has no strong technology signal.
    tech_signal = sum(1 for kw in ["software", "developer", "api", "model", "framework",
                                   "security", "cloud", "chip", "open source", "engineering"]
                      if kw in t)
    return tech_signal < 2


def _is_trusted_source(domain: str, url: str) -> Tuple[bool, str]:
    """Returns (is_preferred, reason). Non-preferred sources are allowed but ranked lower."""
    domain = domain.lower()
    if any(domain.endswith(d) or d in domain for d in TRUSTED_DOMAINS):
        return True, "established_tech_or_news_source"
    if any(marker in (url or "").lower() for marker in OFFICIAL_MARKERS):
        return True, "official_announcement"
    return False, "general_source"


def _detect_category(text: str) -> str:
    t = (text or "").lower()
    for label, keywords in CATEGORY_RULES:
        if any(k in t for k in keywords):
            return label
    return "Technology"


def _deterministic_relevance(article: Dict[str, Any], profile: Dict[str, Any]) -> Tuple[float, List[str], List[str]]:
    """
    Skill/interest overlap scoring. Used to pre-rank candidates and as the
    fallback when Gemini is unavailable.
    Returns (score 0-1, matched_interests, matched_skills).
    """
    haystack = f"{article.get('title', '')} {article.get('summary', '')} {article.get('body_sample', '')}".lower()

    def hits(values: List[str]) -> List[str]:
        found = []
        for v in values or []:
            token = str(v).strip().lower()
            if len(token) < 3:
                continue
            # match the term or its first meaningful word
            candidates = [token] + ([token.split()[0]] if len(token.split()) > 1 else [])
            if any(re.search(rf"\b{re.escape(c)}\b", haystack) for c in candidates):
                found.append(str(v))
        return found

    matched_interests = hits(profile.get("interests", []))
    matched_roles = hits(profile.get("roles", []))
    matched_domains = hits(profile.get("domains", []))
    matched_skills = hits(profile.get("skills", []))

    score = (
        0.42 * min(len(matched_interests), 3) / 3
        + 0.18 * min(len(matched_roles), 2) / 2
        + 0.12 * min(len(matched_domains), 2) / 2
        + 0.28 * min(len(matched_skills), 4) / 4
    )

    if matched_interests:
        score += 0.05
    if not (matched_interests or matched_roles or matched_domains or matched_skills):
        score *= 0.25  # essentially unrelated to this profile

    return round(min(score, 1.0), 3), matched_interests, matched_skills


# -------------------------------------------------------------------
# SOURCE PAGE RETRIEVAL + VERIFICATION
# -------------------------------------------------------------------

def _parse_date(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    cleaned = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(cleaned[:24].strip(), fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def extract_article_metadata(html_text: str, url: str) -> Dict[str, Any]:
    """
    Pulls headline, summary, publication date and source strictly from the
    retrieved page (JSON-LD NewsArticle -> OpenGraph -> Twitter -> <time> -> <title>).
    """
    result: Dict[str, Any] = {
        "title": None, "summary": None, "published_at": None,
        "source_name": None, "body_sample": "", "is_news_article": False,
    }
    if not html_text:
        return result

    # 1. JSON-LD NewsArticle / Article
    for raw_json in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(raw_json.strip())
            items = data if isinstance(data, list) else (data.get("@graph", []) if isinstance(data, dict) and "@graph" in data else [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                schema_type = str(item.get("@type", ""))
                if any(t in schema_type for t in ("NewsArticle", "Article", "BlogPosting", "Report")):
                    result["is_news_article"] = True
                    if not result["title"] and isinstance(item.get("headline"), str):
                        result["title"] = item["headline"].strip()
                    if not result["published_at"]:
                        result["published_at"] = item.get("datePublished") or item.get("dateCreated")
                    if not result["summary"] and isinstance(item.get("description"), str):
                        result["summary"] = item["description"].strip()
                    # The publication is the source, never the byline author.
                    if not result["source_name"]:
                        publisher = item.get("publisher")
                        if isinstance(publisher, dict) and publisher.get("name"):
                            result["source_name"] = str(publisher["name"]).strip()
                        elif isinstance(publisher, list) and publisher and isinstance(publisher[0], dict):
                            result["source_name"] = str(publisher[0].get("name") or "").strip() or None
            if result["is_news_article"]:
                break
        except Exception:
            continue

    def meta(*patterns: str) -> Optional[str]:
        for p in patterns:
            m = re.search(p, html_text, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return None

    # 2. OpenGraph / Twitter
    if not result["title"]:
        result["title"] = meta(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        )
    if not result["summary"]:
        result["summary"] = meta(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']+)["\']',
        )
    if not result["published_at"]:
        result["published_at"] = meta(
            r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'<time[^>]+datetime=["\']([^"\']+)["\']',
        )
    if not result["source_name"]:
        result["source_name"] = meta(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']')

    # 3. <title> / <h1>
    if not result["title"]:
        t = meta(r'<h1[^>]*>(.*?)</h1>', r'<title[^>]*>(.*?)</title>')
        if t:
            result["title"] = clean_page_html(t)

    # 4. Body sample for relevance scoring
    result["body_sample"] = clean_page_html(html_text)[:4000]

    # Source name fallback from domain
    if not result["source_name"]:
        netloc = urlparse(url).netloc.removeprefix("www.")
        result["source_name"] = netloc.split(".")[0].capitalize() if netloc else "Unknown"
    else:
        # A byline or author name is not a publication; only the site name is.
        netloc = urlparse(url).netloc.removeprefix("www.")
        site_word = netloc.split(".")[0].lower()
        if len(result["source_name"].split()) > 4 or result["source_name"].lower() in ("unknown", site_word):
            result["source_name"] = netloc.split(".")[0].capitalize() if netloc else "Unknown"

    if result["title"]:
        result["title"] = clean_page_html(result["title"])[:300]
    if result["summary"]:
        result["summary"] = clean_page_html(result["summary"])[:420]

    return result


def fetch_and_verify_article(url: str, window_hours: int = DEFAULT_WINDOW_HOURS) -> Optional[Dict[str, Any]]:
    """
    Retrieves the ACTUAL source page and verifies it is a real, recent article.
    Returns None when the page cannot be retrieved, is not an article, is too
    old, or is clearly low-value content.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 CareerPulse/1.0"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        with httpx.Client(timeout=8.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            html_text = resp.text
    except Exception as e:
        logger.debug("Source retrieval failed for %s: %s", url, e)
        return None

    if len(html_text) < 300:
        return None

    meta = extract_article_metadata(html_text, url)

    # Must look like an actual article with a real headline
    if not meta.get("title") or len(meta["title"]) < 15:
        return None
    body = meta.get("body_sample", "")
    if len(body) < 200:
        return None

    combined = f"{meta['title']} {meta.get('summary', '')}"
    if _is_rejected_content(combined):
        return None
    if _is_off_topic(combined):
        return None

    # Recency verification. Never invent a date: if the page exposes none,
    # we keep the Tavily-discovered date only, and reject if that is also absent.
    published = _parse_date(meta.get("published_at"))
    if not published:
        logger.debug("No verifiable publication date for %s — skipping", url)
        return None

    age_hours = (datetime.now(timezone.utc) - published).total_seconds() / 3600
    if age_hours > window_hours or age_hours < -2:
        return None

    domain = urlparse(url).netloc.removeprefix("www.")
    trusted, source_reason = _is_trusted_source(domain, url)

    return {
        "title": meta["title"],
        "url": url,
        "canonical_url": _canonicalize_url(url),
        "source_name": meta.get("source_name") or domain,
        "source_domain": domain,
        "summary": meta.get("summary") or body[:300],
        "published_at": published.isoformat(),
        "age_hours": round(age_hours, 2),
        "body_sample": body[:2500],
        "category": _detect_category(f"{meta['title']} {body[:1200]}"),
        "source_trusted": trusted,
        "source_reason": source_reason,
        "verified": True,
    }


# -------------------------------------------------------------------
# DEDUPLICATION
# -------------------------------------------------------------------

def _headline_tokens(title: str) -> Set[str]:
    stop = {"announced", "announces", "launches", "releases", "release", "update", "major",
            "first", "latest", "technology", "tech", "new", "says", "said", "will", "now"}
    tokens = set(re.findall(r"\b[a-z0-9]{4,}\b", (title or "").lower()))
    return tokens - stop


def deduplicate_articles(articles: List[Dict[str, Any]], similarity: float = 0.6) -> List[Dict[str, Any]]:
    """
    Removes repeats by canonical URL and by near-identical headline
    (the same story covered by several outlets).
    """
    seen_urls: Set[str] = set()
    kept: List[Dict[str, Any]] = []
    seen_token_sets: List[Set[str]] = []

    for art in articles:
        canon = art.get("canonical_url") or _canonicalize_url(art.get("url", ""))
        if canon in seen_urls:
            continue

        tokens = _headline_tokens(art.get("title", ""))
        is_dup = False
        for prev in seen_token_sets:
            if tokens and prev:
                inter = len(tokens & prev)
                if inter == 0:
                    continue
                if inter / max(len(tokens), 1) >= similarity or inter / min(len(tokens), len(prev)) >= similarity:
                    is_dup = True
                    break
        if is_dup:
            continue

        seen_urls.add(canon)
        seen_token_sets.append(tokens)
        kept.append(art)

    return kept


# -------------------------------------------------------------------
# GEMINI RELEVANCE ANALYSIS
# -------------------------------------------------------------------

def _parse_gemini_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    t = t.strip()
    start = t.find("{")
    if start == -1:
        return None
    end = t.rfind("}")
    if end == -1:
        return None
    try:
        return json.loads(t[start:end + 1])
    except Exception:
        return None


async def _analyze_with_gemini(client: genai.Client, articles: List[Dict[str, Any]],
                              profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Scores each verified article for profile relevance.
    Gemini may only judge relevance and summarize; the URL and headline are
    always taken from the retrieved source and are re-asserted afterwards.
    """
    sem = asyncio.Semaphore(GEMINI_CONCURRENCY)

    async def analyze(article: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        async with sem:
            prompt = f"""You are a technology news relevance analyst for a personalised developer feed.
Assess whether the following VERIFIED news article is genuinely relevant to one specific candidate profile.

Candidate Profile:
- Skills: {', '.join(profile.get('skills', [])) or 'not specified'}
- Interests: {', '.join(profile.get('interests', [])) or 'not specified'}
- Preferred roles: {', '.join(profile.get('roles', [])) or 'not specified'}
- Preferred domains: {', '.join(profile.get('domains', [])) or 'not specified'}
- Experience level: {profile.get('experience_level') or 'not specified'}

Article headline: {article['title']}
Article source: {article['source_name']}
Article summary: {article.get('summary', '')[:500]}
Article body excerpt: {article.get('body_sample', '')[:1800]}

Rules:
- Judge only from the supplied content. Never invent facts, headlines, dates or URLs.
- "matched_interests" and "matched_skills" must ONLY contain items literally present in the candidate profile.
- relevance_score is 0.0 to 1.0 where 1.0 means strongly relevant to this specific profile.
- Reject generic news that has no meaningful connection to this candidate's skills, interests, roles or domains.
- summary must be 1-2 factual sentences based only on the article content.
- why_it_matters must explain relevance to THIS candidate in one sentence and may only reference profile items listed above.

Return strict JSON only:
{{
  "relevant": true,
  "relevance_score": 0.0,
  "matched_interests": [],
  "matched_skills": [],
  "summary": "",
  "why_it_matters": "",
  "confidence": 0.0
}}"""

            def call():
                chat = client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
                return chat.send_message(prompt)

            try:
                resp = await asyncio.wait_for(asyncio.to_thread(call), timeout=GEMINI_TIMEOUT_SECONDS)
                parsed = _parse_gemini_json(resp.text)
                if not parsed:
                    return None

                allowed_interests = {s.lower() for s in profile.get("interests", [])}
                allowed_skills = {s.lower() for s in profile.get("skills", [])}

                def filter_allowed(values: Any, allowed: Set[str]) -> List[str]:
                    if not isinstance(values, list):
                        return []
                    out = []
                    for v in values:
                        if isinstance(v, str) and v.strip().lower() in allowed:
                            out.append(v.strip())
                    return sorted(set(out))

                score = parsed.get("relevance_score", 0.0)
                try:
                    score = float(score)
                except Exception:
                    score = 0.0

                return {
                    "relevant": bool(parsed.get("relevant", False)) and score >= 0.35,
                    "relevance_score": round(max(0.0, min(score, 1.0)), 3),
                    "matched_interests": filter_allowed(parsed.get("matched_interests"), allowed_interests),
                    "matched_skills": filter_allowed(parsed.get("matched_skills"), allowed_skills),
                    "summary": str(parsed.get("summary") or article.get("summary") or "")[:420],
                    "why_it_matters": str(parsed.get("why_it_matters") or "")[:300],
                    "confidence": parsed.get("confidence", 0.0),
                    "analyzed_by": "gemini",
                }
            except Exception as e:
                logger.debug("Gemini relevance analysis failed for one article: %s", e)
                return None

    results = await asyncio.gather(*(analyze(a) for a in articles))
    return [r for r in results if r is not None]


def _fallback_relevance(article: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic relevance used when Gemini is unavailable.
    Never states anything unsupported: the explanation is built only from
    profile terms that literally matched the article text.
    """
    score, matched_interests, matched_skills = _deterministic_relevance(article, profile)

    reasons: List[str] = []
    if matched_interests:
        reasons.append(f"you are interested in {', '.join(matched_interests[:3])}")
    if matched_skills:
        reasons.append(f"it involves your {', '.join(matched_skills[:3])} skills")

    why = ("Relevant because " + "; ".join(reasons) + ".") if reasons else \
        "General technology news relevant to your field."

    return {
        "relevant": score >= 0.25,
        "relevance_score": score,
        "matched_interests": matched_interests,
        "matched_skills": matched_skills,
        "summary": article.get("summary", ""),
        "why_it_matters": why,
        "confidence": 0.5,
        "analyzed_by": "deterministic",
    }


# -------------------------------------------------------------------
# CACHE  (per user AND per profile fingerprint)
# -------------------------------------------------------------------

# Process-local cache: user_id -> {"fingerprint": str, "fetched_at": float, "articles": [...]}
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}

_SUPABASE_CACHE_TABLE = "user_news_cache"


_supabase_cache_supported: Optional[bool] = None


def _get_cache(supabase_client: Any, user_id: str) -> Optional[Dict[str, Any]]:
    global _supabase_cache_supported

    mem = _MEMORY_CACHE.get(user_id)
    if mem:
        return mem

    if supabase_client is not None and _supabase_cache_supported is not False:
        try:
            res = supabase_client.table(_SUPABASE_CACHE_TABLE).select("*").eq("user_id", user_id).execute()
            if res.data:
                row = res.data[0]
                articles = row.get("articles")
                if isinstance(articles, str):
                    articles = json.loads(articles)
                _SUPABASE_CACHE_SUPPORTED = True
                _MEMORY_CACHE[user_id] = {
                    "fingerprint": row.get("profile_fingerprint"),
                    "fetched_at": float(row.get("fetched_at") or 0),
                    "articles": articles or [],
                }
                return _MEMORY_CACHE[user_id]
        except Exception as e:
            # Table not created yet -> fall back to memory cache permanently.
            _supabase_cache_supported = False
            logger.info("Supabase news cache unavailable, using in-memory cache: %s", e)
    return None


def _set_cache(supabase_client: Any, user_id: str, fingerprint: str, articles: List[Dict[str, Any]]) -> None:
    payload = {
        "user_id": user_id,
        "profile_fingerprint": fingerprint,
        "fetched_at": time.time(),
        "articles": articles,
    }
    _MEMORY_CACHE[user_id] = payload

    if supabase_client is not None and _supabase_cache_supported is not False:
        try:
            supabase_client.table(_SUPABASE_CACHE_TABLE).upsert(payload, on_conflict="user_id").execute()
        except Exception as e:
            _supabase_cache_supported = False
            logger.info("Supabase news cache write skipped (in-memory only): %s", e)


def invalidate_news_cache(user_id: str) -> None:
    """Clears cached news for one user. Called on profile change and on logout."""
    _MEMORY_CACHE.pop(user_id, None)


# -------------------------------------------------------------------
# MAIN PIPELINE
# -------------------------------------------------------------------

def _run_pipeline_sync(profile: Dict[str, Any], limit: int,
                       supabase_client: Any = None) -> List[Dict[str, Any]]:
    """Blocking implementation, executed in a worker thread."""

    # --- 1. dynamic queries from the profile ---
    queries = generate_news_queries(profile)
    if not queries:
        return []

    # --- 2. Tavily discovery ---
    client = TavilyClient(api_key=settings.tavily_api_key)
    candidates: List[Dict[str, Any]] = []
    seen_canon: Set[str] = set()
    search_failures = 0

    for q in queries:
        try:
            resp = client.search(
                query=q,
                topic="news",
                days=1,
                max_results=CANDIDATES_PER_QUERY,
                search_depth="advanced",
            )
        except Exception as e:
            search_failures += 1
            logger.warning("Tavily query failed (%s): %s", q, e)
            continue

        for item in (resp.get("results") or []):
            url = (item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            canon = _canonicalize_url(url)
            if canon in seen_canon:
                continue
            seen_canon.add(canon)
            candidates.append({
                "url": url,
                "discovered_title": (item.get("title") or "").strip(),
                "published_date": item.get("published_date"),
                "matched_query": q,
            })

    if not candidates and search_failures == len(queries):
        raise NewsUnavailable("Search backend is unavailable. Please try again shortly.")

    candidates = candidates[:MAX_PAGES_FETCHED]

    # --- 3. retrieve actual source pages + verify ---
    verified: List[Dict[str, Any]] = []
    for cand in candidates:
        art = fetch_and_verify_article(cand["url"])
        if not art:
            continue
        # Date proven on the page wins; otherwise fall back to the search metadata.
        if not art.get("published_at") and cand.get("published_date"):
            art["published_at"] = cand["published_date"]
        art["matched_query"] = cand["matched_query"]
        verified.append(art)

    if not verified:
        return []

    # --- 4. deduplicate ---
    verified = deduplicate_articles(verified)

    # --- 5. deterministic pre-rank, then keep a bounded candidate set ---
    for art in verified:
        art["deterministic_score"], art["det_interests"], art["det_skills"] = \
            _deterministic_relevance(art, profile)

    verified.sort(key=lambda a: (a["deterministic_score"], -a["age_hours"]), reverse=True)
    candidates_scored = verified[:max(limit * 2, 12)]

    # --- 6. Gemini relevance analysis, with deterministic fallback ---
    final: List[Dict[str, Any]] = []
    gem_client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None

    if gem_client:
        async def run_analysis():
            try:
                return await _analyze_with_gemini(gem_client, candidates_scored, profile)
            except Exception as e:
                logger.warning("Gemini analysis failed, using deterministic fallback: %s", e)
                return []

        analysis_results = asyncio.run(run_analysis())
        by_index = {id(a): i for i, a in enumerate(candidates_scored)}
        results_by_index: Dict[int, Dict[str, Any]] = {}
        # map returned results back by title+url (order is preserved by gather)
        for res, art in zip(analysis_results, candidates_scored):
            results_by_index[by_index[id(art)]] = res

        for idx, art in enumerate(candidates_scored):
            analysis = results_by_index.get(idx) or _fallback_relevance(art, profile)
            if not analysis.get("relevant"):
                continue
            final.append({**art, **analysis})
    else:
        for art in candidates_scored:
            analysis = _fallback_relevance(art, profile)
            if analysis.get("relevant"):
                final.append({**art, **analysis})

    # --- 7. final ranking: relevance first, then recency, then source trust ---
    def rank_key(a: Dict[str, Any]):
        return (
            a.get("relevance_score", 0),
            a.get("source_trusted", False),
            -float(a.get("age_hours", 999)),
        )

    final.sort(key=rank_key, reverse=True)
    final = final[:limit]
    final = deduplicate_articles(final, similarity=0.7)

    # Strip internal fields before returning
    clean = []
    for a in final:
        clean.append({
            "title": a["title"],
            "url": a["url"],
            "source_name": a["source_name"],
            "source_domain": a["source_domain"],
            "summary": a.get("summary", ""),
            "why_it_matters": a.get("why_it_matters", ""),
            "matched_interests": a.get("matched_interests", []),
            "matched_skills": a.get("matched_skills", []),
            "category": a.get("category", "Technology"),
            "published_at": a.get("published_at"),
            "age_hours": a.get("age_hours"),
            "relevance_score": a.get("relevance_score", 0),
            "source_trusted": a.get("source_trusted", False),
            "verified": True,
        })
    return clean


async def get_personalized_news(
    profile: Dict[str, Any],
    limit: int = 10,
    refresh: bool = False,
    supabase_client: Any = None,
) -> Dict[str, Any]:
    """
    Entry point used by the API layer.

    Caching rules:
      * cache is keyed by (user_id, profile fingerprint)
      * a profile change produces a new fingerprint -> automatic refresh
      * `refresh=true` bypasses a cache younger than MIN_CACHE_AGE_FOR_REFRESH
    """
    user_id = profile.get("user_id") or "anonymous"
    fingerprint = profile_fingerprint(profile)
    now = time.time()

    cached = _get_cache(supabase_client, user_id)

    if (cached
            and cached.get("fingerprint") == fingerprint
            and isinstance(cached.get("articles"), list)
            and now - float(cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS
            and not (refresh and now - float(cached.get("fetched_at", 0)) < MIN_CACHE_AGE_FOR_REFRESH)):
        return {
            "status": "success",
            "articles": cached["articles"],
            "cache": "hit",
            "fetched_at": cached["fetched_at"],
            "profile_fingerprint": fingerprint,
            "personalized_for": {
                "skills": profile.get("skills", []),
                "interests": profile.get("interests", []),
                "roles": profile.get("roles", []),
                "domains": profile.get("domains", []),
                "experience_level": profile.get("experience_level", ""),
            },
        }

    if is_profile_empty(profile):
        return {
            "status": "success",
            "articles": [],
            "cache": "bypassed",
            "reason": "empty_profile",
            "profile_fingerprint": fingerprint,
            "personalized_for": {"skills": [], "interests": [], "roles": [], "domains": [], "experience_level": ""},
        }

    if not settings.tavily_api_key:
        return {"status": "error", "articles": [], "detail": "Search backend is not configured.", "cache": "bypass"}

    try:
        articles = await asyncio.to_thread(_run_pipeline_sync, profile, limit, supabase_client)
    except NewsUnavailable as e:
        return {"status": "error", "articles": [], "detail": str(e), "cache": "bypass"}
    except Exception as e:
        logger.exception("Personalized news pipeline failure")
        return {"status": "error", "articles": [], "detail": f"News retrieval failed: {e}", "cache": "bypass"}

    _set_cache(supabase_client, user_id, fingerprint, articles)

    return {
        "status": "success",
        "articles": articles,
        "cache": "miss",
        "fetched_at": now,
        "profile_fingerprint": fingerprint,
        "personalized_for": {
            "skills": profile.get("skills", []),
            "interests": profile.get("interests", []),
            "roles": profile.get("roles", []),
            "domains": profile.get("domains", []),
            "experience_level": profile.get("experience_level", ""),
        },
    }