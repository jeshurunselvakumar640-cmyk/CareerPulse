from fastapi.responses import FileResponse
import os
import re
import json
import hmac
import logging
import asyncio
from datetime import datetime, timedelta
import zoneinfo
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from google import genai

from app.config import settings
from app.services.supabase_client import get_service_role_client
from app.services.tavily_search import search_live_content_via_tavily
from app.services.ai_matcher import enrich_single_job
from app.services.contact_lookup import lookup_company_contact
from app.services.email_service import send_email, verify_smtp_connection
from app.services.email_template import build_test_email_html
from app.services.personalized_news import (
    build_news_profile,
    get_personalized_news,
    invalidate_news_cache,
)
from app.services.daily_digest import (
    send_daily_digest,
    is_digest_already_sent_today,
    load_local_digest_history
)
from app.services import gmail_client
from app.services.gmail_client import MailboxError, MailboxNotConfigured
from app.services import reply_tracker
from app.services.reply_tracker import MigrationRequired
from app.services import resume_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("careerpulse.api")

app = FastAPI(
    title="CareerPulse API",
    description="Personal Career Opportunity Agent powered by FastAPI, Supabase, and Gemini",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase: Client = create_client(settings.supabase_url, settings.supabase_key)

# Mailbox tables are protected by Row Level Security, which rejects anon writes.
# The OAuth callback and reply sync therefore need a service-role client. It is
# used only inside these handlers and is never exposed to the browser.
mailbox_supabase: Optional[Client] = get_service_role_client()
if mailbox_supabase is None:
    logger.warning(
        "SUPABASE_SERVICE_ROLE_KEY is not set. Mailbox connections and replies "
        "use the anon key, so writes to RLS-protected tables will be rejected."
    )

# -------------------------------------------------------------------
# LIFECYCLE
# -------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    logger.info("Initializing CareerPulse services...")

# -------------------------------------------------------------------
# PYDANTIC SCHEMAS
# -------------------------------------------------------------------

class ProfileUpdateRequest(BaseModel):
    headline: Optional[str] = ""
    degree: Optional[str] = ""
    college: Optional[str] = ""
    year: Optional[str] = ""
    location: Optional[str] = ""
    github: Optional[str] = ""
    linkedin: Optional[str] = ""
    projects: Optional[list] = []
    skills: Optional[list] = []
    interests: Optional[list] = []
    preferred_roles: Optional[list] = []
    preferred_domains: Optional[list] = []
    experience_level: Optional[str] = ""

class OpportunityAction(BaseModel):
    title: str
    company: str
    location: Optional[str] = "Remote"
    matchPercentage: Optional[int] = 60
    tier: Optional[str] = "Gold"
    skills: Optional[list] = []
    lackingSkills: Optional[list] = []
    summary: Optional[str] = ""
    url: Optional[str] = ""
    status: str

    class Config:
        extra = "allow"

class OpportunityRemoveRequest(BaseModel):
    company: str
    title: str

    class Config:
        extra = "allow"

class ContactLookupRequest(BaseModel):
    company: str
    title: str

class EmailDraftRequest(BaseModel):
    title: str
    company: str
    summary: str
    recipient_email: str
    recipient_name: str = ""

class EmailSendRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    opportunity_id: str
    company: str
    title: str

class EmailTestRequest(BaseModel):
    to_email: Optional[str] = None

class DigestTriggerRequest(BaseModel):
    force: Optional[bool] = False

class ReplySyncRequest(BaseModel):
    force: Optional[bool] = False

class ReplyDraftRequest(BaseModel):
    opportunity_id: str

class ResumePersonalDetailsRequest(BaseModel):
    profile_picture_url: Optional[str] = ""
    name: Optional[str] = ""
    email: Optional[str] = ""
    date_of_birth: Optional[str] = ""
    gender: Optional[str] = ""
    linkedin_url: Optional[str] = ""
    github_url: Optional[str] = ""
    website_url: Optional[str] = ""
    address: Optional[str] = ""
    pincode: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = ""

class ResumeHeadlineRequest(BaseModel):
    headline: Optional[str] = ""

class ResumeTextRequest(BaseModel):
    value: Optional[str] = ""

class ResumeValueListRequest(BaseModel):
    values: Optional[List[str]] = []

class ResumeEducationRequest(BaseModel):
    id: Optional[str] = ""
    course_degree: Optional[str] = ""
    school_university: Optional[str] = ""
    grade_score: Optional[str] = ""
    currently_doing: Optional[bool] = False
    start_date: Optional[str] = ""
    end_date: Optional[str] = ""

class ResumeExperienceRequest(BaseModel):
    id: Optional[str] = ""
    company_name: Optional[str] = ""
    job_title: Optional[str] = ""
    currently_work_here: Optional[bool] = False
    employment_type: Optional[str] = ""
    start_date: Optional[str] = ""
    end_date: Optional[str] = ""
    details: Optional[str] = ""

class ResumeReferenceRequest(BaseModel):
    id: Optional[str] = ""
    referee_name: Optional[str] = ""
    job_title: Optional[str] = ""
    company_name: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""

class ResumeLinkItemRequest(BaseModel):
    """Shared shape for Projects and Publications (title / link / details)."""
    id: Optional[str] = ""
    title: Optional[str] = ""
    link: Optional[str] = ""
    details: Optional[str] = ""

class ResumeAssetRequest(BaseModel):
    kind: str
    data_url: Optional[str] = ""

class ResumeDeleteRequest(BaseModel):
    id: str

# -------------------------------------------------------------------
# AUTH & TOKEN HELPERS
# -------------------------------------------------------------------

def get_current_token_and_user(request: Request, authorization: Optional[str] = Header(None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ")[1]
    else:
        token = request.cookies.get("sb-access-token")

    if not token:
        return None, None

    try:
        user_res = supabase.auth.get_user(token)
        if not user_res or not user_res.user:
            return None, None
        return token, user_res.user
    except Exception:
        return None, None

def require_auth_token_and_user(request: Request, authorization: Optional[str] = Header(None)):
    token, user = get_current_token_and_user(request, authorization)
    if not token or not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid token. Please log in again.")
    return token, user

# -------------------------------------------------------------------
# HEALTH & AUTH ENDPOINTS
# -------------------------------------------------------------------

@app.get("/api/health")
async def health_check():
    return {"status": "online", "service": "CareerPulse API"}

@app.get("/api/supabase/health")
async def supabase_health():
    try:
        return {"status": "connected", "supabase_url": settings.supabase_url[:15] + "..."}
    except Exception as e:
        # Unauthenticated: keep the raw exception out of the response.
        logger.error("Supabase health check failed: %s", e)
        return {"status": "error", "detail": "Supabase health check failed."}

@app.get("/api/auth/linkedin")
async def linkedin_login(request: Request):
    try:
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/api/auth/callback"
       
        res = supabase.auth.sign_in_with_oauth({
            "provider": "linkedin_oidc",
            "options": {"redirect_to": redirect_uri}
        })
        return RedirectResponse(url=res.url)
    except Exception as e:
        # Matches the Google route: log the detail, return a generic message so
        # the raw exception is not echoed to an unauthenticated caller.
        logger.error("LinkedIn OAuth error: %s", e)
        raise HTTPException(status_code=500, detail="Unable to start LinkedIn login")

@app.get("/api/auth/google")
async def google_login(request: Request):
    try:
        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/api/auth/callback"

        res = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_uri
            }
        })

        return RedirectResponse(url=res.url)

    except Exception as e:
        logger.error("Google OAuth error: %s", str(e))
        raise HTTPException(status_code=500, detail="Unable to start Google login")

@app.get("/api/auth/callback")
async def auth_callback(request: Request, code: str):
    try:
        res = supabase.auth.exchange_code_for_session({"auth_code": code})
        session = res.session
        user = res.user

        is_new_user = True
        try:
            metadata = user.user_metadata or {} if user else {}
            skills = metadata.get("skills") or []
            if skills and len(skills) > 0:
                is_new_user = False
        except Exception:
            is_new_user = True

        redirect_hash = "profile" if is_new_user else "dashboard"
        response = RedirectResponse(url=f"/app#{redirect_hash}")
        response.set_cookie(
            key="sb-access-token",
            value=session.access_token,
            httponly=True,
            samesite="lax",
            # Matches the Gmail cookie below: only mark Secure on a real HTTPS
            # connection so local http://127.0.0.1 development still works.
            secure=str(request.url.scheme) == "https",
            path="/"
        )
        return response
    except Exception as e:
        logger.error("OAuth callback error: %s", str(e))
        return RedirectResponse(url="/?error=auth_failed")

@app.get("/api/auth/logout")
async def logout_get():
    response = RedirectResponse(url="/")
    response.delete_cookie(key="sb-access-token", path="/")
    return response

@app.post("/api/auth/logout")
async def logout_post(request: Request, authorization: Optional[str] = Header(None)):
    # Drop any cached personalized news for this user so a later sign-in
    # as the same account cannot surface another session's data.
    _, user = get_current_token_and_user(request, authorization)
    if user:
        try:
            invalidate_news_cache(user.id)
        except Exception as e:
            logger.warning("News cache invalidation on logout warning: %s", e)

    response = JSONResponse(content={"status": "success", "message": "Logged out successfully"})
    response.delete_cookie(key="sb-access-token", path="/")
    return response

@app.get("/api/user/profile")
async def get_user_profile(request: Request, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    
    empty_profile = {
        "name": "",
        "email": "",
        "profile_picture": "",
        "headline": "",
        "degree": "",
        "college": "",
        "year": "",
        "location": "",
        "experience": "",
        "skills": [],
        "interests": [],
        "preferred_roles": [],
        "preferred_domains": [],
        "experience_level": "",
        "github": "",
        "linkedin": "",
        "projects": []
    }

    if not user:
        return {"authenticated": False, "profile_complete": False, "profile": empty_profile}

    try:
        metadata = user.user_metadata or {}
        skills = metadata.get("skills") or []
        profile_complete = bool(skills and len(skills) > 0)
        return {
            "authenticated": True,
            "profile_complete": profile_complete,
            "profile": {
                "name": metadata.get("full_name") or metadata.get("name") or "",
                "email": user.email or "",
                "profile_picture": metadata.get("avatar_url") or metadata.get("picture") or "",
                "headline": metadata.get("headline") or "",
                "degree": metadata.get("degree") or "",
                "college": metadata.get("college") or "",
                "year": metadata.get("year") or "",
                "location": metadata.get("location") or "",
                "experience": metadata.get("experience") or "",
                "skills": skills,
                "interests": metadata.get("interests") or [],
                "preferred_roles": metadata.get("preferred_roles") or [],
                "preferred_domains": metadata.get("preferred_domains") or [],
                "experience_level": metadata.get("experience_level") or "",
                "github": metadata.get("github") or "",
                "linkedin": metadata.get("linkedin") or "",
                "projects": metadata.get("projects") or []
            }
        }
    except Exception as e:
        logger.error("Profile fetch error: %s", str(e))
        return {"authenticated": False, "profile_complete": False, "profile": empty_profile}

@app.post("/api/user/profile/update")
async def update_user_profile(req: ProfileUpdateRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        user_client = create_client(settings.supabase_url, settings.supabase_key)
        user_client.auth.set_session(access_token=token, refresh_token="")

        existing_metadata = user.user_metadata or {}
        updated_metadata = {
            **existing_metadata,
            "headline": req.headline,
            "degree": req.degree,
            "college": req.college,
            "year": req.year,
            "location": req.location,
            "github": req.github,
            "linkedin": req.linkedin,
            "projects": req.projects,
            "skills": req.skills,
            "interests": req.interests,
            "preferred_roles": req.preferred_roles,
            "preferred_domains": req.preferred_domains,
            "experience_level": req.experience_level
        }

        user_client.auth.update_user({
            "data": updated_metadata
        })

        # The personalized Tech News cache is keyed on the profile, so a profile
        # edit must invalidate it or the user would keep seeing stale news.
        try:
            invalidate_news_cache(user.id)
        except Exception as cache_err:
            logger.warning("News cache invalidation warning: %s", cache_err)

        return {"status": "success", "message": "Profile updated in Supabase successfully"}

    except Exception as e:
        error_msg = str(e)
        logger.error("Supabase profile update error: %s", error_msg)
        if "expired" in error_msg.lower() or "jwt" in error_msg.lower():
            raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {error_msg}")

# -------------------------------------------------------------------
# OPPORTUNITIES & PERSISTENCE
# -------------------------------------------------------------------

@app.get("/api/user/opportunities/saved")
async def get_saved_opportunities(request: Request, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    if not user:
        return {"status": "success", "saved": [], "waitlisted": []}
    try:
        res = supabase.table("user_saved_opportunities").select("*").eq("user_id", user.id).execute()
        rows = res.data or []
        
        interested = [r["opportunity_data"] for r in rows if r["status"] == "interested"]
        waitlisted = [r["opportunity_data"] for r in rows if r["status"] == "waitlisted"]
        return {"status": "success", "saved": interested, "waitlisted": waitlisted}
    except Exception as e:
        # Report the failure instead of an empty success: answering 200 with no
        # rows makes the dashboard clear a list that is still stored in the DB.
        logger.warning("Fetch saved error: %s", e)
        raise HTTPException(status_code=500, detail="Could not load saved opportunities.")

@app.post("/api/user/opportunities/save")
async def save_opportunity(action: OpportunityAction, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    try:
        opp_data = action.model_dump() if hasattr(action, "model_dump") else action.dict()
        payload = {
            "user_id": user.id,
            "opportunity_key": f"{action.company}_{action.title}".lower().replace(" ", "_"),
            "status": action.status,
            "opportunity_data": opp_data
        }
        supabase.table("user_saved_opportunities").upsert(payload, on_conflict="user_id,opportunity_key").execute()
        return {"status": "success"}
    except Exception as e:
        # A failed write must not answer 200: the browser would treat the card
        # as saved and the opportunity would be missing after a reload. The raw
        # exception is logged rather than returned.
        logger.error("Save opportunity error: %s", e)
        raise HTTPException(status_code=500, detail="Could not save this opportunity.")

@app.post("/api/user/opportunities/remove")
async def remove_saved_opportunity(action: OpportunityRemoveRequest, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    try:
        opp_key = f"{action.company}_{action.title}".lower().replace(" ", "_")
        supabase.table("user_saved_opportunities").delete().eq("user_id", user.id).eq("opportunity_key", opp_key).execute()
        return {"status": "success"}
    except Exception as e:
        logger.error("Remove opportunity error: %s", e)
        raise HTTPException(status_code=500, detail="Could not remove saved opportunity.")

@app.get("/api/user/profile/insights")
async def get_opportunity_insights(count: int = 50, request: Request = None, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization) if request else (None, None)
    
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to fetch opportunity insights.")

    metadata = user.user_metadata or {}
    user_profile = {
        "skills": metadata.get("skills", []),
        "locations": [metadata.get("location")] if metadata.get("location") else ["Remote"],
        "roles": [metadata.get("headline")] if metadata.get("headline") else ["Software Engineer"]
    }

    raw_data = await asyncio.to_thread(search_live_content_via_tavily, user_profile, 10)
    raw_jobs = raw_data.get("jobs", [])
    
    seen_fingerprints = set()
    unique_jobs = []
    for job in raw_jobs:
        fp = f"{job.get('company','').lower()}_{job.get('title','').lower()}"
        if fp not in seen_fingerprints:
            seen_fingerprints.add(fp)
            unique_jobs.append(job)

    client_gen = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
    enrichment_tasks = [enrich_single_job(client_gen, job, user_profile) for job in unique_jobs[:count]]
    enriched_opportunities = await asyncio.gather(*enrichment_tasks)

    valid_opportunities = [j for j in enriched_opportunities if j.get("is_valid_opportunity", True)]
    verified_news = raw_data.get("news", [])[:6]

    return {
        "status": "success",
        "insights": {
            "insight_text": f"Found {len(valid_opportunities)} strictly verified individual openings matching candidate technical stack.",
            "best_role": user_profile["roles"][0] if user_profile["roles"] else "Software Engineer",
            "top_skill": user_profile["skills"][0] if user_profile["skills"] else "Software Engineering",
            "growing_skill": user_profile["skills"][1] if len(user_profile["skills"]) > 1 else "API Development",
            "recommended": "Full Stack Engineering"
        },
        "opportunities": valid_opportunities,
        "daily_news": verified_news
    }

# -------------------------------------------------------------------
# PERSONALIZED TECHNOLOGY NEWS
# -------------------------------------------------------------------

@app.get("/api/user/news/personalized")
async def get_personalized_tech_news(
    limit: int = 10,
    refresh: bool = False,
    auth_tuple=Depends(require_auth_token_and_user),
):
    """
    Returns technology news personalized to the CURRENT profile of the
    signed-in user (skills, interests, preferred roles, preferred domains,
    experience level). Every article is retrieved from its actual source
    page and verified before it is returned.
    """
    _, user = auth_tuple
    limit = max(1, min(int(limit or 10), 10))

    profile = build_news_profile(user.user_metadata or {}, user_id=user.id)
    result = await get_personalized_news(
        profile,
        limit=limit,
        refresh=bool(refresh),
        # user_news_cache is RLS-protected, so the anon client cannot read or
        # write it. Reuse the same server-side service-role client the mailbox
        # handlers use. It bypasses RLS and is never returned to the browser.
        # Falls back to the default client when no service-role key is set.
        supabase_client=mailbox_supabase or supabase,
    )
    return result

# -------------------------------------------------------------------
# CONTACT LOOKUP & EMAIL GENERATION
# -------------------------------------------------------------------

@app.post("/api/opportunity/contact-lookup")
async def opportunity_contact_lookup(req: ContactLookupRequest,
                                    auth_tuple=Depends(require_auth_token_and_user)):
    # Requires a signed-in caller: this runs a paid Tavily search with the
    # server's API key, so leaving it open lets anyone spend that quota.
    _token, _user = auth_tuple
    data = lookup_company_contact(req.company, req.title)
    return {"status": "success", "contact": data}

@app.post("/api/email/generate")
async def generate_email_draft(req: EmailDraftRequest, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    m = user.user_metadata or {}
    
    user_data = {
        "name": m.get("full_name") or m.get("name") or "",
        "degree": m.get("degree") or "",
        "college": m.get("college") or "",
        "year": m.get("year") or "",
        "skills": m.get("skills") or [],
        "projects": m.get("projects") or [],
        "github": m.get("github") or "",
        "linkedin": m.get("linkedin") or "",
        "location": m.get("location") or ""
    }

    recipient_salutation = f"Dear {req.recipient_name}," if req.recipient_name else "Dear Hiring Team,"

    prompt = f"""You are a professional career communication assistant.
Draft a concise, professional, personalized email to a company requesting consideration for an internship or job opportunity and, if appropriate, an interview.
Use ONLY the information provided below. Do not invent or exaggerate any skills, projects, achievements, experience, company information, recruiter names, or qualifications.

Candidate Information:
Name: {user_data['name']}
Degree/Program: {user_data['degree']}
College/University: {user_data['college']}
Year/Semester: {user_data['year']}
Skills: {', '.join(user_data['skills'])}
Relevant Projects: {', '.join(user_data['projects'])}
GitHub Profile: {user_data['github']}
LinkedIn Profile: {user_data['linkedin']}
Preferred Role: {req.title}
Location: {user_data['location']}

Company Information:
Company: {req.company}
Recipient Name: {req.recipient_name or 'N/A'}
Position: {req.title}
Job/Internship Description: {req.summary}

Email requirements:
- Write a clear and professional subject line.
- Address the recipient as: "{recipient_salutation}".
- Start with a brief introduction containing candidate's name, degree, college, and current year.
- Clearly state purpose: requesting consideration for specified role.
- Mention 2-3 skills/projects genuinely relevant to role.
- Explain briefly why candidate's background is relevant.
- Politely express interest in discussing the opportunity in an interview.
- Mention portfolio links (GitHub: {user_data['github']}, LinkedIn: {user_data['linkedin']}).
- Keep email length strictly between 120 and 180 words.
- Use professional, natural, human language.
- End with a polite call to action and professional sign-off with candidate's contact details.

Return ONLY strict JSON with keys "subject" and "body"."""

    try:
        client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
        if client:
            chat = client.chats.create(model=settings.primary_model or "gemini-2.5-flash")
            response = await asyncio.to_thread(chat.send_message, prompt)
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            return {"status": "success", "subject": parsed.get("subject"), "body": parsed.get("body")}
    except Exception as e:
        logger.error("Email draft generation error: %s", e)

    fallback_subject = f"Application for {req.title} - {user_data['name']}"
    skills_str = ', '.join(user_data['skills'][:3]) if user_data['skills'] else "software engineering"
    project_str = user_data['projects'][0] if user_data['projects'] else "academic projects"

    fallback_body = f"""{recipient_salutation}

My name is {user_data['name']}, a {user_data['year']} student pursuing {user_data['degree']} at {user_data['college']}. I am writing to express my strong interest in the {req.title} position at {req.company}.

Through my academic coursework and hands-on projects including {project_str}, I have built practical skills in {skills_str}. My technical background aligns well with the requirements for this role.

I am eager to bring this foundation to your engineering team. You can review my work at my GitHub ({user_data['github']}) and LinkedIn ({user_data['linkedin']}).

Thank you for your time and consideration. I would welcome the opportunity to discuss how my technical skills align with your needs in a brief interview. Please let me know if you are available for a conversation.

Sincerely,

{user_data['name']}
{user_data['degree']} | {user_data['college']}
{user_data['location']}
GitHub: {user_data['github']}
LinkedIn: {user_data['linkedin']}"""

    return {"status": "success", "subject": fallback_subject, "body": fallback_body}

@app.post("/api/email/send")
async def send_outreach_email(req: EmailSendRequest, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    if not re.match(email_regex, req.to_email):
        raise HTTPException(status_code=400, detail="Invalid recipient email address.")

    if not req.subject or not req.subject.strip():
        raise HTTPException(status_code=400, detail="Subject line cannot be empty.")
        
    if not req.body or not req.body.strip():
        raise HTTPException(status_code=400, detail="Email body content cannot be empty.")

    try:
        sent_message_id = send_email(req.to_email, req.subject, req.body)

        # Durable record of the outreach email. The existing email_logs table is
        # reused (it already stores user_id, opportunity_id, recipient_email,
        # subject, body, status, sent_at) and extended with the message id and
        # provider thread fields so a company reply can be matched later.
        reply_tracker.record_sent_email(
            supabase=_mailbox_db(),
            user_id=user.id,
            opportunity_id=req.opportunity_id,
            recipient_email=req.to_email,
            sender_email=settings.smtp_user,
            subject=req.subject,
            body=req.body,
            message_id=sent_message_id or "",
            company=req.company or "",
            title=req.title or "",
        )

        return {"status": "success", "message": "Email sent successfully"}
    except Exception as e:
        # SMTP exceptions can carry host/transport detail, so it is logged
        # rather than echoed back to the browser.
        logger.error("Email send error: %s", e)
        raise HTTPException(status_code=500, detail="Unable to send the email right now.")

@app.api_route("/api/email/test", methods=["GET", "POST"])
async def email_test(req: Optional[EmailTestRequest] = None,
                     auth_tuple=Depends(require_auth_token_and_user)):
    # Requires a signed-in caller. Without this, anyone who could reach the API
    # could make the server send mail to an address of their choosing, which
    # turns the SMTP credential into an open relay and can get the sending
    # domain blacklisted.
    _token, _user = auth_tuple
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    if req and req.to_email:
        if not re.match(email_regex, req.to_email):
            raise HTTPException(status_code=400, detail="Invalid test email address format.")
        try:
            send_email(
                to_email=req.to_email,
                subject="CareerPulse — HTML Email Delivery Test",
                body=(
                    "CareerPulse SMTP Test\n\n"
                    "HTML email delivery is working successfully.\n\n"
                    "This message was sent as multipart/alternative with an HTML part "
                    "and a plain-text fallback."
                ),
                html_body=build_test_email_html("CareerPulse — HTML Email Delivery Test"),
            )
            return {"status": "success", "message": f"Test email sent successfully to {req.to_email}"}
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            # The raw SMTP exception can carry host/credential detail, so it is
            # logged server-side and only a generic message is returned.
            logger.error("SMTP test dispatch failed: %s", e)
            raise HTTPException(status_code=500, detail="SMTP dispatch failed. Please try again.")

    smtp_status = verify_smtp_connection()
    if smtp_status.get("status") == "success":
        return {"status": "success", "message": "SMTP transport operational."}
    else:
        raise HTTPException(status_code=500, detail=smtp_status.get("message", "SMTP connection failed."))

# -------------------------------------------------------------------
# MAILBOX INTEGRATION & COMPANY REPLIES
# -------------------------------------------------------------------
# Reading replies needs a real mailbox connector: SMTP only sends. CareerPulse
# asks the user for a read-only Gmail grant and stores the resulting tokens
# encrypted on the server. No token ever reaches the browser.

GMAIL_OAUTH_COOKIE = "cp-gmail-oauth"
GMAIL_REDIRECT_COOKIE = "cp-gmail-redirect"
GMAIL_OAUTH_MAX_AGE = 600


def _mailbox_db() -> Client:
    """
    Database handle for mailbox and reply tables.

    These tables are protected by Row Level Security, which rejects anon writes.
    The service-role client bypasses RLS and is used server-side only; it never
    reaches the browser. Falls back to the default client when no service-role
    key is configured.
    """
    return mailbox_supabase or supabase

# Provider-safe messages for the UI. Raw OAuth/API errors are never returned.
_MAILBOX_ERROR_MESSAGES = {
    "not_configured": "Gmail integration is not configured on this server yet.",
    "auth_error": "Your Gmail connection needs to be renewed. Please reconnect Gmail.",
    "rate_limited": "Gmail is busy right now. Please try again in a few minutes.",
    "provider_error": "Gmail could not be reached. Please try again shortly.",
    "mailbox_error": "The mailbox could not be read. Please try again shortly.",
    "not_connected": "Connect your Gmail account to receive company replies.",
    "migration_required": "Reply tracking is not available until the database migration is applied.",
}

def _mailbox_error_detail(code: str, fallback: str = "") -> str:
    return _MAILBOX_ERROR_MESSAGES.get(code, fallback or _MAILBOX_ERROR_MESSAGES["mailbox_error"])

def _mailbox_error_status(code: str) -> int:
    if code == "not_connected":
        return 409
    if code == "not_configured":
        return 503
    if code == "auth_error":
        return 401
    if code == "migration_required":
        return 503
    if code == "rate_limited":
        return 429
    return 502

def _gmail_redirect_uri(request: Request) -> str:
    """
    Resolve the OAuth redirect URI.

    A configured value always wins. Otherwise it is derived from the live host,
    honouring the forwarded scheme explicitly: on Vercel the app sits behind a
    proxy and uvicorn only trusts forwarded headers from loopback, so
    `request.base_url` can silently downgrade to `http://`. Google requires the
    redirect URI to match the authorize request character for character.
    """
    if settings.gmail_redirect_uri:
        return settings.gmail_redirect_uri

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    if forwarded_proto in ("http", "https"):
        host = forwarded_host or request.url.netloc
        return f"{forwarded_proto}://{host}/api/email/integration/callback"

    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/email/integration/callback"

@app.get("/api/email/integration/status")
async def email_integration_status(auth_tuple=Depends(require_auth_token_and_user)):
    """Render state only: connection flags, mailbox address, scopes, sync time."""
    _, user = auth_tuple
    status = reply_tracker.mailbox_integration_status(_mailbox_db(), user.id)
    status["unread_replies"] = reply_tracker.count_unread_replies(_mailbox_db(), user.id)
    return {"status": "success", **status}

@app.get("/api/email/integration/connect")
async def email_integration_connect(request: Request, auth_tuple=Depends(require_auth_token_and_user)):
    """Start Google OAuth. The user is sent to Google's consent screen."""
    _, user = auth_tuple

    if not gmail_client.is_mailbox_oauth_configured():
        raise HTTPException(
            status_code=503,
            detail=_mailbox_error_detail("not_configured"),
        )

    redirect_uri = _gmail_redirect_uri(request)
    # The state carries the user id under an HMAC signature so the callback can
    # still identify the user if the session cookie is missing on the way back.
    signed_state = gmail_client.build_signed_state(user.id)
    try:
        url = gmail_client.build_authorization_url(signed_state, redirect_uri)
    except MailboxError as e:
        raise HTTPException(status_code=503, detail=_mailbox_error_detail(getattr(e, "code", "")))

    response = RedirectResponse(url=url)
    # httpOnly + SameSite=Lax: the state is not readable by page scripts and
    # the cookie is returned on Google's top-level redirect back to us.
    response.set_cookie(
        key=GMAIL_OAUTH_COOKIE,
        value=signed_state,
        httponly=True,
        samesite="lax",
        secure=str(request.url.scheme) == "https",
        max_age=GMAIL_OAUTH_MAX_AGE,
        path="/",
    )
    # Reuse the exact redirect URI that was sent to Google, so the token
    # exchange repeats it byte for byte.
    response.set_cookie(
        key=GMAIL_REDIRECT_COOKIE,
        value=redirect_uri,
        httponly=True,
        samesite="lax",
        secure=str(request.url.scheme) == "https",
        max_age=GMAIL_OAUTH_MAX_AGE,
        path="/",
    )
    return response

@app.get("/api/email/integration/callback")
async def email_integration_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """
    Google OAuth redirect target.

    The user identity is re-derived from the existing session cookie, never
    from anything the provider sends, so one account can never attach another
    account's mailbox.

    The session cookie is written once at login and browsers withhold cookies on
    some cross-site redirects, so a live session can be unavailable by the time
    Google redirects back. The signed state carries the same user id and is
    HMAC-verified, which keeps CSRF protection without depending on the cookie.
    """
    token, user = get_current_token_and_user(request, None)
    state_payload = gmail_client.read_signed_state(state) if state else None

    # Lifecycle trace. Only shapes and booleans are logged: no token, code,
    # secret or state value is ever written.
    logger.info(
        "Gmail OAuth callback received: code_present=%s code_length=%d "
        "state_present=%s state_length=%d provider_error=%s "
        "session_cookie_present=%s session_resolved=%s signed_state_valid=%s",
        bool(code), len(code or ""), bool(state), len(state or ""), error or "(none)",
        bool(request.cookies.get("sb-access-token")),
        bool(user),
        bool(state_payload),
    )

    if not user and state_payload:
        logger.info(
            "Resolving the Gmail connection from a signed OAuth state because "
            "the session was unavailable on the redirect back."
        )
        user_id = str(state_payload["uid"])
    elif user:
        user_id = user.id
    else:
        logger.warning(
            "Gmail OAuth callback aborted: no authenticated session and no valid "
            "signed state, so there is no user to attach the mailbox to."
        )
        return RedirectResponse(url="/?error=signin_required")

    logger.info(
        "User resolution for the Gmail callback: source=%s user_id_present=%s.",
        "session" if user else ("signed_state" if state_payload else "none"),
        bool(user_id),
    )

    # With a live session the cookie must still match: it proves this browser
    # started the flow. Without a session the signed state is the only proof.
    if user is not None:
        stored_state = request.cookies.get(GMAIL_OAUTH_COOKIE)
        if not state or not stored_state or not hmac.compare_digest(state, stored_state):
            logger.warning(
                "Rejected a Gmail OAuth callback: state cookie missing or does not "
                "match the returned state (cookie_present=%s state_present=%s).",
                bool(stored_state), bool(state),
            )
            return RedirectResponse(url="/app#applications&mailbox=state_mismatch")

    if error:
        logger.info("Gmail authorization was not completed: %s", error)
        return RedirectResponse(url="/app#applications&mailbox=denied")

    if not code:
        logger.warning("Gmail OAuth callback arrived without an authorization code.")
        return RedirectResponse(url="/app#applications&mailbox=missing_code")

    # The redirect URI stored at connect time is the exact string Google
    # received on the authorize request. Reusing it guarantees the token
    # request repeats it byte for byte.
    redirect_uri = request.cookies.get(GMAIL_REDIRECT_COOKIE) or _gmail_redirect_uri(request)
    logger.info(
        "Gmail OAuth callback: exchanging the authorization code "
        "(redirect_uri_source=%s, code_length=%d).",
        "cookie" if request.cookies.get(GMAIL_REDIRECT_COOKIE) else "derived",
        len(code or ""),
    )

    try:
        bundle = gmail_client.exchange_authorization_code(code, redirect_uri)
        # A grant whose Gmail identity could not be verified is not a usable
        # connection: never persist it and never report it as connected.
        if not (bundle.mailbox_email or "").strip():
            logger.warning(
                "Gmail OAuth token exchange succeeded but returned no mailbox "
                "address; the connection was not saved."
            )
            return RedirectResponse(url="/app#applications&mailbox=email_unresolved")

        logger.info(
            "Gmail OAuth token exchange succeeded: mailbox_resolved=%s "
            "has_refresh_token=%s token_expires_at_set=%s granted_scopes_present=%s.",
            bool(bundle.mailbox_email),
            bool(bundle.refresh_token),
            bundle.expires_at is not None,
            bool(bundle.scopes),
        )

        reply_tracker.save_connection(
            supabase=_mailbox_db(),
            user_id=user_id,
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=bundle.expires_at,
            scopes=bundle.scopes,
            mailbox_email=bundle.mailbox_email,
        )
        logger.info(
            "save_connection() returned without raising: the mailbox connection "
            "for this user is stored and the callback is succeeding."
        )
    except MailboxNotConfigured as e:
        logger.error(
            "Gmail OAuth callback failed at configuration check: %s", e
        )
        return RedirectResponse(url="/app#applications&mailbox=not_configured")
    except MigrationRequired as e:
        logger.error(
            "Gmail OAuth callback could not persist the connection because the "
            "reply tracking migration has not been applied: %s", e
        )
        return RedirectResponse(url="/app#applications&mailbox=migration_required")
    except MailboxError as e:
        code_name = getattr(e, "code", "mailbox_error")
        provider_code = getattr(e, "provider_code", "") or "(none)"
        # The detail is logged for the operator only. The browser receives the
        # error code, never this text.
        logger.error(
            "Gmail OAuth callback failed before the connection was stored "
            "(error_code=%s, google_error_code=%s, exception=%s): %s",
            code_name, provider_code, type(e).__name__, e,
        )
        return RedirectResponse(url=f"/app#applications&mailbox={code_name}")
    except Exception as e:
        # Anything unexpected still has to be visible: a silent 500 here is what
        # leaves a completed OAuth flow with no stored connection.
        logger.exception(
            "Gmail OAuth callback raised an unexpected error (exception=%s): %s",
            type(e).__name__, e,
        )
        return RedirectResponse(url="/app#applications&mailbox=provider_error")

    logger.info(
        "Gmail OAuth callback completed: connection stored, redirecting to the "
        "dashboard."
    )
    response = RedirectResponse(url="/app#applications&mailbox=connected")
    response.delete_cookie(key=GMAIL_OAUTH_COOKIE, path="/")
    response.delete_cookie(key=GMAIL_REDIRECT_COOKIE, path="/")
    return response

@app.post("/api/email/integration/disconnect")
async def email_integration_disconnect(auth_tuple=Depends(require_auth_token_and_user)):
    """Remove the stored mailbox connection and its tokens for this user."""
    _, user = auth_tuple
    removed = reply_tracker.disconnect_mailbox(_mailbox_db(), user.id)
    return {"status": "success", "disconnected": removed}

@app.post("/api/email/sync")
async def sync_email_replies(req: Optional[ReplySyncRequest] = None, auth_tuple=Depends(require_auth_token_and_user)):
    """Check the connected mailbox for new company replies to tracked applications."""
    _, user = auth_tuple
    force = bool(req.force) if req else False
    result = await reply_tracker.sync_replies(_mailbox_db(), user.id, force=force)

    if result.get("status") == "error":
        error_code = result.get("error_code") or "mailbox_error"
        raise HTTPException(status_code=_mailbox_error_status(error_code), detail=result.get("message") or _mailbox_error_detail(error_code))
    return result

@app.get("/api/email/replies")
async def list_email_replies(
    limit: int = 50,
    unread_only: bool = False,
    auth_tuple=Depends(require_auth_token_and_user),
):
    """Replies linked to the current user's applications. Ownership is server-side."""
    _, user = auth_tuple
    replies = reply_tracker.list_replies(_mailbox_db(), user.id, limit=limit, unread_only=unread_only)
    return {
        "status": "success",
        "replies": replies,
        "unread_count": reply_tracker.count_unread_replies(_mailbox_db(), user.id),
    }

@app.get("/api/email/replies/{reply_id}")
async def get_email_reply(reply_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    reply = reply_tracker.get_reply(_mailbox_db(), user.id, reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found.")
    return {"status": "success", "reply": reply}

@app.post("/api/email/replies/{reply_id}/read")
async def mark_email_reply_read(reply_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    if not reply_tracker.mark_reply_read(_mailbox_db(), user.id, reply_id):
        raise HTTPException(status_code=404, detail="Reply not found.")
    return {
        "status": "success",
        "unread_count": reply_tracker.count_unread_replies(_mailbox_db(), user.id),
    }

@app.get("/api/applications")
async def list_applications(auth_tuple=Depends(require_auth_token_and_user)):
    """Application communication list: sent, replied, interview requested, etc."""
    _, user = auth_tuple
    applications = reply_tracker.list_applications(_mailbox_db(), user.id)
    return {
        "status": "success",
        "applications": applications,
        "unread_replies": reply_tracker.count_unread_replies(_mailbox_db(), user.id),
    }

@app.get("/api/opportunities/{opportunity_id}/conversation")
async def get_opportunity_conversation(opportunity_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    """Full chronological conversation for one opportunity of this user."""
    _, user = auth_tuple
    return reply_tracker.get_conversation(_mailbox_db(), user.id, opportunity_id)

@app.post("/api/email/draft-response")
async def draft_email_response(req: ReplyDraftRequest, auth_tuple=Depends(require_auth_token_and_user)):
    """
    Produce an editable draft response to the latest company reply.

    Nothing is sent. The user reviews the draft and explicitly sends it through
    the existing email composer.
    """
    _, user = auth_tuple
    if not req.opportunity_id:
        raise HTTPException(status_code=400, detail="An opportunity id is required.")
    result = await reply_tracker.draft_reply_response(
        _mailbox_db(), user.id, req.opportunity_id, user.user_metadata or {}
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("detail", "Nothing to respond to yet."))
    return result

# -------------------------------------------------------------------
# DAILY DIGEST ENDPOINTS
# -------------------------------------------------------------------

@app.get("/api/digest/status")
async def get_digest_status(auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    already_sent = is_digest_already_sent_today(user.id, user.email)
    local_history = load_local_digest_history()
    
    return {
        "status": "success",
        "already_sent_today": already_sent,
        "last_sent_timestamp": local_history.get("last_sent_timestamp"),
        "recent_records": local_history.get("sent_records", [])[-5:]
    }

@app.api_route("/api/digest/send", methods=["GET", "POST"])
async def trigger_digest_send(
    request: Request,
    req: Optional[DigestTriggerRequest] = None,
    force: Optional[bool] = None,
    authorization: Optional[str] = Header(None)
):
    """
    Triggers the 24-hour daily technology & career opportunity digest.
    Directly callable by Vercel Cron (HTTP GET) and frontend manual trigger (HTTP POST).
    """
    logger.info("digest: /api/digest/send endpoint invoked (method=%s)", request.method)

    token, user = get_current_token_and_user(request, authorization)
    force_flag = (req.force if req and req.force is not None else False) or (force is True)

    if user:
        metadata = user.user_metadata or {}
        user_profile = {
            "user_id": user.id,
            "name": metadata.get("full_name") or metadata.get("name") or "User",
            "email": user.email,
            "skills": metadata.get("skills", []),
            "locations": [metadata.get("location")] if metadata.get("location") else ["Remote"],
            "roles": [metadata.get("headline")] if metadata.get("headline") else ["Software Engineer"]
        }
        logger.info("digest: digest generation started for authenticated user=%s", user.id)
        result = await send_daily_digest(user_profile, force=force_flag)
        logger.info("digest: final response status: %s", result.get("status"))
        return result

    # Vercel Cron / automated dispatch (no user session)
    logger.info("digest: automated cron dispatch invoked")
    profiles = []
    try:
        client_to_use = mailbox_supabase or supabase
        res = client_to_use.table("resume_profiles").select("*").execute()
        profiles = res.data or []
    except Exception as e:
        logger.warning("digest: could not query resume_profiles: %s", e)

    if profiles:
        results = []
        for p in profiles:
            p_profile = {
                "user_id": p.get("user_id") or p.get("id") or "candidate_default_user",
                "name": p.get("name") or "Jeshurun Selvakumar",
                "email": p.get("email") or settings.recipient_email or settings.smtp_user,
                "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
                "locations": [p.get("city") or "Mumbai", "Remote"],
                "roles": [p.get("headline") or "Software Engineer Intern"]
            }
            if not p_profile["email"]:
                continue
            logger.info("digest: digest generation started for candidate=%s", p_profile["email"])
            r = await send_daily_digest(p_profile, force=force_flag)
            results.append(r)

        final_status = "success" if any(r.get("status") == "success" for r in results) else (results[0].get("status") if results else "success")
        logger.info("digest: final response status: %s", final_status)
        return results[0] if len(results) == 1 else {"status": final_status, "results": results}

    default_profile = {
        "user_id": "candidate_default_user",
        "name": "Jeshurun Selvakumar",
        "email": settings.recipient_email or settings.smtp_user or "jeshurunselvakumar640@gmail.com",
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "locations": ["Mumbai", "Navi Mumbai", "Remote"],
        "roles": ["Software Engineer Intern", "Backend Developer Intern", "AI ML Intern"]
    }
    logger.info("digest: digest generation started for default candidate profile")
    result = await send_daily_digest(default_profile, force=force_flag)
    logger.info("digest: final response status: %s", result.get("status"))
    return result

# -------------------------------------------------------------------
# RESUME BUILDER
# -------------------------------------------------------------------
# Ownership is derived from the validated Supabase session, never from the
# request body: the browser has no way to name another user. Every query runs
# through a client carrying the caller's JWT so the RLS policies in
# supabase/migrations/20261003_resume_builder.sql are the enforcement boundary.

#: Simple free-text resume fields saved through one endpoint.
_RESUME_TEXT_FIELDS = {"summary", "additional_information"}


def _resume_client(token: str) -> Client:
    """Supabase client bound to the caller's session token."""
    user_client = create_client(settings.supabase_url, settings.supabase_key)
    user_client.auth.set_session(access_token=token, refresh_token="")
    return user_client


def _resume_defaults(user) -> Dict[str, Any]:
    """One-time seed for a brand-new resume, taken from the existing profile.

    Copies rather than links: the resume becomes independently editable from
    this point, and later profile edits do not write through into it.
    """
    metadata = user.user_metadata or {}
    return {
        "name": metadata.get("full_name") or metadata.get("name") or "",
        "email": user.email or "",
        "profile_picture_url": metadata.get("avatar_url") or metadata.get("picture") or "",
        "headline": metadata.get("headline") or "",
        "linkedin_url": metadata.get("linkedin") or "",
        "github_url": metadata.get("github") or "",
    }


def _require_resume(client, user) -> Dict[str, Any]:
    profile = resume_service.get_or_create_resume(
        client, user.id, defaults=_resume_defaults(user)
    )
    if not profile:
        raise HTTPException(
            status_code=503,
            detail="Resume storage is unavailable. Apply the resume migration first.",
        )
    return profile


@app.get("/api/resume")
async def get_resume(auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        client = _resume_client(token)
        profile = _require_resume(client, user)
        children = resume_service.load_child_records(client, profile["id"])

        payload: Dict[str, Any] = dict(profile)
        # The column has a database default, but normalise it here so the
        # response shape is stable even for a row written before the column
        # existed (or with an explicit NULL).
        try:
            payload["total_experience_months"] = int(profile.get("total_experience_months") or 0)
        except (TypeError, ValueError):
            payload["total_experience_months"] = 0
        # The stored values are storage paths. Hand the browser short-lived
        # signed URLs for rendering and keep the paths round-trippable.
        payload["profile_picture_display"] = resume_service.sign_asset_url(
            client, profile.get("profile_picture_url")
        )
        payload["signature_display"] = resume_service.sign_asset_url(
            client, profile.get("signature_url")
        )
        payload["total_experience_display"] = resume_service.format_experience(
            profile.get("total_experience_months")
        )
        payload["complete"] = resume_service.is_resume_complete(profile)

        for section, rows in children.items():
            if section in resume_service.VALUE_LIST_SECTIONS:
                payload[section] = [row.get("value") for row in rows]
            else:
                payload[section] = rows

        return {"status": "success", "resume": payload}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume load error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to load resume: {e}")


@app.post("/api/resume/personal-details")
async def save_resume_personal_details(req: ResumePersonalDetailsRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        payload = {
            "name": resume_service.require_text(req.name, "Name", 200),
            "email": resume_service.validate_email(req.email, required=True),
            "profile_picture_url": resume_service.optional_text(req.profile_picture_url, 500),
            "date_of_birth": resume_service.optional_text(req.date_of_birth, 40) or None,
            "gender": resume_service.optional_text(req.gender, 60),
            "linkedin_url": resume_service.optional_text(req.linkedin_url, 500),
            "github_url": resume_service.optional_text(req.github_url, 500),
            "website_url": resume_service.optional_text(req.website_url, 500),
            "address": resume_service.optional_text(req.address, 500),
            "pincode": resume_service.optional_text(req.pincode, 20),
            "city": resume_service.optional_text(req.city, 120),
            "state": resume_service.optional_text(req.state, 120),
            "country": resume_service.optional_text(req.country, 120),
        }
        client = _resume_client(token)
        profile = _require_resume(client, user)
        client.table(resume_service.RESUME_TABLE).update(payload).eq("id", profile["id"]).execute()
        return {"status": "success", "message": "Personal details saved"}

    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume personal details save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save personal details: {e}")


@app.post("/api/resume/headline")
async def save_resume_headline(req: ResumeHeadlineRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        headline = resume_service.require_text(req.headline, "Headline / Designation", 200)
        client = _resume_client(token)
        profile = _require_resume(client, user)
        client.table(resume_service.RESUME_TABLE).update({"headline": headline}).eq("id", profile["id"]).execute()
        return {"status": "success", "message": "Headline saved"}
    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume headline save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save headline: {e}")


@app.post("/api/resume/text/{field}")
async def save_resume_text_field(field: str, req: ResumeTextRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    if field not in _RESUME_TEXT_FIELDS:
        raise HTTPException(status_code=400, detail="Unsupported resume text field.")
    try:
        client = _resume_client(token)
        profile = _require_resume(client, user)
        client.table(resume_service.RESUME_TABLE).update(
            {field: resume_service.optional_text(req.value, 8000)}
        ).eq("id", profile["id"]).execute()
        return {"status": "success", "message": f"{field.replace('_', ' ').title()} saved"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume text save error (%s): %s", field, e)
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")


@app.post("/api/resume/items/{section}")
async def save_resume_items(section: str, req: ResumeValueListRequest, auth_tuple=Depends(require_auth_token_and_user)):
    """Replace a whole single-column list (skills, hobbies, awards, ...)."""
    token, user = auth_tuple
    if section not in resume_service.VALUE_LIST_SECTIONS:
        raise HTTPException(status_code=400, detail="Unsupported resume item list.")
    try:
        values = resume_service.decode_json_list(req.values)
        client = _resume_client(token)
        profile = _require_resume(client, user)
        resume_service.replace_value_list(client, profile["id"], section, values)
        return {"status": "success", "message": f"{section.title()} saved", "values": values}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume list save error (%s): %s", section, e)
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")


@app.post("/api/resume/education")
async def save_resume_education(req: ResumeEducationRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        start_date, end_date = resume_service.validate_date_pair(
            req.start_date, req.end_date, bool(req.currently_doing)
        )
        payload = {
            "course_degree": resume_service.require_text(req.course_degree, "Course / Degree", 200),
            "school_university": resume_service.require_text(req.school_university, "School / University", 200),
            "grade_score": resume_service.optional_text(req.grade_score, 120),
            "currently_doing": bool(req.currently_doing),
            "start_date": start_date,
            "end_date": end_date,
        }

        client = _resume_client(token)
        profile = _require_resume(client, user)
        table = resume_service.CHILD_TABLES["education"]

        if req.id:
            # Scoped to this resume as well as the id, so an id belonging to
            # another account can never be touched.
            client.table(table).update(payload).eq("id", req.id).eq("resume_id", profile["id"]).execute()
        else:
            existing = resume_service._rows(
                client.table(table).select("id").eq("resume_id", profile["id"]).execute()
            )
            payload["resume_id"] = profile["id"]
            payload["sort_order"] = len(existing)
            client.table(table).insert(payload).execute()

        return {"status": "success", "message": "Education saved"}

    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume education save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save education: {e}")


@app.post("/api/resume/experience")
async def save_resume_experience(req: ResumeExperienceRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        start_date, end_date = resume_service.validate_date_pair(
            req.start_date, req.end_date, bool(req.currently_work_here)
        )
        payload = {
            "company_name": resume_service.require_text(req.company_name, "Company Name", 200),
            "job_title": resume_service.require_text(req.job_title, "Job Title", 200),
            "employment_type": resume_service.validate_employment_type(req.employment_type),
            "currently_work_here": bool(req.currently_work_here),
            "start_date": start_date,
            "end_date": end_date,
            "details": resume_service.optional_text(req.details, 4000),
        }

        client = _resume_client(token)
        profile = _require_resume(client, user)
        table = resume_service.CHILD_TABLES["experience"]

        if req.id:
            client.table(table).update(payload).eq("id", req.id).eq("resume_id", profile["id"]).execute()
        else:
            existing = resume_service._rows(
                client.table(table).select("id").eq("resume_id", profile["id"]).execute()
            )
            payload["resume_id"] = profile["id"]
            payload["sort_order"] = len(existing)
            client.table(table).insert(payload).execute()

        months = resume_service.recompute_total_experience(client, profile["id"])
        return {
            "status": "success",
            "message": "Experience saved",
            "total_experience_months": months,
            "total_experience_display": resume_service.format_experience(months),
        }

    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume experience save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save experience: {e}")


@app.post("/api/resume/references")
async def save_resume_reference(req: ResumeReferenceRequest, auth_tuple=Depends(require_auth_token_and_user)):
    token, user = auth_tuple
    try:
        payload = {
            "referee_name": resume_service.require_text(req.referee_name, "Referee's Name", 200),
            "job_title": resume_service.optional_text(req.job_title, 200),
            "company_name": resume_service.optional_text(req.company_name, 200),
            "email": resume_service.validate_email(req.email, required=False),
            "phone": resume_service.optional_text(req.phone, 40),
        }
        client = _resume_client(token)
        profile = _require_resume(client, user)
        table = resume_service.CHILD_TABLES["references"]

        if req.id:
            client.table(table).update(payload).eq("id", req.id).eq("resume_id", profile["id"]).execute()
        else:
            existing = resume_service._rows(
                client.table(table).select("id").eq("resume_id", profile["id"]).execute()
            )
            payload["resume_id"] = profile["id"]
            payload["sort_order"] = len(existing)
            client.table(table).insert(payload).execute()

        return {"status": "success", "message": "Reference saved"}
    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume reference save error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save reference: {e}")


@app.post("/api/resume/link-item/{section}")
async def save_resume_link_item(section: str, req: ResumeLinkItemRequest, auth_tuple=Depends(require_auth_token_and_user)):
    """Projects and Publications share the title / link / details shape."""
    token, user = auth_tuple
    if section not in ("projects", "publications"):
        raise HTTPException(status_code=400, detail="Unsupported resume collection.")
    try:
        payload = {
            "title": resume_service.require_text(req.title, "Title", 300),
            "link": resume_service.optional_text(req.link, 500),
            "details": resume_service.optional_text(req.details, 4000),
        }
        client = _resume_client(token)
        profile = _require_resume(client, user)
        table = resume_service.CHILD_TABLES[section]

        if req.id:
            client.table(table).update(payload).eq("id", req.id).eq("resume_id", profile["id"]).execute()
        else:
            existing = resume_service._rows(
                client.table(table).select("id").eq("resume_id", profile["id"]).execute()
            )
            payload["resume_id"] = profile["id"]
            payload["sort_order"] = len(existing)
            client.table(table).insert(payload).execute()

        return {"status": "success", "message": f"{section.title()[:-1]} saved"}
    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume %s save error: %s", section, e)
        raise HTTPException(status_code=500, detail=f"Failed to save: {e}")


@app.delete("/api/resume/child/{section}/{item_id}")
async def delete_resume_child(section: str, item_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    """Delete one repeatable entry. Never touches the rest of the resume."""
    token, user = auth_tuple
    table = resume_service.CHILD_TABLES.get(section)
    if not table:
        raise HTTPException(status_code=400, detail="Unsupported resume collection.")
    try:
        client = _resume_client(token)
        profile = _require_resume(client, user)
        client.table(table).delete().eq("id", item_id).eq("resume_id", profile["id"]).execute()

        if section == "experience":
            months = resume_service.recompute_total_experience(client, profile["id"])
            return {
                "status": "success",
                "message": "Experience removed",
                "total_experience_months": months,
                "total_experience_display": resume_service.format_experience(months),
            }

        return {"status": "success", "message": "Entry removed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume child delete error (%s): %s", section, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete entry: {e}")


@app.post("/api/resume/asset")
async def upload_resume_asset(req: ResumeAssetRequest, auth_tuple=Depends(require_auth_token_and_user)):
    """Upload a profile picture or signature into the private resume bucket.

    The image is sent as a base64 data URL rather than multipart so this stays
    a plain JSON endpoint and needs no new dependency. Only the storage path is
    written to the database; the bytes never enter a text column.
    """
    token, user = auth_tuple
    try:
        client = _resume_client(token)
        profile = _require_resume(client, user)
        path = resume_service.upload_asset(client, user.id, req.kind, req.data_url or "")

        column = "profile_picture_url" if req.kind == "profile-picture" else "signature_url"
        saved = client.table(resume_service.RESUME_TABLE).update(
            {column: path}
        ).eq("id", profile["id"]).execute()

        # Confirm the reference really landed. A write that matches no row is
        # not an error on its own, so without this the endpoint would report
        # success while the photo is only in storage and the resume reloads
        # without it.
        rows = getattr(saved, "data", None)
        if rows is not None and len(rows) == 0:
            raise HTTPException(
                status_code=500,
                detail="The image was uploaded but could not be attached to your resume. Please try again."
            )

        display_url = resume_service.sign_asset_url(client, path)

        return {
            "status": "success",
            "message": "Photo uploaded" if req.kind == "profile-picture" else "Signature uploaded",
            "path": path,
            "display_url": display_url,
        }
    except resume_service.ResumeValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume asset upload error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to upload image: {e}")


@app.post("/api/resume/asset/clear")
async def clear_resume_asset(req: ResumeAssetRequest, auth_tuple=Depends(require_auth_token_and_user)):
    """Detach a resume image from the document.

    Only the reference is removed; the stored object is left alone because it
    is addressed by the caller's own storage path and is overwritten by the
    next upload.
    """
    token, user = auth_tuple
    if req.kind not in ("profile-picture", "signature"):
        raise HTTPException(status_code=400, detail="Unknown resume asset type.")
    try:
        client = _resume_client(token)
        profile = _require_resume(client, user)
        column = "profile_picture_url" if req.kind == "profile-picture" else "signature_url"
        client.table(resume_service.RESUME_TABLE).update({column: None}).eq("id", profile["id"]).execute()
        return {"status": "success", "message": "Image removed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume asset clear error: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to remove image: {e}")


# -------------------------------------------------------------------
# STATIC MOUNT
# -------------------------------------------------------------------

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

def _serve_frontend_file(filename: str):
    target = os.path.join(frontend_path, filename)
    if os.path.exists(target):
        return FileResponse(target)
    return None

@app.get("/")
async def serve_landing():
    """Public landing page. Accessible without authentication."""
    response = _serve_frontend_file("landing.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "CareerPulse API active"}, status_code=200)

@app.get("/app")
async def serve_dashboard_app():
    """Authenticated single-page application shell."""
    response = _serve_frontend_file("index.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "CareerPulse API active"}, status_code=200)

@app.get("/privacy")
async def serve_privacy_policy():
    """Public Privacy Policy. Accessible without authentication."""
    response = _serve_frontend_file("privacy.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "Privacy policy not available"}, status_code=404)

@app.get("/terms")
async def serve_terms_of_service():
    """Public Terms of Service. Accessible without authentication."""
    response = _serve_frontend_file("terms.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "Terms of service not available"}, status_code=404)

@app.get("/google530afea8cea95908.html")
async def serve_google_site_verification():
    """
    Google Search Console HTML ownership-verification file.

    Explicit single-path route on purpose: this deployment serves every request
    through FastAPI (no static root is exposed), so the file has to be reachable
    at the site root for Google to fetch it. Contents are not modified or cached.
    """
    response = _serve_frontend_file("google530afea8cea95908.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "Verification file not available"}, status_code=404)

@app.get("/manifest.webmanifest")
async def serve_pwa_manifest():
    """
    PWA web app manifest.

    Served from the site root so the <link rel="manifest"> tag resolves on
    every page. The manifest MIME type is set explicitly because ".webmanifest"
    is not a type Python's mimetypes module recognises, and browsers reject a
    manifest that is not served as JSON.
    """
    response = _serve_frontend_file("manifest.webmanifest")
    if response is not None:
        response.headers["Content-Type"] = "application/manifest+json"
        response.headers["Cache-Control"] = "no-cache"
        return response
    return JSONResponse(content={"status": "Manifest not available"}, status_code=404)

@app.get("/sw.js")
async def serve_pwa_service_worker():
    """
    PWA service worker.

    Served from the site root so its default scope covers the whole origin,
    which is what lets it control "/" and "/app". A JavaScript content type is
    mandatory: browsers refuse to register a worker served as anything else.
    "no-cache" keeps the worker itself always revalidated so a new build is
    picked up, while the versioned cache inside it still does the real caching.
    """
    response = _serve_frontend_file("sw.js")
    if response is not None:
        response.headers["Content-Type"] = "text/javascript; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response
    return JSONResponse(content={"status": "Service worker not available"}, status_code=404)

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
async def serve_favicon():
    """Serve the CareerPulse tab icon/favicon."""
    response = _serve_frontend_file("favicon.png")
    if response is not None:
        response.headers["Content-Type"] = "image/png"
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    target = os.path.join(frontend_path, "assets", "logo.png")
    if os.path.exists(target):
        return FileResponse(target, media_type="image/png")
    return JSONResponse(content={"status": "Favicon not available"}, status_code=404)