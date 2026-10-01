import logging
from typing import Dict, Any, List
from app.services.job_search import discover_and_extract_opportunities
from app.services.news_service import fetch_and_verify_technology_news

logger = logging.getLogger("careerpulse.search_service")

def search_live_content_via_tavily(user_profile: Dict[str, Any], max_results_per_query: int = 10) -> Dict[str, Any]:
    """
    Unified entry point for retrieving verified single job opportunities
    and verified technology news from the public web.
    Never fabricates fake jobs or padded titles.
    """
    logger.info("Triggered search_live_content_via_tavily")

    try:
        # 1. Fetch strictly verified single opportunities
        verified_jobs = discover_and_extract_opportunities(user_profile, max_results=max_results_per_query * 2)
    except Exception as e:
        logger.error("Error during job discovery: %s", e)
        verified_jobs = []

    try:
        # 2. Fetch verified technology news from the last 24 hours
        verified_news = fetch_and_verify_technology_news(max_stories=5)
    except Exception as e:
        logger.error("Error during news retrieval: %s", e)
        verified_news = []

    return {
        "jobs": verified_jobs,
        "news": verified_news
    }