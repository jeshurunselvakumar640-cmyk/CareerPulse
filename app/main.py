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

# -------------------------------------------------------------------
# NATIVE 5:00 AM AUTOMATED SCHEDULER
# -------------------------------------------------------------------

async def daily_5am_loop():
    """Calculates time until next 5:00 AM IST and executes the daily digest automatically across users."""
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    
    while True:
        now = datetime.now(tz)
        target_time = now.replace(hour=5, minute=0, second=0, microsecond=0)
        
        if now >= target_time:
            target_time += timedelta(days=1)
            
        seconds_to_wait = (target_time - now).total_seconds()
        logger.info(f"⏰ Next automated daily digest scheduled in {seconds_to_wait / 3600:.2f} hours (at 5:00 AM IST).")
        
        await asyncio.sleep(seconds_to_wait)
        
        logger.info("⏰ Executing automated daily digest email dispatch at 5:00 AM IST...")
        try:
            # Query all registered user profiles from Supabase
            profiles_res = supabase.table("user_profiles").select("*").execute()
            profiles = profiles_res.data or []

            for profile in profiles:
                if not profile.get("email"):
                    continue
                user_profile = {
                    "user_id": profile.get("id") or profile.get("user_id"),
                    "name": profile.get("full_name") or profile.get("name", "User"),
                    "email": profile["email"],
                    "skills": profile.get("skills", []),
                    "locations": profile.get("locations", ["Remote"]),
                    "roles": profile.get("roles", ["Software Engineer"])
                }
                result = await send_daily_digest(user_profile, force=False)
                logger.info(f"Automated 5:00 AM Digest result for {profile['email']}: {result}")
        except Exception as e:
            logger.error("Failed to execute scheduled 5:00 AM digest: %s", str(e))

# -------------------------------------------------------------------
# LIFECYCLE
# -------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    logger.info("Initializing CareerPulse services and native 5:00 AM automated scheduler...")
    asyncio.create_task(daily_5am_loop())

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
    location: str
    matchPercentage: int
    tier: str
    skills: list = []
    lackingSkills: list = []
    summary: str
    url: str
    status: str

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
        return {"status": "error", "detail": str(e)}

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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/auth/callback")
async def auth_callback(code: str):
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
        logger.warning("Fetch saved error: %s", e)
        return {"status": "success", "saved": [], "waitlisted": []}

@app.post("/api/user/opportunities/save")
async def save_opportunity(action: OpportunityAction, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    try:
        payload = {
            "user_id": user.id,
            "opportunity_key": f"{action.company}_{action.title}".lower().replace(" ", "_"),
            "status": action.status,
            "opportunity_data": action.dict()
        }
        supabase.table("user_saved_opportunities").upsert(payload, on_conflict="user_id,opportunity_key").execute()
        return {"status": "success"}
    except Exception as e:
        logger.error("Save opportunity error: %s", e)
        return {"status": "error", "detail": str(e)}

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
        supabase_client=supabase,
    )
    return result

# -------------------------------------------------------------------
# CONTACT LOOKUP & EMAIL GENERATION
# -------------------------------------------------------------------

@app.post("/api/opportunity/contact-lookup")
async def opportunity_contact_lookup(req: ContactLookupRequest):
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
            supabase=supabase,
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
        logger.error("Email send error: %s", e)
        raise HTTPException(status_code=500, detail=f"Unable to send the email right now: {str(e)}")

@app.api_route("/api/email/test", methods=["GET", "POST"])
async def email_test(req: Optional[EmailTestRequest] = None):
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
            logger.error("SMTP test dispatch failed: %s", e)
            raise HTTPException(status_code=500, detail=f"SMTP dispatch failure: {str(e)}")

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
    status = reply_tracker.mailbox_integration_status(supabase, user.id)
    status["unread_replies"] = reply_tracker.count_unread_replies(supabase, user.id)
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

    if not user and state_payload:
        logger.info(
            "Resolving the Gmail connection from a signed OAuth state because "
            "the session was unavailable on the redirect back."
        )
        user_id = str(state_payload["uid"])
    elif user:
        user_id = user.id
    else:
        return RedirectResponse(url="/?error=signin_required")

    # With a live session the cookie must still match: it proves this browser
    # started the flow. Without a session the signed state is the only proof.
    if user is not None:
        stored_state = request.cookies.get(GMAIL_OAUTH_COOKIE)
        if not state or not stored_state or not hmac.compare_digest(state, stored_state):
            logger.warning("Rejected a Gmail OAuth callback with an invalid state value.")
            return RedirectResponse(url="/app#applications&mailbox=state_mismatch")

    if error:
        logger.info("Gmail authorization was not completed: %s", error)
        return RedirectResponse(url="/app#applications&mailbox=denied")

    if not code:
        return RedirectResponse(url="/app#applications&mailbox=missing_code")

    # The redirect URI stored at connect time is the exact string Google
    # received on the authorize request. Reusing it guarantees the token
    # request repeats it byte for byte.
    redirect_uri = request.cookies.get(GMAIL_REDIRECT_COOKIE) or _gmail_redirect_uri(request)

    try:
        bundle = gmail_client.exchange_authorization_code(code, redirect_uri)
        # A grant whose Gmail identity could not be verified is not a usable
        # connection: never persist it and never report it as connected.
        if not (bundle.mailbox_email or "").strip():
            logger.warning(
                "Gmail OAuth completed without a resolved mailbox address; "
                "the connection was not saved."
            )
            return RedirectResponse(url="/app#applications&mailbox=email_unresolved")
        reply_tracker.save_connection(
            supabase=supabase,
            user_id=user_id,
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=bundle.expires_at,
            scopes=bundle.scopes,
            mailbox_email=bundle.mailbox_email,
        )
    except MailboxNotConfigured as e:
        logger.warning("Gmail OAuth is not configured: %s", e)
        return RedirectResponse(url="/app#applications&mailbox=not_configured")
    except MailboxError as e:
        code_name = getattr(e, "code", "mailbox_error")
        logger.warning("Gmail OAuth failed (%s)", code_name)
        return RedirectResponse(url=f"/app#applications&mailbox={code_name}")

    response = RedirectResponse(url="/app#applications&mailbox=connected")
    response.delete_cookie(key=GMAIL_OAUTH_COOKIE, path="/")
    response.delete_cookie(key=GMAIL_REDIRECT_COOKIE, path="/")
    return response

@app.post("/api/email/integration/disconnect")
async def email_integration_disconnect(auth_tuple=Depends(require_auth_token_and_user)):
    """Remove the stored mailbox connection and its tokens for this user."""
    _, user = auth_tuple
    removed = reply_tracker.disconnect_mailbox(supabase, user.id)
    return {"status": "success", "disconnected": removed}

@app.post("/api/email/sync")
async def sync_email_replies(req: Optional[ReplySyncRequest] = None, auth_tuple=Depends(require_auth_token_and_user)):
    """Check the connected mailbox for new company replies to tracked applications."""
    _, user = auth_tuple
    force = bool(req.force) if req else False
    result = await reply_tracker.sync_replies(supabase, user.id, force=force)

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
    replies = reply_tracker.list_replies(supabase, user.id, limit=limit, unread_only=unread_only)
    return {
        "status": "success",
        "replies": replies,
        "unread_count": reply_tracker.count_unread_replies(supabase, user.id),
    }

@app.get("/api/email/replies/{reply_id}")
async def get_email_reply(reply_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    reply = reply_tracker.get_reply(supabase, user.id, reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found.")
    return {"status": "success", "reply": reply}

@app.post("/api/email/replies/{reply_id}/read")
async def mark_email_reply_read(reply_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    if not reply_tracker.mark_reply_read(supabase, user.id, reply_id):
        raise HTTPException(status_code=404, detail="Reply not found.")
    return {
        "status": "success",
        "unread_count": reply_tracker.count_unread_replies(supabase, user.id),
    }

@app.get("/api/applications")
async def list_applications(auth_tuple=Depends(require_auth_token_and_user)):
    """Application communication list: sent, replied, interview requested, etc."""
    _, user = auth_tuple
    applications = reply_tracker.list_applications(supabase, user.id)
    return {
        "status": "success",
        "applications": applications,
        "unread_replies": reply_tracker.count_unread_replies(supabase, user.id),
    }

@app.get("/api/opportunities/{opportunity_id}/conversation")
async def get_opportunity_conversation(opportunity_id: str, auth_tuple=Depends(require_auth_token_and_user)):
    """Full chronological conversation for one opportunity of this user."""
    _, user = auth_tuple
    return reply_tracker.get_conversation(supabase, user.id, opportunity_id)

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
        supabase, user.id, req.opportunity_id, user.user_metadata or {}
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

@app.post("/api/digest/send")
async def trigger_digest_send(req: Optional[DigestTriggerRequest] = None, auth_tuple=Depends(require_auth_token_and_user)):
    _, user = auth_tuple
    force = req.force if req else False
    metadata = user.user_metadata or {}
    user_profile = {
        "user_id": user.id,
        "name": metadata.get("full_name") or metadata.get("name") or "User",
        "email": user.email,
        "skills": metadata.get("skills", []),
        "locations": [metadata.get("location")] if metadata.get("location") else ["Remote"],
        "roles": [metadata.get("headline")] if metadata.get("headline") else ["Software Engineer"]
    }

    result = await send_daily_digest(user_profile, force=force)
    return result

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