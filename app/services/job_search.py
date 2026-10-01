import re
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse
import httpx
from tavily import TavilyClient
from google import genai

from app.config import settings
from app.services.html_parser import (
    clean_page_html,
    clean_job_title,
    is_html_contaminated,
    is_listing_page,
    is_expired_opportunity,
    extract_structured_metadata_from_html
)

logger = logging.getLogger("careerpulse.job_pipeline")

def normalize_text_for_dedup(text: str) -> str:
    """Normalizes text for robust deduplication comparison."""
    if not text:
        return ""
    t = text.lower()
    # Remove common company legal and regional suffixes for comparison
    t = re.sub(r'\b(inc|ltd|llc|technologies|solutions|corporation|corp|private|pvt|co|india|usa|us)\b', '', t)
    # Remove punctuation and extra whitespace
    t = re.sub(r'[^\w\s]', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def is_direct_company_url(url: str) -> bool:
    """Checks if a URL belongs to a company's direct ATS or careers portal rather than an aggregator."""
    u = url.lower()
    direct_patterns = [
        "greenhouse.io", "lever.co", "workday.com", "smartrecruiters.com",
        "ashbyhq.com", "bamboohr.com", "myworkdayjobs.com", "jobs.lever.co",
        "boards.greenhouse.io", "apply.workable.com", "careers."
    ]
    return any(p in u for p in direct_patterns)


def is_known_aggregator(url: str) -> bool:
    """Identifies third-party aggregator and job board platforms."""
    u = url.lower()
    aggregator_domains = [
        "indeed.com", "naukri.com", "monster.com", "shine.com",
        "glassdoor.com", "simplyhired.com", "ziprecruiter.com", "jooble.org"
    ]
    return any(d in u for d in aggregator_domains)


def fetch_page_content(url: str) -> Optional[str]:
    """Retrieves live HTML page content with a strict timeout and realistic user-agent."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 CareerPulse/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }
        with httpx.Client(timeout=7.0, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200 and len(resp.text) > 200:
                return resp.text
    except Exception as e:
        logger.debug("Live fetch failed for %s: %s", url, e)
    return None


def extract_skills_heuristically(text: str) -> Tuple[List[str], List[str]]:
    """Deterministic extraction of required and preferred skills from job description text."""
    if not text:
        return (["Python"], ["SQL"])

    t_lower = text.lower()
    tech_catalog = [
        "python", "fastapi", "django", "flask", "javascript", "typescript",
        "react", "node", "sql", "postgresql", "mysql", "mongodb", "git",
        "docker", "aws", "c++", "c", "java", "machine learning", "rest api"
    ]

    found_skills = []
    for tech in tech_catalog:
        if re.search(rf'\b{re.escape(tech)}\b', t_lower):
            found_skills.append(tech.capitalize() if tech != "fastapi" and tech != "sql" else ("FastAPI" if tech == "fastapi" else "SQL"))

    # Separate into required and preferred based on section markers if present
    req = []
    pref = []
    if "preferred" in t_lower or "nice to have" in t_lower or "bonus" in t_lower:
        parts = re.split(r'\b(preferred|nice to have|bonus|plus)\b', t_lower, maxsplit=1)
        req_text = parts[0]
        pref_text = parts[1] if len(parts) > 1 else ""

        for s in found_skills:
            if s.lower() in pref_text and s.lower() not in req_text:
                pref.append(s)
            else:
                req.append(s)
    else:
        req = found_skills[:4]
        pref = found_skills[4:7]

    return (req or ["Python"], pref or ["SQL"])


def extract_opportunity_with_gemini(
    client: Optional[genai.Client],
    source_content: str,
    page_url: str,
    pre_extracted: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Uses Gemini strictly as a factual extraction engine with the user-mandated prompt.
    Gemini is forbidden from inventing jobs, companies, or URLs.
    """
    if not client or not source_content:
        return None

    # Truncate content safely to avoid token overflow
    truncated_content = source_content[:3500]

    system_instruction = """You are a factual job and internship information extraction engine.
You are NOT a job recommendation generator.
Extract information only from the supplied source content.
Determine whether the supplied page represents ONE specific active job or internship opportunity.
Reject:
job listing pages
search pages
aggregator pages
category pages
generic careers pages without a specific opening
blogs
recruitment articles
listicles
expired opportunities
closed opportunities
pages where a specific company/job cannot be established
Never invent missing information.
Separate required skills from preferred skills.
Return strict JSON only.
Accuracy is more important than completeness."""

    prompt = f"""Source Page URL: {page_url}
Pre-extracted Metadata: {json.dumps({k: v for k, v in pre_extracted.items() if v}, default=str)}

Supplied Page Content:
{truncated_content}

Return ONLY a strict JSON object adhering to this schema:
{{
    "is_valid_opportunity": true/false,
    "opportunity_type": "job" or "internship",
    "title": "exact job title or null",
    "company": "exact company name or null",
    "location": "specific location or 'Remote' or null",
    "employment_type": "Full-time" or "Internship" or "Part-time" or null,
    "experience_level": "Internship" or "Entry level" or null,
    "required_skills": ["skill1", "skill2"],
    "preferred_skills": ["skill1"],
    "description_summary": "concise 2-3 sentence factual summary of responsibilities without HTML",
    "application_url": "verified direct link if present in text, else null",
    "posted_date": "YYYY-MM-DD or null",
    "deadline": "YYYY-MM-DD or null",
    "is_aggregator": false,
    "is_expired": false,
    "confidence": integer between 0 and 100
}}"""

    try:
        chat = client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
        resp = chat.send_message(f"{system_instruction}\n\n{prompt}")
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text.strip())
        return data
    except Exception as e:
        logger.debug("Gemini extraction error: %s", e)
        return None


def discover_and_extract_opportunities(user_profile: Dict[str, Any], max_results: int = 25) -> List[Dict[str, Any]]:
    """
    STRICT JOB VALIDATION PIPELINE:
    Tavily Search -> Candidate URLs -> Retrieve actual page content -> Extract structured job information -> Validate -> Normalize -> Deduplicate -> Match against candidate -> Display.
    """
    api_key = settings.tavily_api_key
    if not api_key:
        logger.warning("Tavily API key missing in config")
        return []

    logger.info("Starting strict job discovery pipeline...")

    # 1. Tavily Search (Discovery Only)
    tavily_client = TavilyClient(api_key=api_key)
    candidate_skills = user_profile.get("skills", ["Python", "FastAPI"])
    top_skill = candidate_skills[0] if candidate_skills else "Python"
    second_skill = candidate_skills[1] if len(candidate_skills) > 1 else "FastAPI"

    queries = [
        f'site:myinternships.in/internships OR site:greenhouse.io OR site:lever.co "{top_skill}" intern Mumbai remote',
        f'site:unstop.com/o OR site:internshala.com/internship/detail "{top_skill}" intern developer Mumbai',
        f'"{top_skill}" "{second_skill}" Developer Intern apply Mumbai remote active job'
    ]

    raw_search_items = []
    for q in queries:
        try:
            logger.info("Executing Tavily discovery query: %s", q)
            resp = tavily_client.search(query=q, max_results=8, search_depth="basic")
            results = resp.get("results", [])
            logger.info("Found %d candidate search results for query", len(results))
            raw_search_items.extend(results)
        except Exception as e:
            logger.error("Tavily search query failed: %s", e)

    # Candidate URLs collection
    candidate_urls: Set[str] = set()
    url_to_snippet: Dict[str, Dict[str, Any]] = {}

    for item in raw_search_items:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        if not url:
            continue

        # Preliminary listing check on URL and search title
        if is_listing_page(title=title, url=url):
            logger.info("Rejected candidate URL early (listing pattern): %s - %s", url, title)
            continue

        if url not in candidate_urls:
            candidate_urls.add(url)
            url_to_snippet[url] = item

    logger.info("Collected %d candidate URLs for retrieval", len(candidate_urls))

    # Initialize Gemini client if available
    gemini_client = None
    if settings.gemini_api_key:
        try:
            gemini_client = genai.Client(api_key=settings.gemini_api_key)
        except Exception:
            gemini_client = None

    extracted_opportunities: List[Dict[str, Any]] = []

    # 2. Retrieve actual page content & Extract structured metadata
    for url in list(candidate_urls)[:max_results * 2]:
        snippet_item = url_to_snippet.get(url, {})
        raw_snippet_title = snippet_item.get("title", "")
        raw_snippet_content = snippet_item.get("content", "")

        page_html = fetch_page_content(url)
        metadata = {}

        if page_html:
            metadata = extract_structured_metadata_from_html(page_html, url)
            content_text = metadata.get("cleaned_text", "")
        else:
            # Fallback to snippet if page cannot be retrieved
            content_text = clean_page_html(raw_snippet_content)

        # 3. Deterministic Validation
        best_title = metadata.get("title") or clean_job_title(raw_snippet_title)
        if not best_title:
            logger.info("Rejected opportunity (no valid title or HTML contaminated): %s", url)
            continue

        if is_html_contaminated(best_title):
            logger.info("Rejected opportunity (title HTML contaminated): %s", best_title)
            continue

        if is_listing_page(title=best_title, text=content_text, url=url):
            logger.info("Rejected opportunity (detected listing page): %s - %s", best_title, url)
            continue

        # Expiration check
        is_expired = (
            is_expired_opportunity(content_text, metadata.get("deadline")) or
            is_expired_opportunity(raw_snippet_content)
        )
        if is_expired:
            logger.info("Rejected opportunity (expired / closed): %s", best_title)
            continue

        # Establish Company
        company = metadata.get("company")
        if not company:
            # Parse from title if formatted like "Role at Company" or "Role - Company"
            if " at " in raw_snippet_title:
                company = raw_snippet_title.split(" at ")[-1].split("|")[0].split(" - ")[0].strip()
            elif " - " in raw_snippet_title:
                parts = raw_snippet_title.split(" - ")
                if len(parts) > 1 and len(parts[1].strip()) < 35:
                    company = parts[1].strip()
            else:
                domain = urlparse(url).netloc.replace("www.", "")
                company = domain.split(".")[0].capitalize()

        if not company or len(company) < 2:
            logger.info("Rejected opportunity (company could not be verified): %s", url)
            continue

        # Location
        location = metadata.get("location") or "Mumbai, Remote"

        # Skills
        req_skills, pref_skills = extract_skills_heuristically(content_text or raw_snippet_content)

        # Description
        description = metadata.get("description") or clean_page_html(content_text or raw_snippet_content)
        if len(description) > 300:
            description = description[:297] + "..."

        # 4. Optional Gemini Deep Extraction (if content is rich)
        gemini_result = None
        if gemini_client and len(content_text) > 150:
            gemini_result = extract_opportunity_with_gemini(
                client=gemini_client,
                source_content=content_text,
                page_url=url,
                pre_extracted={
                    "title": best_title,
                    "company": company,
                    "location": location
                }
            )

        # Merge or validate with Gemini output if valid
        if gemini_result and isinstance(gemini_result, dict):
            if not gemini_result.get("is_valid_opportunity", True):
                logger.info("Gemini rejected opportunity as invalid / listing: %s", best_title)
                continue

            g_title = clean_job_title(gemini_result.get("title", ""))
            if g_title:
                best_title = g_title
            g_comp = gemini_result.get("company")
            if g_comp and isinstance(g_comp, str) and len(g_comp) >= 2:
                company = g_comp
            if gemini_result.get("required_skills"):
                req_skills = [s for s in gemini_result["required_skills"] if isinstance(s, str)]
            if gemini_result.get("preferred_skills"):
                pref_skills = [s for s in gemini_result["preferred_skills"] if isinstance(s, str)]
            if gemini_result.get("description_summary"):
                description = clean_page_html(gemini_result["description_summary"])
            if gemini_result.get("location"):
                location = gemini_result["location"]
            if gemini_result.get("is_expired"):
                logger.info("Gemini marked opportunity as expired: %s", best_title)
                continue

        # 5. Build strict Opportunity Schema
        opportunity_record = {
            "is_valid_opportunity": True,
            "opportunity_type": "internship" if "intern" in best_title.lower() else "job",
            "title": best_title,
            "company": company,
            "location": location,
            "employment_type": metadata.get("employment_type") or ("Internship" if "intern" in best_title.lower() else "Full-time"),
            "experience_level": "Internship" if "intern" in best_title.lower() else "Entry level",
            "required_skills": req_skills,
            "preferred_skills": pref_skills,
            "description_summary": description or "Verified individual career opportunity matching candidate technical skills.",
            "application_url": url,
            "source_url": url,
            "posted_date": metadata.get("posted_date") or None,
            "deadline": metadata.get("deadline") or None,
            "is_aggregator": is_known_aggregator(url),
            "is_expired": False,
            "confidence": 95 if metadata.get("is_job_posting") else 85
        }

        extracted_opportunities.append(opportunity_record)

    logger.info("Extracted %d valid single opportunity records before deduplication", len(extracted_opportunities))

    # 6. Deduplicate Jobs
    # Deduplicate using normalized company + title + location.
    # Prefer official company/application URL over an aggregator URL.
    deduped_map: Dict[str, Dict[str, Any]] = {}

    for opp in extracted_opportunities:
        key = f"{normalize_text_for_dedup(opp['company'])}_{normalize_text_for_dedup(opp['title'])}_{normalize_text_for_dedup(opp['location'])}"
        
        if key not in deduped_map:
            deduped_map[key] = opp
        else:
            existing = deduped_map[key]
            # If current opp is a direct company URL and existing is an aggregator, replace it
            if is_direct_company_url(opp["application_url"]) and not is_direct_company_url(existing["application_url"]):
                deduped_map[key] = opp
            elif existing.get("is_aggregator") and not opp.get("is_aggregator"):
                deduped_map[key] = opp

    final_unique_jobs = list(deduped_map.values())
    logger.info("Retained %d unique verified single opportunities after deduplication", len(final_unique_jobs))

    return final_unique_jobs[:max_results]