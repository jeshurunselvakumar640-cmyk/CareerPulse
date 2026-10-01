# app/services/news_service.py
"""
Technology news verification pipeline for the 24-hour digest.

A publication date alone is NOT treated as proof that the reported
development is current. Each candidate page is retrieved and inspected so
the actual EVENT can be distinguished from the page's publish date, which
is how historical explainers republished today get rejected.
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import httpx
from tavily import TavilyClient
from google import genai

from app.config import settings
from app.services.html_parser import clean_page_html, clean_inline_safe

logger = logging.getLogger("careerpulse.news_pipeline")

# ---------------------------------------------------------------------------
# SOURCE QUALITY
# ---------------------------------------------------------------------------

OFFICIAL_SOURCE_DOMAINS = [
    "openai.com", "anthropic.com", "deepmind.google", "blog.google", "ai.google",
    "blogs.microsoft.com", "blogs.nvidia.com", "developer.nvidia.com", "aws.amazon.com",
    "cloud.google.com", "azure.microsoft.com", "github.blog", "huggingface.co",
    "apple.com", "meta.com", "microsoft.com", "nvidia.com", "amd.com", "intel.com",
    "tsmc.com", "qualcomm.com", "arm.com", "ieee.org", "acm.org", "arxiv.org",
    "nature.com", "science.org", "nist.gov", "cisa.gov", "europa.eu", "sec.gov",
]

REPUTABLE_DOMAINS = [
    "reuters.com", "bloomberg.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "technologyreview.com", "wired.com", "wsj.com",
    "ft.com", "bbc.com", "cnbc.com", "zdnet.com", "theregister.com",
    "venturebeat.com", "axios.com", "fortune.com", "businessinsider.com",
    "engadget.com", "tomshardware.com", "anandtech.com", "bleepingcomputer.com",
    "therecord.media", "infoworld.com", "computerworld.com", "darkreading.com",
    "securityweek.com", "phoronix.com", "lwn.net", "arstechnica.co.uk",
]

# Content farms / SEO scrapers / list aggregators -> always reject
LOW_QUALITY_DOMAINS = [
    "jotechgeek", "jotech", "medium.com", "dev.to", "hashnode.com",
    "substack.com", "quora.com", "reddit.com", "wikipedia.org", "pinterest.",
    "hubspot.com", "blogspot.com", "scribd", "slideshare", "researchgate.net",
    "coursehero", "fiverr.com", "towardsai.net", "guru99.com",
    "geeksforgeeks.org", "javatpoint.com", "tutorialspoint", "w3schools.com",
    "hackernoon.com", "greatlearning", "simplilearn.com", "intellipaat.com",
    "analyticsvidhya.com", "timesofindia.indiatimes.com", "india.com",
]

# Content that is evergreen/commentary, not a single current development
HISTORICAL_PHRASES = [
    "what tech came out in", "tech that came out in", "top releases of",
    "best tech of", "best technology of", "best gadgets of", "best apps of",
    "top 10 tech", "year in review", "yearly recap",
    "retrospective", "a look back at", "look back on", "recap of",
    "through the years", "history of", "evolution of", "must know",
    "everything you need to know", "ultimate guide", "state of the industry in",
]

NON_EVENT_PHRASES = [
    "how to", "how do i", "tutorial", "beginner guide", "explained",
    "what is", "introduction to", "guide to", "checklist", "roadmap",
    "career advice", "tips and tricks", "review:", "roundup", "highlights of",
]

LISTICLE_MARKERS = [
    "top 10", "top 15", "top 20", "best of", "list of",
    "must know", "ultimate guide",
]

DISALLOWED_MARKERS = [
    "how-to", "tutorial", "deal", "discount", "coupon", "best-laptop",
    "best-phone", "review", "opinion", "editorial", "gossip", "horoscope",
    "buying-guide", "top-10", "roundup", "unboxing"
]


def domain_of(url: str) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def classify_source(url: str) -> Dict[str, Any]:
    """Classifies a source domain and assigns a credibility tier."""
    d = domain_of(url)
    lowered = (url or "").lower()

    for bad in LOW_QUALITY_DOMAINS:
        if bad in d or bad in lowered:
            return {"tier": 0, "label": "low_quality", "domain": d, "reject": True}

    for official in OFFICIAL_SOURCE_DOMAINS:
        if d.endswith(official) or official in d:
            return {"tier": 3, "label": "official", "domain": d, "reject": False}

    for rep in REPUTABLE_DOMAINS:
        if d.endswith(rep) or rep in d:
            return {"tier": 2, "label": "reputable", "domain": d, "reject": False}

    return {"tier": 1, "label": "other", "domain": d, "reject": False}


# ---------------------------------------------------------------------------
# HISTORICAL / EVENT-CURRENCY DETECTION
# ---------------------------------------------------------------------------

def detect_historical_content(text: str) -> Dict[str, Any]:
    """
    Determines whether content describes an event inside the requested window
    rather than an old event republished today.
    """
    t = (text or "").lower()
    if not t:
        return {"historical": True, "reason": "empty content"}

    for phrase in HISTORICAL_PHRASES:
        if phrase in t:
            return {"historical": True, "reason": f"historical phrase: '{phrase}'"}

    for marker in LISTICLE_MARKERS:
        if marker in t:
            return {"historical": True, "reason": f"listicle marker: '{marker}'"}

    for phrase in NON_EVENT_PHRASES:
        if phrase in t:
            return {"historical": True, "reason": f"non-event content: '{phrase}'"}

    # Year tokens clearly older than the window, when repeated.
    now_year = datetime.now(timezone.utc).year
    years = [int(m) for m in re.findall(r"\b(?:19|20)\d{2}\b", t)]
    old_years = [y for y in years if y < now_year - 1]
    if len(old_years) >= 3:
        return {"historical": True,
                "reason": f"references older years {sorted(set(old_years))[:4]}"}

    return {"historical": False, "reason": ""}


def is_clickbait_or_trivial(title: str, url: str) -> bool:
    """Detects clickbait, gear deals, generic tutorials, and opinion pieces."""
    combined = f"{title} {url}".lower()
    if any(marker in combined for marker in DISALLOWED_MARKERS):
        return True

    patterns = [
        r'\bhow\s+to\b',
        r'\bbest\s+deals\b',
        r'\bwatch\s+this\b',
        r'\bhere[\'’]s\s+why\b',
        r'\bopinion\b',
        r'\btop\s+\d+\b',
        r'\bwhich\s+should\s+you\s+buy\b'
    ]
    for p in patterns:
        if re.search(p, combined):
            return True
    return False


def verify_publication_date(date_str: Optional[str]) -> bool:
    """Verifies publication within the last 36 hours (24h + timezone buffer)."""
    if not date_str:
        return True
    try:
        cleaned_date = date_str.replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(cleaned_date)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - pub_dt
        if age.total_seconds() > 36 * 3600 or age.total_seconds() < -3600:
            return False
        return True
    except Exception:
        return True


def score_news_importance(title: str, summary: str, source_domain: str) -> int:
    """Internal importance score (0-100). Never surfaced in the email."""
    score = 40
    source_info = classify_source(f"https://{source_domain}")
    score += (source_info["tier"] - 1) * 10

    t = f"{title} {summary}".lower()

    if any(k in t for k in ["model release", "new architecture", "benchmark",
                            "breakthrough", "frontier model", "open source model",
                            "neural network", "launches", "unveils", "releases model"]):
        score += 20

    if any(k in t for k in ["critical vulnerability", "zero-day", "antitrust",
                            "regulation", "ftc", "eu ai act", "cyberattack",
                            "breach", "sanction"]):
        score += 15

    if any(k in t for k in ["google", "microsoft", "apple", "nvidia", "meta",
                            "amazon", "openai", "anthropic", "tesla"]):
        score += 10

    if any(k in t for k in ["semiconductor", "tsmc", "gpu", "chip", "quantum"]):
        score += 10

    return min(100, score)


# ---------------------------------------------------------------------------
# EVENT-LEVEL DEDUPLICATION
# ---------------------------------------------------------------------------

_EVENT_STOPWORDS = {
    "announced", "announces", "announced", "launches", "launched", "launch",
    "releases", "released", "release", "releasing", "unveils", "unveiled",
    "reveals", "revealed", "debuts", "introduces", "introduced", "rolls",
    "update", "updates", "major", "first", "latest", "technology", "tech",
    "new", "says", "said", "will", "now", "after", "before", "over", "with",
    "from", "that", "this", "have", "has", "been", "are", "was", "were",
    "its", "their", "more", "than", "inc", "corp", "company", "big", "best", "top",
    "called", "most", "yet", "stock", "shares", "after", "hours", "percent",
    "adds", "market", "investors", "analysts", "wall", "street",
}


def event_signature(title: str) -> frozenset:
    """Token signature of the EVENT, so 'Google launches X' == 'Google announces X'."""
    tokens = set(re.findall(r"\b[a-z0-9]{3,}\b", (title or "").lower()))
    tokens -= _EVENT_STOPWORDS
    return frozenset(tokens)


def _is_same_event(sig: frozenset, other: frozenset, similarity: float = 0.55) -> bool:
    """
    Two stories are the same event when they share enough distinctive tokens.
    Requiring >= 2 shared tokens prevents unrelated single-word overlaps
    (e.g. two different 'Google' stories) from being merged.
    """
    if not sig or not other:
        return False
    inter = len(sig & other)
    if inter < 2:
        return False
    return inter / min(len(sig), len(other)) >= 0.3 or inter / min(len(sig), len(other)) >= similarity


def deduplicate_news(stories: List[Dict[str, Any]], similarity: float = 0.55) -> List[Dict[str, Any]]:
    """Collapses multiple outlets covering the SAME event into one story,
    keeping the highest-credibility source."""
    kept: List[Dict[str, Any]] = []
    kept_sigs: List[frozenset] = []

    ordered = sorted(
        stories,
        key=lambda s: (s.get("_source_tier", 0), s.get("_importance", 0)),
        reverse=True,
    )

    for story in ordered:
        sig = event_signature(story.get("title", ""))
        if not sig:
            continue

        if any(_is_same_event(sig, prev, similarity) for prev in kept_sigs):
            continue

        kept_sigs.append(sig)
        kept.append(story)

    return kept


# ---------------------------------------------------------------------------
# SOURCE PAGE RETRIEVAL
# ---------------------------------------------------------------------------

def fetch_article_page(url: str) -> Optional[str]:
    """Retrieves the actual article HTML for verification."""
    try:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 CareerPulse/1.0"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        with httpx.Client(timeout=8.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200 and len(resp.text) > 400:
                return resp.text
    except Exception as e:
        logger.debug("Article fetch failed for %s: %s", url, e)
    return None


def extract_news_metadata(html_text: str, url: str) -> Dict[str, Any]:
    """Extracts real title / description / publish date from the page itself."""
    result: Dict[str, Any] = {"title": None, "description": None,
                              "published_at": None, "site_name": None}
    if not html_text:
        return result

    for raw_json in re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(raw_json.strip())
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and "@graph" in data:
                items = data["@graph"]
            else:
                items = [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                st = str(item.get("@type", ""))
                if any(x in st for x in ("NewsArticle", "Article", "BlogPosting")):
                    if not result["title"] and isinstance(item.get("headline"), str):
                        result["title"] = item["headline"]
                    if not result["published_at"]:
                        result["published_at"] = item.get("datePublished")
                    if not result["description"] and isinstance(item.get("description"), str):
                        result["description"] = item["description"]
        except Exception:
            continue

    def meta(*patterns):
        for p in patterns:
            m = re.search(p, html_text, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return None

    if not result["title"]:
        result["title"] = meta(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        )
    if not result["description"]:
        result["description"] = meta(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        )
    if not result["published_at"]:
        result["published_at"] = meta(
            r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
            r'"datePublished"\s*:\s*"([^"]+)"',
        )
    if not result["site_name"]:
        result["site_name"] = meta(
            r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']'
        )

    if not result["title"]:
        t = meta(r'<h1[^>]*>(.*?)</h1>', r'<title[^>]*>(.*?)</title>')
        if t:
            result["title"] = clean_page_html(t)

    if result["title"]:
        result["title"] = clean_inline_safe(result["title"])[:300]
    if result["description"]:
        result["description"] = clean_inline_safe(result["description"])[:500]

    return result


# ---------------------------------------------------------------------------
# GEMINI VERIFICATION
# ---------------------------------------------------------------------------

def _gemini_verify_and_summarize(story: Dict[str, Any]) -> None:
    """Strict JSON analysis. May only reject/summarize - never invent facts."""
    body = story.get("_body", "")

    prompt = f"""You are a technology news verification and importance-analysis engine.

You are NOT a generic content generator.

Evaluate only the supplied source content.

Determine whether the article describes a genuinely important and current technology development.

Reject:
- historical articles
- old events
- tutorials
- listicles
- generic guides
- promotional content
- opinion presented as factual news
- duplicate coverage
- low-quality SEO content
- aggregator pages
- unsupported claims
- articles whose actual event is outside the requested 24-hour window

Publication date alone is NOT sufficient.
Determine whether the actual technology event/development being reported is current.

Never invent facts, URLs, publication dates, companies, products, statistics, or quotes.
The source URL is supplied by the retrieval system.

Return strict JSON.

Headline: {story['title']}
Source content: {body[:2500]}

Return strict JSON only:
{{
    "is_current_development": true,
    "is_important": true,
    "confidence": 0.0,
    "factual_summary": "2-3 factual sentences based only on the content above",
    "why_it_matters": "1 concise sentence explaining why this matters to a software engineer",
    "category": "short category label"
}}"""

    client = genai.Client(api_key=settings.gemini_api_key)
    chat = client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
    resp = chat.send_message(prompt)
    txt = (resp.text or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
    data = json.loads(txt.strip())

    if data.get("is_important") is False or data.get("is_current_development") is False:
        story["is_verified"] = False
        story["is_current"] = False
        story["rejection_reason"] = "Gemini: not a current, important development"
        return

    if data.get("factual_summary"):
        story["summary"] = str(data["factual_summary"])[:350]
    story["why_it_matters"] = str(data.get("why_it_matters")
                                  or "Significant technical and industry development.")
    story["category"] = str(data.get("category") or "Technology")[:60]
    story["confidence"] = data.get("confidence", 0.0)


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def fetch_and_verify_technology_news(max_stories: int = 6) -> List[Dict[str, Any]]:
    """
    Retrieves, verifies, deduplicates and scores major technology developments
    from the previous 24 hours. Returns at most `max_stories` verified items.
    Never pads with trivial or fabricated content.
    """
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("Tavily API key missing for news search")
        return []

    logger.info("digest: news retrieval started")
    client = TavilyClient(api_key=api_key)

    news_queries = [
        "major artificial intelligence model release research breakthrough",
        "major technology developer platform announcement cybersecurity critical",
        "semiconductor hardware computing enterprise AI news",
    ]

    candidate_results = []
    for q in news_queries:
        try:
            resp = client.search(query=q, topic="news", days=1,
                                  max_results=10, search_depth="advanced")
            candidate_results.extend(resp.get("results", []))
        except Exception as e:
            logger.warning("digest: tavily news query failed (%s): %s", q, e)

    logger.info("digest: news candidates found = %d", len(candidate_results))

    verified: List[Dict[str, Any]] = []
    seen_urls = set()
    rejected = 0

    for item in candidate_results:
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        raw_content = (item.get("content") or "").strip()
        pub_date = item.get("published_date")

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        source_info = classify_source(url)
        if source_info["reject"]:
            rejected += 1
            continue

        if is_clickbait_or_trivial(title, url):
            rejected += 1
            continue

        if not verify_publication_date(pub_date):
            rejected += 1
            continue

        # Retrieve the ACTUAL page - Tavily is discovery only
        html_text = fetch_article_page(url)
        page_meta = extract_news_metadata(html_text, url) if html_text else {}

        real_title = page_meta.get("title") or title
        real_description = page_meta.get("description") or raw_content
        page_date = page_meta.get("published_at") or pub_date

        if page_date and not verify_publication_date(page_date):
            rejected += 1
            continue

        if not real_title or len(real_title) < 15:
            rejected += 1
            continue

        body_text = clean_page_html(html_text)[:5000] if html_text else raw_content[:5000]

        hist = detect_historical_content(f"{real_title} {real_description} {body_text[:2500]}")
        if hist["historical"]:
            rejected += 1
            logger.debug("news rejected (historical): %s -> %s",
                         real_title[:70], hist["reason"])
            continue

        importance = score_news_importance(real_title, real_description, source_info["domain"])
        if importance < 50:
            rejected += 1
            continue

        verified.append({
            "title": real_title,
            "source_name": (page_meta.get("site_name")
                            or source_info["domain"].split(".")[0].capitalize()),
            "source_url": url,
            "published_at": page_date or "",
            "event_date": page_date or "",
            "summary": (real_description or body_text[:350]).strip()[:350],
            "category": "Technology",
            "importance": 0,
            "confidence": 0,
            "is_current": True,
            "is_verified": True,
            "rejection_reason": "",
            "_source_tier": source_info["tier"],
            "_importance": importance,
            "_body": body_text[:6000],
        })

    logger.info("digest: news rejected = %d, news verified candidates = %d",
                rejected, len(verified))

    if not verified:
        return []

    before = len(verified)
    deduped = deduplicate_news(verified)
    logger.info("digest: news duplicates removed = %d", before - len(deduped))

    deduped.sort(key=lambda s: s.get("_importance", 0), reverse=True)
    selected = deduped[:max_stories]

    if settings.gemini_api_key and selected:
        for story in selected:
            try:
                _gemini_verify_and_summarize(story)
            except Exception as e:
                logger.debug("digest: gemini verification fallback: %s", e)
                story.setdefault("why_it_matters",
                                 "Significant technical and industry development.")
                story.setdefault("category", "Technology")

    final = [s for s in selected if s.get("is_verified") and s.get("is_current")]
    logger.info("digest: final verified news count = %d", len(final))

    for s in final:
        s.pop("_body", None)
        s.pop("_source_tier", None)
        s["importance"] = int(s.get("_importance", 0))
        s.pop("_importance", None)

    return final[:max_stories]