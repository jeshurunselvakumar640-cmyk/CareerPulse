import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from app.config import settings
from app.services.email_service import send_email
from app.services.email_template import build_digest_html, build_digest_text
from app.services.news_service import fetch_and_verify_technology_news
from app.services.job_search import discover_and_extract_opportunities
from app.services.ai_matcher import deterministic_score_opportunity, display_skills
from app.services.supabase_client import get_supabase_client

logger = logging.getLogger("careerpulse.daily_digest")

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", ".digest_history.json")


def load_local_digest_history() -> Dict[str, Any]:
    """Loads local persistent digest dispatch logs for offline/restart idempotency."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Error reading digest history file: %s", e)
    return {"last_sent_timestamp": None, "sent_records": []}


def save_local_digest_history(data: Dict[str, Any]) -> None:
    """Saves local persistent digest dispatch log."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning("Error writing digest history file: %s", e)


def is_digest_already_sent_today(user_id: str, recipient_email: str) -> bool:
    """
    STRICT 24-HOUR IDEMPOTENCY CHECK.
    Checks both local cache and Supabase email_logs.
    Prevents duplicate emails upon server restarts, web refreshes, or duplicate scheduler ticks.
    """
    now = datetime.now(timezone.utc)
    today_date = now.strftime("%Y-%m-%d")
    expected_digest_id = f"daily_digest_{today_date}"

    # 1. Check local persistent file
    local_history = load_local_digest_history()
    last_sent_iso = local_history.get("last_sent_timestamp")
    if last_sent_iso:
        try:
            last_sent_dt = datetime.fromisoformat(last_sent_iso)
            if last_sent_dt.tzinfo is None:
                last_sent_dt = last_sent_dt.replace(tzinfo=timezone.utc)
            hours_since = (now - last_sent_dt).total_seconds() / 3600.0
            if hours_since < 23.5:
                logger.info("Local history: Digest already sent %.1f hours ago (within 24h window).", hours_since)
                return True
        except Exception:
            pass

    for rec in local_history.get("sent_records", []):
        if rec.get("digest_date") == today_date and (rec.get("user_id") == user_id or rec.get("recipient_email") == recipient_email):
            logger.info("Local history: Digest record already exists for %s.", today_date)
            return True

    # 2. Check Supabase email_logs table
    try:
        supabase = get_supabase_client()
        # Query for matching opportunity_id today
        res = supabase.table("email_logs").select("*").eq("opportunity_id", expected_digest_id).execute()
        if res.data and len(res.data) > 0:
            logger.info("Supabase email_logs: Found existing digest record for today: %s", expected_digest_id)
            return True

        # Also query for any digest sent within the last 24 hours
        cutoff = (now - timedelta(hours=23, minutes=30)).isoformat()
        res_recent = (
            supabase.table("email_logs")
            .select("*")
            .eq("recipient_email", recipient_email)
            .gte("sent_at", cutoff)
            .execute()
        )
        if res_recent.data and any("daily_digest" in r.get("opportunity_id", "") for r in res_recent.data):
            logger.info("Supabase email_logs: Digest already sent within the past 24 hours to %s", recipient_email)
            return True

    except Exception as e:
        logger.debug("Supabase check non-fatal warning: %s", e)

    return False


def _period_label() -> str:
    """
    Digest period label derived from the actual 24-hour window processed.
    Never hardcoded.
    """
    now = datetime.now(timezone.utc).astimezone()
    start = now - timedelta(hours=24)
    if start.month == now.month and start.day == now.day:
        return f"{start.strftime('%B %d')} – {now.strftime('%B %d, %Y')}"
    if start.month == now.month:
        return f"{start.strftime('%B %d')} – {now.strftime('%B %d, %Y')}"
    return f"{start.strftime('%b %d')} – {now.strftime('%b')} {now.day}, {now.year}"


def build_digest_email_text(
    candidate_name: str,
    news_items: List[Dict[str, Any]],
    opportunities: List[Dict[str, Any]],
    skill_gaps: List[str]
) -> str:
    """
    Backwards-compatible plain-text digest body, so existing callers keep working.
    """
    return build_digest_text(
        candidate_name=candidate_name,
        stories=news_items,
        opportunities=opportunities,
        skill_gaps=skill_gaps,
        period_label=_period_label(),
    )


async def send_daily_digest(user_profile: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """
    Executes the 24-hour daily technology & career opportunity digest.
    Guarantees idempotency: sends exactly once per 24-hour period.
    """
    user_id = user_profile.get("user_id") or "candidate_default_user"
    candidate_name = user_profile.get("name") or "Jeshurun Selvakumar"
    recipient_email = user_profile.get("email") or settings.smtp_user

    logger.info("digest: generation started for user=%s", user_id)

    # 1. Idempotency Check
    if not force and is_digest_already_sent_today(user_id, recipient_email):
        msg = f"Daily digest already sent to {recipient_email} in the last 24 hours. Skipping."
        logger.info(msg)
        return {"status": "skipped", "message": msg}

    # 2. News Pipeline - verified, current, max 6 stories
    news_items = await asyncio.to_thread(fetch_and_verify_technology_news, 6)
    logger.info("digest: news verified count = %d", len(news_items))

    # 3. Job Pipeline (verified individual opportunities only)
    raw_jobs = await asyncio.to_thread(discover_and_extract_opportunities, user_profile, 6)
    logger.info("digest: job candidates after validation = %d", len(raw_jobs))
    logger.info("digest: search completed (news_count=%d, jobs_count=%d)", len(news_items), len(raw_jobs))

    scored_jobs = []
    for job in raw_jobs:
        match_res = deterministic_score_opportunity(job, user_profile)
        if not match_res.get("is_eligible"):
            logger.info("digest: job rejected (not eligible): %s", job.get("title"))
            continue
        job["match"] = match_res
        job["required_skills"] = display_skills(job.get("required_skills") or [])
        job["preferred_skills"] = display_skills(job.get("preferred_skills") or [])
        scored_jobs.append(job)
    logger.info("digest: jobs verified and eligible = %d", len(scored_jobs))
    logger.info("digest: Gemini summarization completed")

    # 4. Skill gaps, from verified opportunities only
    gap_counts: Dict[str, int] = {}
    for job in scored_jobs:
        for s in job.get("match", {}).get("missing_skills", []):
            gap_counts[s] = gap_counts.get(s, 0) + 1
    skill_gaps = display_skills(sorted(gap_counts.keys(), key=lambda k: gap_counts[k], reverse=True))

    # 5. Render both HTML and plain-text parts
    period_label = _period_label()
    subject = "CareerPulse — Your 24-Hour Technology & Career Digest"

    plain_body = build_digest_text(
        candidate_name=candidate_name,
        stories=news_items,
        opportunities=scored_jobs,
        skill_gaps=skill_gaps,
        period_label=period_label,
    )
    html_body = build_digest_html(
        candidate_name=candidate_name,
        stories=news_items,
        opportunities=scored_jobs,
        skill_gaps=skill_gaps,
        period_label=period_label,
    )
    logger.info("digest: email generated (html=%d bytes text=%d bytes)",
                len(html_body), len(plain_body))

    # 6. Send Email via SMTP in worker thread
    logger.info("digest: email send attempted to %s", recipient_email)
    try:
        await asyncio.to_thread(
            send_email,
            to_email=recipient_email,
            subject=subject,
            body=plain_body,
            html_body=html_body
        )
        logger.info("digest: email send succeeded to %s", recipient_email)
    except Exception as e:
        logger.error("digest: email send failed to %s: %s", recipient_email, e)
        return {"status": "error", "message": f"SMTP dispatch failed: {str(e)}"}

    # 6. Record Idempotency Logging
    now_iso = datetime.now(timezone.utc).isoformat()
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest_key = f"daily_digest_{today_date}"

    # Supabase log
    try:
        supabase = get_supabase_client()
        supabase.table("email_logs").insert({
            "user_id": user_id,
            "opportunity_id": digest_key,
            "recipient_email": recipient_email,
            "subject": subject,
            "body": plain_body[:800] + "... [digest truncated]",
            "status": "Sent"
        }).execute()
    except Exception as e:
        logger.warning("Could not persist digest log to Supabase: %s", e)

    # Local file log
    local_history = load_local_digest_history()
    local_history["last_sent_timestamp"] = now_iso
    local_history["sent_records"].append({
        "user_id": user_id,
        "recipient_email": recipient_email,
        "digest_date": today_date,
        "sent_at": now_iso,
        "news_count": len(news_items),
        "jobs_count": len(scored_jobs)
    })
    # Keep last 30 records
    local_history["sent_records"] = local_history["sent_records"][-30:]
    save_local_digest_history(local_history)

    return {
        "status": "success",
        "message": f"Daily digest successfully sent to {recipient_email}",
        "news_count": len(news_items),
        "jobs_count": len(scored_jobs)
    }


async def background_scheduler_worker():
    """
    Background worker process running every hour.
    Checks if 24 hours have elapsed since the candidate's last digest.
    If 24h have elapsed, safely dispatches the daily digest once.
    """
    logger.info("Starting CareerPulse 24-hour daily digest background worker...")
    # Initial sleep to let FastAPI start up completely
    await asyncio.sleep(5)

    default_profile = {
        "user_id": "candidate_default_user",
        "name": "Jeshurun Selvakumar",
        "email": settings.smtp_user or "jeshurunselvakumar640@gmail.com",
        "skills": ["Python", "Java", "C", "React", "JavaScript", "SQL", "FastAPI"],
        "locations": ["Mumbai", "Navi Mumbai", "Remote"],
        "roles": ["Software Engineer Intern", "Backend Developer Intern", "AI ML Intern"]
    }

    while True:
        try:
            # Check if digest is due
            if not is_digest_already_sent_today(default_profile["user_id"], default_profile["email"]):
                logger.info("24-hour interval elapsed. Triggering daily digest...")
                await send_daily_digest(default_profile, force=False)
            else:
                logger.info("Daily digest scheduler: Already sent today. Sleeping.")
        except Exception as e:
            logger.error("Error in background digest scheduler: %s", e)

        # Sleep for 1 hour before next periodic evaluation
        await asyncio.sleep(3600)
