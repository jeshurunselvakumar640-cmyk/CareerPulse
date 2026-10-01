import re
import html
import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional, Dict, Any, List

class HTMLTextExtractor(HTMLParser):
    """
    Strips scripts, styles, SVGs, nav, headers, footers, forms,
    and extracts only clean visible text.
    """
    def __init__(self):
        super().__init__()
        self.text_chunks: List[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style', 'svg', 'nav', 'header', 'footer', 'aside', 'noscript', 'form', 'button', 'select']:
            self.skip_depth += 1
        else:
            # Check for contaminated CSS markers or hidden components
            attr_str = " ".join([f"{k}='{v}'" for k, v in attrs]).lower()
            if any(marker in attr_str for marker in ['display: none', 'visibility: hidden', 'hidden']):
                self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ['script', 'style', 'svg', 'nav', 'header', 'footer', 'aside', 'noscript', 'form', 'button', 'select']:
            if self.skip_depth > 0:
                self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self.text_chunks.append(cleaned)


def clean_page_html(raw_html: str) -> str:
    """Strips tags, scripts, styles, and extracts decoded, clean visible text."""
    if not raw_html or not isinstance(raw_html, str):
        return ""
    try:
        parser = HTMLTextExtractor()
        parser.feed(raw_html)
        text = " ".join(parser.text_chunks)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        clean = re.sub(r'<[^>]*>', ' ', raw_html)
        clean = html.unescape(clean)
        return re.sub(r'\s+', ' ', clean).strip()


def is_html_contaminated(text: str) -> bool:
    """
    Detects strong evidence of raw HTML, CSS utility classes, DOM markup,
    or corrupted UI snippets in a string.
    """
    if not text or not isinstance(text, str):
        return False
    t = text.lower().strip()

    # Raw HTML tags or brackets
    html_tags = ['<div', '<span', '<img', '<svg', '<button', '<p', '<a ', '<ul', '<li', '</div>', '</span>', '</p>', '/>', 'href=', 'style=', 'src=', 'alt=']
    if any(tag in t for tag in html_tags):
        return True

    # Trailing quotes/brackets from truncated HTML attributes (e.g. 'Company Logo">', 'class="..."')
    if re.search(r'["\']\s*>', t) or re.search(r'^class=', t) or re.search(r'\bclass\s*=', t):
        return True

    # CSS utility patterns (Tailwind / utility classes)
    css_patterns = [
        'rounded-xl', 'bg-slate-', 'border-slate-', 'shrink-0',
        'w-10', 'h-10', 'p-2', 'px-', 'py-', 'text-indigo-', 'text-slate-',
        'shadow-', 'items-center', 'justify-between', 'justify-center',
        'flex-col', 'grid-cols'
    ]
    # Check if multiple CSS utility tokens appear
    css_hits = sum(1 for pat in css_patterns if pat in t)
    if css_hits >= 1 and (' ' in t or '-' in t):
        return True

    return False


def is_listing_page(title: str, text: str = "", url: str = "") -> bool:
    """
    Deterministic Python validation logic to detect and reject:
    - job listing pages
    - search result pages
    - aggregator listing pages
    - career category pages
    - listicles & collection pages ("X jobs in India", "Latest Internships", etc.)
    """
    combined_title = str(title or "").lower().strip()
    url_lower = str(url or "").lower().strip()
    text_lower = str(text or "")[:4000].lower()

    # 1. URL-level aggregator / search / listing indicators
    invalid_url_patterns = [
        "/jobs/search", "/internships/search", "/search/jobs", "search?", "q=",
        "/jobs-in-", "/internships-in-", "/vacancies-in-",
        "/all-jobs", "/find-jobs", "/browse-jobs", "/browse/", "/categories/",
        "-jobs.html", "/best-", "/top-", "/list-of-", "/roundup"
    ]
    for pattern in invalid_url_patterns:
        if pattern in url_lower:
            return True

    # 2. Strict listing patterns in title
    listing_title_patterns = [
        r'\bjobs\s+in\b',
        r'\bjobs\s+available\b',
        r'\bjob\s+openings\b',
        r'\bjob\s+listings\b',
        r'\bfind\s+jobs\b',
        r'\bsearch\s+jobs\b',
        r'\blatest\s+jobs\b',
        r'\ball\s+jobs\b',
        r'\btop\s+jobs\b',
        r'\d+\+?\s+(?:[\w/]+\s+){0,5}(?:jobs|openings|internships|vacancies)\b',
        r'\b(?:jobs|openings|internships|vacancies)\s+(?:available\s+)?in\b',
        r'\bopportunities\s+in\b',
        r'\binternships\s+in\b',
        r'\bvacancies\s+in\b',
        r'\blatest\s+internship\s+opportunities\b',
        r'\blatest\s+(?:[\w/]+\s+){0,3}(?:jobs|internships|vacancies|opportunities)\b',
        r'\bshowing\s+\d+',
        r'\btop\s+\d+\b',
        r'\bbest\s+\d+\b',
        r'\blist\s+of\b',
        r'\bjobs\s+for\s+freshers\b',
        r'\bsearch\s+results\b',
        r'\bcareers\s+portal\b',
        r'\bhiring\s+trends\b',
        r'\bsalary\s+guide\b'
    ]
    for pattern in listing_title_patterns:
        if re.search(pattern, combined_title):
            return True

    # 3. Content-level listing indicators
    content_listing_patterns = [
        r'showing\s+\d+[\s–-]+(\d+)?\s+of\s+\d+',
        r'\d+\s+jobs\s+found',
        r'filter\s+by\s+category',
        r'sort\s+by\s+relevance',
        r'search\s+across\s+\d+\s+jobs',
        r'load\s+more\s+jobs',
        r'view\s+all\s+\d+\s+openings'
    ]
    for pattern in content_listing_patterns:
        if re.search(pattern, text_lower):
            return True

    # 4. Check for multiple distinct job-cards or repeated application buttons
    apply_button_count = len(re.findall(r'\b(apply now|view job|quick apply)\b', text_lower))
    if apply_button_count >= 5:
        return True

    return False


def is_expired_opportunity(text: str, deadline: Optional[str] = None) -> bool:
    """Checks whether text or deadline proves the opening is expired or closed."""
    if not text:
        return False
    t = str(text).lower()

    closed_signals = [
        "job closed",
        "no longer accepting applications",
        "position filled",
        "position closed",
        "this job has expired",
        "opportunity expired",
        "application deadline has passed",
        "this vacancy is no longer available",
        "listing has ended",
        "applications are closed"
    ]
    if any(sig in t for sig in closed_signals):
        return True

    if deadline:
        try:
            # Parse ISO or simple date YYYY-MM-DD
            d_str = deadline.strip()[:10]
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            if d < datetime.now(timezone.utc).date():
                return True
        except Exception:
            pass

    return False


def clean_job_title(title: str) -> Optional[str]:
    """
    Cleans, normalizes, and validates a job title.
    Rejects aggregators, listing patterns, and HTML/CSS contaminated strings.
    Extracts the singular, clean opportunity title.
    """
    if not title or not isinstance(title, str):
        return None

    raw = title.strip()
    if not raw:
        return None

    # Immediate rejection if contaminated with HTML/CSS
    if is_html_contaminated(raw):
        return None

    # Immediate rejection if title indicates a collection/list page
    if is_listing_page(raw):
        return None

    # Unescape HTML entities (e.g. &amp;, &quot;, &#39;)
    t = html.unescape(raw)
    t = re.sub(r'<[^>]*>', '', t)
    t = re.sub(r'\s+', ' ', t).strip()

    # Remove leading/trailing non-alphanumeric noise
    t = re.sub(r'^[^\w\(\)\#\+\.]+|[^\w\(\)\#\+\.]+$', '', t).strip()

    if len(t) < 3 or len(t) > 100:
        return None

    # Strip company or site suffix if attached via standard separators
    # E.g. "Software Engineer Intern - Acme Corp" -> "Software Engineer Intern"
    # But only if left side is a coherent title (>= 4 chars)
    for sep in [" - ", " | ", " at ", " @ "]:
        if sep in t:
            parts = t.split(sep)
            candidate = parts[0].strip()
            # If left side looks like a title, use it
            if len(candidate) >= 4 and not is_listing_page(candidate) and not is_html_contaminated(candidate):
                t = candidate
                break

    # Secondary check after stripping
    if is_listing_page(t) or is_html_contaminated(t):
        return None

    return t


def extract_structured_metadata_from_html(html_str: str, url: str) -> Dict[str, Any]:
    """
    Extracts structured metadata using strict priority:
    1. JSON-LD JobPosting schema (Schema.org)
    2. OpenGraph metadata (og:title, og:description, og:site_name)
    3. Twitter cards (twitter:title, twitter:description)
    4. <title> and <h1>
    5. Clean visible text
    """
    result: Dict[str, Any] = {
        "title": None,
        "company": None,
        "location": None,
        "employment_type": None,
        "description": None,
        "posted_date": None,
        "deadline": None,
        "is_job_posting": False,
        "cleaned_text": ""
    }

    if not html_str:
        return result

    # Clean visible text
    cleaned_text = clean_page_html(html_str)
    result["cleaned_text"] = cleaned_text

    # Priority 1: JSON-LD JobPosting
    json_ld_matches = re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>', html_str, re.DOTALL | re.IGNORECASE)
    for raw_json in json_ld_matches:
        try:
            data = json.loads(raw_json.strip())
            # Handle array of schemas or graph
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    items = data["@graph"]
                else:
                    items = [data]

            for item in items:
                if not isinstance(item, dict):
                    continue
                schema_type = str(item.get("@type", ""))
                if "JobPosting" in schema_type:
                    result["is_job_posting"] = True
                    result["title"] = clean_job_title(str(item.get("title", "")))

                    hiring_org = item.get("hiringOrganization")
                    if isinstance(hiring_org, dict):
                        result["company"] = hiring_org.get("name")
                    elif isinstance(hiring_org, str):
                        result["company"] = hiring_org

                    # Location
                    job_loc = item.get("jobLocation")
                    if isinstance(job_loc, dict):
                        address = job_loc.get("address")
                        if isinstance(address, dict):
                            loc_parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
                            result["location"] = ", ".join([str(p) for p in loc_parts if p])
                        elif isinstance(address, str):
                            result["location"] = address

                    result["employment_type"] = item.get("employmentType")
                    desc = item.get("description")
                    if desc:
                        result["description"] = clean_page_html(desc)[:600]
                    result["posted_date"] = item.get("datePosted")
                    result["deadline"] = item.get("validThrough")
                    break
        except Exception:
            continue

    # Priority 2: OpenGraph
    og_title_m = re.search(r'<meta[^>]+property=[\'"]og:title[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_str, re.IGNORECASE)
    if not og_title_m:
        og_title_m = re.search(r'<meta[^>]+content=[\'"]([^\'"]+)[\'"][^>]+property=[\'"]og:title[\'"]', html_str, re.IGNORECASE)
    if og_title_m and not result["title"]:
        candidate_title = clean_job_title(og_title_m.group(1))
        if candidate_title:
            result["title"] = candidate_title

    og_site_m = re.search(r'<meta[^>]+property=[\'"]og:site_name[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_str, re.IGNORECASE)
    if og_site_m and not result["company"]:
        result["company"] = og_site_m.group(1).strip()

    og_desc_m = re.search(r'<meta[^>]+property=[\'"]og:description[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_str, re.IGNORECASE)
    if og_desc_m and not result["description"]:
        result["description"] = clean_page_html(og_desc_m.group(1))[:600]

    # Priority 3: Twitter cards
    tw_title_m = re.search(r'<meta[^>]+name=[\'"]twitter:title[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html_str, re.IGNORECASE)
    if tw_title_m and not result["title"]:
        candidate_title = clean_job_title(tw_title_m.group(1))
        if candidate_title:
            result["title"] = candidate_title

    # Priority 4: <title> tag
    title_tag_m = re.search(r'<title[^>]*>(.*?)</title>', html_str, re.IGNORECASE | re.DOTALL)
    if title_tag_m and not result["title"]:
        candidate_title = clean_job_title(title_tag_m.group(1))
        if candidate_title:
            result["title"] = candidate_title

    # Priority 5: <h1> tag
    h1_tag_m = re.search(r'<h1[^>]*>(.*?)</h1>', html_str, re.IGNORECASE | re.DOTALL)
    if h1_tag_m and not result["title"]:
        candidate_title = clean_job_title(clean_page_html(h1_tag_m.group(1)))
        if candidate_title:
            result["title"] = candidate_title

    return result