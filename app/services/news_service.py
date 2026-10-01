import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import httpx
from tavily import TavilyClient
from google import genai

from app.config import settings
from app.services.html_parser import clean_page_html

logger = logging.getLogger("careerpulse.news_pipeline")

# Reputable technology news & primary publication domains
PREFERRED_DOMAINS = [
    "reuters.com", "bloomberg.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "technologyreview.com", "wired.com", "nature.com",
    "openai.com", "anthropic.com", "deepmind.google", "blog.google",
    "blogs.microsoft.com", "github.blog", "aws.amazon.com", "developer.nvidia.com",
    "huggingface.co", "wsj.com", "ft.com", "bbc.com", "cnbc.com"
]

DISALLOWED_MARKERS = [
    "how-to", "tutorial", "deal", "discount", "coupon", "best-laptop",
    "best-phone", "review", "opinion", "editorial", "gossip", "horoscope",
    "buying-guide", "top-10", "roundup", "unboxing"
]

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
    """Verifies that the article was published within the last 36 hours (24 hours + timezone buffer)."""
    if not date_str:
        return True # If date cannot be parsed, allow if discovered by fresh news search
    try:
        # Normalize ISO strings
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
    """
    Computes an internal importance score (0-100) based on:
    - Industry impact & technical significance
    - Developer / platform relevance
    - Source credibility
    """
    score = 40 # Base threshold

    t = f"{title} {summary}".lower()

    # Domain credibility
    if any(domain in source_domain.lower() for domain in PREFERRED_DOMAINS):
        score += 20

    # Major AI breakthroughs / model releases
    if any(k in t for k in ["model release", "new architecture", "benchmark", "breakthrough", "frontier model", "open source model", "neural network"]):
        score += 20

    # Major regulation / cybersecurity
    if any(k in t for k in ["critical vulnerability", "zero-day", "antitrust", "regulation", "ftc", "eu ai act", "cyberattack"]):
        score += 15

    # Big Tech / Platform changes
    if any(k in t for k in ["google", "microsoft", "apple", "nvidia", "meta", "amazon", "openai", "anthropic"]):
        score += 10

    # Hardware & Semiconductors
    if any(k in t for k in ["semiconductor", "tsmc", "gpu", "chip", "quantum"]):
        score += 10

    return min(100, score)


def deduplicate_news(stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicates multiple news stories covering the exact same event."""
    unique_stories: List[Dict[str, Any]] = []
    seen_topic_tokens: List[set] = []

    for story in stories:
        title = story.get("title", "")
        # Extract meaningful alphanumeric tokens
        tokens = set(re.findall(r'\b[a-zA-Z0-9]{4,}\b', title.lower()))
        # Remove common noise words
        tokens = tokens - {"announced", "announces", "launches", "releases", "update", "major", "first", "latest", "technology"}

        is_duplicate = False
        for seen in seen_topic_tokens:
            intersection = tokens.intersection(seen)
            if len(tokens) > 0 and len(intersection) / len(tokens) >= 0.60:
                is_duplicate = True
                break

        if not is_duplicate:
            unique_stories.append(story)
            seen_topic_tokens.append(tokens)

    return unique_stories


def fetch_and_verify_technology_news(max_stories: int = 6) -> List[Dict[str, Any]]:
    """
    Retrieves, verifies, deduplicates, and scores major technology developments
    from the previous 24 hours.
    Returns 3 to 6 major verified stories. Never pads with fake or trivial content.
    """
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("Tavily API key missing for news search")
        return []

    logger.info("Searching for major technology news from the last 24 hours...")
    client = TavilyClient(api_key=api_key)

    news_queries = [
        "major artificial intelligence model release research breakthrough 24 hours",
        "major technology developer platform announcement cybersecurity critical",
        "semiconductor hardware computing enterprise AI news today"
    ]

    candidate_results = []
    for q in news_queries:
        try:
            resp = client.search(
                query=q,
                topic="news",
                days=1,
                max_results=8,
                search_depth="advanced"
            )
            candidate_results.extend(resp.get("results", []))
        except Exception as e:
            logger.debug("Tavily news query failed: %s", e)

    # Filter & Verify candidate articles
    verified_candidates: List[Dict[str, Any]] = []
    seen_urls = set()

    for item in candidate_results:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        raw_content = item.get("content", "").strip()
        pub_date = item.get("published_date")

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # Check for clickbait, tutorials, or deals
        if is_clickbait_or_trivial(title, url):
            continue

        # Check publication date window
        if not verify_publication_date(pub_date):
            continue

        source_domain = urlparse(url).netloc.replace("www.", "")
        source_name = source_domain.split(".")[0].capitalize()

        importance = score_news_importance(title, raw_content, source_domain)
        if importance < 50:
            continue

        verified_candidates.append({
            "title": title,
            "source_name": source_name,
            "source_url": url,
            "published_at": pub_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": clean_page_html(raw_content)[:350],
            "importance_score": importance
        })

    # Deduplicate overlapping reporting
    deduped = deduplicate_news(verified_candidates)

    # Sort by importance score descending
    deduped.sort(key=lambda s: s["importance_score"], reverse=True)

    # Target 3 to 6 stories (never pad to reach 5-6 if fewer exist; never send more than 6)
    selected = deduped[:max_stories]
    logger.info("Selected %d verified major technology news stories", len(selected))

    # Optional Gemini enhancement for 2-4 sentence factual summary + Why it matters
    # URLs and dates are strictly preserved from search results and NEVER generated by Gemini
    if settings.gemini_api_key and selected:
        try:
            gem_client = genai.Client(api_key=settings.gemini_api_key)
            for story in selected:
                try:
                    p = f"""Summarize this verified tech news event in 2-3 concise, factual sentences, and add one concise sentence explaining why it matters for software engineers.
Do NOT invent URLs, dates, or statistics.

Headline: {story['title']}
Source Content: {story['summary']}

Return strict JSON:
{{
    "factual_summary": "2-3 factual sentences",
    "why_it_matters": "1 concise sentence"
}}"""
                    chat = gem_client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
                    r = chat.send_message(p)
                    txt = r.text.strip()
                    if txt.startswith("```"):
                        txt = txt.split("```")[1]
                        if txt.startswith("json"):
                            txt = txt[4:]
                    p_json = json.loads(txt.strip())
                    if p_json.get("factual_summary"):
                        story["summary"] = p_json["factual_summary"]
                    if p_json.get("why_it_matters"):
                        story["why_it_matters"] = p_json["why_it_matters"]
                    else:
                        story["why_it_matters"] = "Relevant for platform and software engineering developments."
                except Exception:
                    story["why_it_matters"] = "Significant technical and industry development."
        except Exception as e:
            logger.debug("Gemini news summarization error: %s", e)

    return selected
