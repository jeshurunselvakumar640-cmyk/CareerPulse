import os
import re
import json
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
from app.services.daily_digest import (
    send_daily_digest,
    is_digest_already_sent_today,
    load_local_digest_history
)

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
    """Calculates time until next 5:00 AM IST and executes the daily digest automatically."""
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
            user_profile = {
                "user_id": "candidate_default_user",
                "name": "Jeshurun Selvakumar",
                "email": settings.smtp_user or "jeshurunselvakumar640@gmail.com",
                "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
                "locations": ["Mumbai", "Navi Mumbai", "Remote"],
                "roles": ["Software Engineer Intern", "Backend Developer Intern", "AI ML Intern"]
            }
            # force=False enforces strict 24-hour idempotency check
            result = await send_daily_digest(user_profile, force=False)
            logger.info("Automated 5:00 AM Digest Dispatch Result: %s", result)
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
        # Automatically detects whether running locally or on Vercel
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

        # Determine new vs existing user based on profile completeness
        # A user with no skills in their metadata is considered new
        is_new_user = True
        try:
            metadata = user.user_metadata or {} if user else {}
            skills = metadata.get("skills") or []
            # If the user has skills saved, they have completed their profile before
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
    """GET logout — clears cookie and redirects to root."""
    response = RedirectResponse(url="/")
    response.delete_cookie(key="sb-access-token", path="/")
    return response

@app.post("/api/auth/logout")
async def logout_post():
    """POST logout — clears cookie and returns JSON success for frontend-driven redirect."""
    response = JSONResponse(content={"status": "success", "message": "Logged out successfully"})
    response.delete_cookie(key="sb-access-token", path="/")
    return response

@app.get("/api/user/profile")
async def get_user_profile(request: Request, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    
    default_profile = {
        "name": "Jeshurun Selvakumar",
        "email": "jeshurunselvakumar640@gmail.com",
        "profile_picture": "",
        "headline": "Computer Engineering Student | SIES Graduate School of Technology",
        "degree": "B.Tech Computer Engineering",
        "college": "SIES Graduate School of Technology",
        "year": "Second Year (Semester 3)",
        "location": "Mumbai / Navi Mumbai",
        "experience": "Student / Internship",
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "github": "https://github.com/JeshurunSelvakumar",
        "linkedin": "https://linkedin.com/in/jeshurun-selvakumar",
        "projects": ["CareerPulse AI Agent", "Chordician Application", "PhysiX"]
    }

    if not user:
        return {"authenticated": False, "profile_complete": False, "profile": default_profile}

    try:
        metadata = user.user_metadata or {}
        skills = metadata.get("skills") or []
        profile_complete = bool(skills and len(skills) > 0)
        return {
            "authenticated": True,
            "profile_complete": profile_complete,
            "profile": {
                "name": metadata.get("full_name") or metadata.get("name") or default_profile["name"],
                "email": user.email or default_profile["email"],
                "profile_picture": metadata.get("avatar_url") or metadata.get("picture") or "",
                "headline": metadata.get("headline") or default_profile["headline"],
                "degree": metadata.get("degree") or default_profile["degree"],
                "college": metadata.get("college") or default_profile["college"],
                "year": metadata.get("year") or default_profile["year"],
                "location": metadata.get("location") or default_profile["location"],
                "experience": "Student / Internship",
                "skills": skills if skills else default_profile["skills"],
                "github": metadata.get("github") or default_profile["github"],
                "linkedin": metadata.get("linkedin") or default_profile["linkedin"],
                "projects": metadata.get("projects") or default_profile["projects"]
            }
        }
    except Exception as e:
        logger.error("Profile fetch error: %s", str(e))
        return {"authenticated": False, "profile_complete": False, "profile": default_profile}

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
            "skills": req.skills
        }

        user_client.auth.update_user({
            "data": updated_metadata
        })

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
    
    user_profile = {
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "locations": ["Mumbai", "Navi Mumbai", "Pune", "Remote"],
        "roles": ["Software Engineer Intern", "Backend Developer Intern", "Frontend Developer Intern", "AI ML Intern"]
    }

    if user and user.user_metadata:
        user_profile["skills"] = user.user_metadata.get("skills", user_profile["skills"])

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
            "best_role": "Software Engineering Intern",
            "top_skill": user_profile["skills"][0] if user_profile["skills"] else "Python",
            "growing_skill": "FastAPI",
            "recommended": "Full Stack AI"
        },
        "opportunities": valid_opportunities,
        "daily_news": verified_news
    }

# -------------------------------------------------------------------
# CONTACT LOOKUP & EMAIL GENERATION
# -------------------------------------------------------------------

@app.post("/api/opportunity/contact-lookup")
async def opportunity_contact_lookup(req: ContactLookupRequest):
    data = lookup_company_contact(req.company, req.title)
    return {"status": "success", "contact": data}

@app.post("/api/email/generate")
async def generate_email_draft(req: EmailDraftRequest, request: Request, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    
    user_data = {
        "name": "Jeshurun Selvakumar",
        "degree": "B.Tech Computer Engineering",
        "college": "SIES Graduate School of Technology",
        "year": "Second Year",
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "projects": ["CareerPulse AI Agent", "Chordician Application", "PhysiX"],
        "github": "https://github.com/JeshurunSelvakumar",
        "linkedin": "https://linkedin.com/in/jeshurun-selvakumar",
        "location": "Mumbai / Navi Mumbai"
    }

    if user and user.user_metadata:
        m = user.user_metadata
        user_data["name"] = m.get("full_name") or m.get("name") or user_data["name"]
        user_data["degree"] = m.get("degree") or user_data["degree"]
        user_data["college"] = m.get("college") or user_data["college"]
        user_data["year"] = m.get("year") or user_data["year"]
        user_data["skills"] = m.get("skills") or user_data["skills"]
        user_data["projects"] = m.get("projects") or user_data["projects"]
        user_data["github"] = m.get("github") or user_data["github"]
        user_data["linkedin"] = m.get("linkedin") or user_data["linkedin"]
        user_data["location"] = m.get("location") or user_data["location"]

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
Availability: Immediate / Next Semester
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
            chat = client.chats.create(model=settings.primary_model or "gemini-3.5-flash-lite")
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
    fallback_body = f"""{recipient_salutation}

My name is {user_data['name']}, a {user_data['year']} student pursuing {user_data['degree']} at {user_data['college']}. I am writing to express my strong interest in the {req.title} position at {req.company}.

Through my academic coursework and hands-on projects including {user_data['projects'][0]}, I have built practical skills in {', '.join(user_data['skills'][:3])}. My technical background in full-stack engineering and software automation aligns well with the requirements for this role.

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
        send_email(req.to_email, req.subject, req.body)

        log_payload = {
            "user_id": user.id,
            "opportunity_id": req.opportunity_id,
            "recipient_email": req.to_email,
            "subject": req.subject,
            "body": req.body,
            "status": "Sent"
        }
        try:
            supabase.table("email_logs").insert(log_payload).execute()
        except Exception as db_err:
            logger.warning("Supabase email logging warning: %s", db_err)

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
                subject="CareerPulse — SMTP Test Verification",
                body="This is a test notification confirming that CareerPulse Gmail SMTP transport is fully operational."
            )
            return {"status": "success", "message": f"Test email sent successfully to {req.to_email}"}
        except Exception as e:
            logger.error("SMTP test dispatch failed: %s", e)
            raise HTTPException(status_code=500, detail=f"SMTP dispatch failure: {str(e)}")

    smtp_status = verify_smtp_connection()
    if smtp_status.get("status") == "success":
        return {"status": "success", "message": "SMTP transport operational."}
    else:
        raise HTTPException(status_code=500, detail=smtp_status.get("message", "SMTP connection failed."))

# -------------------------------------------------------------------
# DAILY DIGEST ENDPOINTS
# -------------------------------------------------------------------

@app.get("/api/digest/status")
async def get_digest_status(request: Request, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    user_id = user.id if user else "candidate_default_user"
    user_email = user.email if user else (settings.smtp_user or "jeshurunselvakumar640@gmail.com")
    
    already_sent = is_digest_already_sent_today(user_id, user_email)
    local_history = load_local_digest_history()
    
    return {
        "status": "success",
        "already_sent_today": already_sent,
        "last_sent_timestamp": local_history.get("last_sent_timestamp"),
        "recent_records": local_history.get("sent_records", [])[-5:]
    }

@app.post("/api/digest/send")
async def trigger_digest_send(req: Optional[DigestTriggerRequest] = None, request: Request = None, authorization: Optional[str] = Header(None)):
    _, user = get_current_token_and_user(request, authorization)
    force = req.force if req else False
    user_profile = {
        "user_id": user.id if user else "candidate_default_user",
        "name": user.user_metadata.get("full_name") if user and user.user_metadata else "Jeshurun Selvakumar",
        "email": user.email if user else (settings.smtp_user or "jeshurunselvakumar640@gmail.com"),
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "locations": ["Mumbai", "Navi Mumbai", "Remote"],
        "roles": ["Software Engineer Intern", "Backend Developer Intern", "AI ML Intern"]
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
    from fastapi.responses import FileResponse
    target = os.path.join(frontend_path, filename)
    if os.path.exists(target):
        return FileResponse(target)
    return None


@app.get("/")
async def serve_landing():
    """Public landing page. Accessible without authentication."""
    response = _serve_frontend_file("landing.html")
    if response is not None:
        # Public marketing page: never cache a stale authenticated shell.
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "CareerPulse API active"}, status_code=200)


@app.get("/app")
async def serve_dashboard_app():
    """Authenticated single-page application shell (dashboard, profile, opportunities)."""
    response = _serve_frontend_file("index.html")
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
        return response
    return JSONResponse(content={"status": "CareerPulse API active"}, status_code=200)