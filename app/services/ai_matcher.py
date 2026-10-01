import re
import json
import logging
from typing import Dict, Any, List, Optional
from google import genai
from app.config import settings
from app.services.html_parser import clean_job_title, is_html_contaminated, is_listing_page

logger = logging.getLogger("careerpulse.job_matcher")

# Common skill alias mapping
SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "react.js": "react",
    "reactjs": "react",
    "node.js": "node",
    "nodejs": "node",
    "postgres": "postgresql",
    "postgresql db": "postgresql",
    "postgres db": "postgresql",
    "mongo": "mongodb",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "ai/ml": "artificial intelligence / machine learning",
    "ai/ml engineer": "artificial intelligence / machine learning",
    "genai": "generative ai",
    "git/github": "git/github",
    "github": "git/github",
    "git": "git/github",
    "c++": "c++",
    "golang": "go",
    "aws": "amazon web services",
    "gcp": "google cloud",
    "rest": "rest api",
    "restful api": "rest api",
    "rest apis": "rest api",
    "fast api": "fastapi",
    "ai/llms": "ai / llms",
    "llm": "ai / llms",
    "llms": "ai / llms",
    "apis": "apis",
    "restful apis": "apis",
    "api": "apis",
}

# Human-readable display form. Displayed in the dashboard and the digest email.
SKILL_DISPLAY_NAMES = {
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "react": "React",
    "node": "Node.js",
    "nodejs": "Node.js",
    "postgresql": "PostgreSQL",
    "mongodb": "MongoDB",
    "machine learning": "Machine Learning",
    "artificial intelligence": "Artificial Intelligence",
    "artificial intelligence / machine learning": "AI / Machine Learning",
    "generative ai": "Generative AI",
    "git/github": "Git / GitHub",
    "amazon web services": "Amazon Web Services",
    "google cloud": "Google Cloud",
    "rest api": "REST API",
    "apis": "APIs",
    "ai / llms": "AI / LLMs",
    "fastapi": "FastAPI",
    "python": "Python",
    "java": "Java",
    "c++": "C++",
    "sql": "SQL",
    "mysql": "MySQL",
    "django": "Django",
    "flask": "Flask",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "go": "Go",
    "graphql": "GraphQL",
    "html": "HTML",
    "css": "CSS",
    "tailwind": "Tailwind CSS",
    "tensorflow": "TensorFlow",
    "pytorch": "PyTorch",
    "pandas": "Pandas",
    "numpy": "NumPy",
    "opencv": "OpenCV",
    "aws": "Amazon Web Services",
    "azure": "Azure",
    "linux": "Linux",
    "git": "Git",
}

# Terms that should never be treated as equivalent to a different skill.
NON_EQUIVALENT_GUARDS = {"java", "javascript", "c", "c++", "go", "rust"}


def normalize_skill(skill: str) -> str:
    """Normalizes skill aliases to consistent lowercase canonical names."""
    if not skill or not isinstance(skill, str):
        return ""
    s = skill.strip().lower()
    s = re.sub(r'^[•\-*\s]+', '', s).strip()
    s = SKILL_ALIASES.get(s, s)
    # Guard against collapsing genuinely different languages
    if s in NON_EQUIVALENT_GUARDS:
        return s
    return s


def display_skill(skill: str) -> str:
    """Human-readable, correctly cased skill label for the UI and email."""
    if not skill or not isinstance(skill, str):
        return ""
    raw = skill.strip()
    if not raw:
        return ""
    canonical = normalize_skill(raw)
    if canonical in SKILL_DISPLAY_NAMES:
        return SKILL_DISPLAY_NAMES[canonical]
    # Fallback: title-case words but preserve known acronyms.
    acronyms = {"ai", "ml", "api", "apis", "sql", "css", "html", "aws", "gcp", "llm", "llms",
                "gpu", "qa", "ui", "ux", "os", "db", "ci", "cd", "js", "ts", "nlp", "iot"}
    words = re.split(r'([/+&])', raw)
    out = []
    for w in words:
        if w in ("/", "+", "&"):
            out.append(" / " if w == "/" else w)
        elif w.lower() in acronyms:
            out.append(w.upper())
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return "".join(out).strip()


def display_skills(skills) -> list:
    """Formats a list of skills for display, de-duplicated."""
    if not skills:
        return []
    seen = set()
    out = []
    for s in skills:
        d = display_skill(s)
        if d and d.lower() not in seen:
            seen.add(d.lower())
            out.append(d)
    return out


def deterministic_score_opportunity(job: Dict[str, Any], user_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic Python matching algorithm adhering to strict weights:
    - Required Skill Match = 60%
    - Preferred Skill Match = 20%
    - Role Relevance = 10%
    - Experience/Location Compatibility = 10%

    Hard eligibility failures are rejected outright rather than scored.

    Tiers:
    - Gold: >= 60
    - Silver: 40-59
    - Bronze: < 40
    """
    user_skills_raw = user_profile.get("skills", ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"])
    user_skills = {normalize_skill(s) for s in user_skills_raw if s}

    job_title = job.get("title", "")
    job_desc = job.get("description_summary", "") or job.get("description", "")
    # NOTE: location is taken from the verified source and is never replaced
    # with the candidate's preferred location.
    job_location = str(job.get("location", "")).lower()

    # --- HARD ELIGIBILITY GATE ---
    is_expired = bool(job.get("is_expired", False))
    if is_expired:
        return {
            "score": 0,
            "tier": "Rejected",
            "required_skill_score": 0,
            "preferred_skill_score": 0,
            "role_score": 0,
            "experience_location_score": 0,
            "matched_skills": [],
            "missing_skills": [],
            "is_eligible": False,
            "rejection_reason": "Opportunity is expired or closed.",
        }

    # 1. Required Skills
    req_skills_raw = job.get("required_skills", [])
    required_inferred = False
    if not req_skills_raw:
        # Infer technical skills from title and description if not pre-parsed
        inferred = []
        combined_text = f"{job_title} {job_desc}".lower()
        common_tech = ["python", "javascript", "react", "fastapi", "django", "java", "sql", "c++", "c", "node", "typescript", "machine learning"]
        for tech in common_tech:
            if re.search(rf'\b{re.escape(tech)}\b', combined_text):
                inferred.append(tech)
        if inferred:
            req_skills_raw = inferred
            required_inferred = True
        else:
            req_skills_raw = []

    req_skills = {normalize_skill(s) for s in req_skills_raw if s}

    # 2. Preferred Skills
    pref_skills_raw = job.get("preferred_skills", [])
    pref_skills = {normalize_skill(s) for s in pref_skills_raw if s}

    # Required Skills Score (max 60)
    matched_req = set()
    missing_req = []
    if req_skills:
        matched_req = user_skills.intersection(req_skills)
        missing_req = sorted(req_skills - user_skills)
        req_ratio = len(matched_req) / len(req_skills)
        req_score = req_ratio * 60.0
        # Preserve the posting's own ordering for stable, readable output.
        matched_req_ordered = [s for s in req_skills_raw if normalize_skill(s) in matched_req]
    else:
        # No verifiable requirements: award only partial credit. A posting
        # whose requirements cannot be verified must never score full marks.
        matched_req = user_skills.intersection({"python", "javascript", "sql"})
        matched_req_ordered = ["Python"] if matched_req else []
        req_ratio = 0.5
        req_score = req_ratio * 60.0

    # Preferred Skills Score (max 20)
    preferred_known = bool(pref_skills)
    if pref_skills:
        matched_pref = user_skills.intersection(pref_skills)
        pref_ratio = len(matched_pref) / len(pref_skills)
        pref_score = pref_ratio * 20.0
    else:
        # Neutral baseline if preferred skills unspecified
        pref_score = 15.0 if req_score >= 40 else 10.0

    # Role Relevance Score (max 10)
    role_score = 5.0
    candidate_roles = [r.lower() for r in user_profile.get("roles", ["Software Engineer Intern", "Backend Developer Intern", "AI ML Intern"])]
    title_lower = job_title.lower()
    if any(kw in title_lower for kw in ["software", "developer", "engineer", "intern", "backend", "full stack", "ai", "machine learning"]):
        role_score = 10.0
    elif any(r in title_lower for r in candidate_roles):
        role_score = 10.0

    # Location & Experience Compatibility (max 10)
    exp_loc_score = 5.0
    candidate_locs = [l.lower() for l in user_profile.get("locations", ["mumbai", "navi mumbai", "pune", "remote"])]
    if "remote" in job_location or any(loc in job_location for loc in candidate_locs):
        exp_loc_score += 3.0
    exp_level = str(job.get("experience_level", "")).lower()
    emp_type = str(job.get("employment_type", "")).lower()
    if "intern" in exp_level or "intern" in emp_type or "entry" in exp_level or "intern" in title_lower:
        exp_loc_score += 2.0
    exp_loc_score = min(10.0, exp_loc_score)

    total_score = int(round(req_score + pref_score + role_score + exp_loc_score))
    total_score = max(0, min(100, total_score))

    # Confidence ceiling: a score may only approach 100 when the posting
    # actually stated its requirements and preferred skills were verifiable.
    # Matching one skill (e.g. Python) alone must never produce Gold 100%.
    if not req_skills or required_inferred:
        total_score = min(total_score, 72)
    if not preferred_known:
        total_score = min(total_score, 88)
    if len(user_skills.intersection(req_skills)) <= 1 and req_skills:
        total_score = min(total_score, 84)
    # Any unverified requirement caps the score further.
    if missing_req and len(missing_req) > 2:
        total_score = min(total_score, 70)

    # Strict Tiers: Gold >= 60, Silver 40-59, Bronze < 40
    if total_score >= 60:
        tier = "Gold"
    elif total_score >= 40:
        tier = "Silver"
    else:
        tier = "Bronze"

    # Human-readable matched and missing skills
    ordered_raw = matched_req_ordered if matched_req_ordered else list(matched_req)
    matched_skills_display = display_skills(ordered_raw) or display_skills(["Python"])
    missing_skills_display = display_skills(missing_req)

    return {
        "score": total_score,
        "tier": tier,
        "required_skill_score": int(round(req_score)),
        "preferred_skill_score": int(round(pref_score)),
        "role_score": int(round(role_score)),
        "experience_location_score": int(round(exp_loc_score)),
        "matched_skills": matched_skills_display,
        "missing_skills": missing_skills_display,
        "is_eligible": total_score >= 35 and not job.get("is_expired", False)
    }


async def enrich_single_job(client: Optional[genai.Client], job: Dict[str, Any], user_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates, extracts semantic nuance, and calculates deterministic matching.
    Gemini is only used with supplied source content and never invents missing information.
    """
    raw_title = job.get("title", "")
    validated_title = clean_job_title(raw_title)

    # Rejection check for HTML contamination or listing title
    if not validated_title or is_html_contaminated(raw_title) or is_listing_page(raw_title):
        # Mark opportunity invalid if contaminated or listing
        job["is_valid_opportunity"] = False
        job["rejection_reason"] = "Title indicates listing page or HTML contamination"
        return job

    job["title"] = validated_title

    # Perform deterministic matching
    det_match = deterministic_score_opportunity(job, user_profile)
    job["match"] = det_match
    job["skill_analysis"] = {
        "matched": det_match["matched_skills"],
        "related": [],
        "missing": det_match["missing_skills"]
    }

    # If Gemini client is provided and description is available, perform strict semantic analysis
    if client and job.get("description_summary"):
        try:
            prompt = f"""You are a factual job and internship information extraction engine.
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
Accuracy is more important than completeness.

Candidate Skills: {', '.join(user_profile.get('skills', []))}
Job Title: {job.get('title')}
Company: {job.get('company')}
Description: {job.get('description_summary', '')[:400]}

Return strict JSON only:
{{
    "is_single_active_job": true/false,
    "missing_required_skills": [string]
}}"""

            def call_gemini():
                chat = client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
                return chat.send_message(prompt)

            import asyncio
            response = await asyncio.wait_for(asyncio.to_thread(call_gemini), timeout=3.0)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())

            if not parsed.get("is_single_active_job", True):
                job["is_valid_opportunity"] = False
                job["rejection_reason"] = "Gemini identified content as listing or inactive"

            ai_missing = parsed.get("missing_required_skills", [])
            if ai_missing and isinstance(ai_missing, list):
                # Update missing skills if Gemini extracted verified missing requirements
                cleaned_missing = [s.strip().capitalize() for s in ai_missing if s and len(s) < 30]
                if cleaned_missing:
                    det_match["missing_skills"] = list(set(det_match["missing_skills"] + cleaned_missing))
                    job["skill_analysis"]["missing"] = det_match["missing_skills"]

        except Exception as e:
            logger.debug("Gemini semantic analysis fallback: %s", e)

    return job