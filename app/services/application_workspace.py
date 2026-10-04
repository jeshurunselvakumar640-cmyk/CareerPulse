"""
Application Workspace.

Everything the application page needs that the existing reply pipeline does not
already provide:

  * grounded next-step extraction (Gemini)
  * interview detection, only from an explicitly stated date AND time
  * a chronological timeline built from real rows only
  * an optional calendar event per confirmed interview
  * in-app reminder surfacing

Deliberate non-goals, so nothing existing is duplicated or replaced:

  * This module does NOT re-detect replies. `reply_tracker.match_reply` already
    decides which application an email belongs to, and a 5-tier cascade keyed on
    thread ids and headers is far stronger than anything re-implemented here.
  * It does NOT re-classify replies. The existing `classification` stays the
    single source of truth for what a reply is.
  * It does NOT own a status machine. The existing `list_applications` status
    stays authoritative for the Overview tab.
  * It does NOT send mail and does NOT touch the daily digest.

Failure isolation is a hard requirement, not a nicety: every public entry point
here is wrapped so that a Gemini outage, a Calendar outage or a schema that has
not been migrated yet can never break Gmail sync, OAuth, discovery, the resume
builder or anything else. Every one of them returns a status dictionary instead
of raising.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.services import calendar_client, reply_tracker
from app.services.gmail_client import (
    MailboxAuthError,
    MailboxError,
    MailboxProviderError,
    MailboxRateLimited,
)

logger = logging.getLogger(__name__)

REPLY_TABLE = "email_replies"
SENT_TABLE = "email_logs"
TIMELINE_TABLE = "application_timeline_events"
INTERVIEW_TABLE = "application_interviews"

MAX_NEXT_STEPS = 12
MAX_STEP_CHARS = 400

# Timeline events CareerPulse is willing to assert, mapped from the EXISTING
# classification values in reply_tracker.CLASSIFICATIONS. Nothing here invents a
# classification: only labels that already exist are rendered, so this adds no
# meaning to any reply. (These are derived at read time from email_replies rather
# than persisted, which is why they cannot duplicate.)
_CLASSIFICATION_EVENTS = {
    "application_received": ("application_received", "Application received"),
    "generic_acknowledgement": ("acknowledgement", "Application acknowledged"),
    "positive_response": ("recruiter_reply", "Recruiter replied"),
    "interview_request": ("interview_requested", "Interview requested"),
    "interview_scheduled": ("interview_scheduled", "Interview scheduled"),
    "additional_information_requested": ("information_requested", "Additional information requested"),
    "rejection": ("rejection", "Rejected"),
    "follow_up_required": ("follow_up", "Follow-up required"),
}

# A calendar event may only ever be created from a reply the existing classifier
# marked as a scheduled interview AND that stated a definite date and time.
_INTERVIEW_CLASSIFICATIONS = {"interview_scheduled"}

_TIMEZONE_ABBREVIATIONS = {
    "UTC": 0, "GMT": 0, "Z": 0,
    "IST": 330, "BST": 60, "WET": 0, "CET": 60, "CEST": 120, "EET": 120, "EEST": 180,
    "EST": -300, "EDT": -240, "CST": -360, "CDT": -300,
    "MST": -420, "MDT": -360, "PST": -480, "PDT": -420,
    "JST": 540, "KST": 540, "SGT": 480, "AEST": 600, "AEDT": 660,
    "MSK": 180, "GST": 240,
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# The instruction is used verbatim, as specified. It is the contract that keeps
# Gemini from summarising, explaining or inventing.
_NEXT_STEPS_INSTRUCTION = """This is a reply received for my job/internship application from {COMPANY_NAME}.

Analyze the email carefully and determine what the applicant needs to do next.

Give me ONLY the concrete next steps I should take.

Return the answer strictly as a numbered list of actionable steps.

Do not provide a summary.
Do not explain your reasoning.
Do not add assumptions.
Do not invent deadlines, dates, times, interview details, documents, actions, or requirements that are not explicitly present in the email.

If the email does not require any action from the applicant, explicitly say so as a numbered step.

If an interview is mentioned, include the exact interview date and time only if explicitly stated.

If a deadline is mentioned, include it exactly as stated.

If the email is ambiguous, do not guess. State the uncertainty where appropriate."""

# Machine-readable envelope around the instruction above. The numbered list is
# preserved verbatim in "next_steps"; nothing else is requested from the model.
_ANALYSIS_SCHEMA_HINT = """
Return strict JSON with exactly these keys:
- "next_steps": array of strings. Each string is one numbered step, written as
  it should appear to the user (for example "1. Complete the coding assessment.").
  Preserve the email's own wording for any date, deadline or link. If no action
  is required, return a single step saying so.
- "requires_action": boolean. True only if the applicant must do something.
- "interview": object or null. Set it ONLY when the email explicitly states both
  a date and a time. Use:
    {
      "date_text": "<the date exactly as written in the email>",
      "time_text": "<the time exactly as written in the email>",
      "timezone_text": "<timezone abbreviation or offset exactly as written, or null>",
      "interview_type": "<only if stated, else null>",
      "meeting_link": "<only if stated, else null>",
      "location": "<only if stated, else null>"
    }
  If the email mentions an interview without an explicit date and time, return
  null here and say so as a numbered step instead.
- "interview_mentioned_without_schedule": boolean.

Rules:
- Never invent a date, time, timezone, link, location or document.
- Never add a step that is not supported by the email text.
- If the email is ambiguous, state the uncertainty in the step rather than guessing."""


# ===================================================================
# Grounded datetime extraction
#
# The single most important guarantee in this module: a datetime is only
# accepted when the matched date text AND the matched time text both appear
# verbatim in the email body. That makes hallucinated dates structurally
# impossible rather than merely discouraged by the prompt.
# ===================================================================

_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b", re.I),
    re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I),
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]

_TIME_PATTERN = re.compile(
    r"\b(\d{1,2})(:(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b|\b(\d{1,2}):(\d{2})\b", re.I
)

_OFFSET_PATTERN = re.compile(r"(?:GMT|UTC)?\s*([+-])(\d{1,2}):?(\d{2})\b")
_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_TIMEZONE_ABBREVIATIONS, key=len, reverse=True)) + r")\b"
)


def _find_date(text: str) -> Optional[Tuple[datetime, str]]:
    """Return (naive datetime, the exact matched text) or None."""
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(0)
        try:
            if pattern is _DATE_PATTERNS[2]:
                dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif m.group(1).isdigit() and m.group(2).isalpha():
                dt = datetime(int(m.group(3)), _MONTHS[m.group(2).lower()[:4].rstrip(".")], int(m.group(1)))
            else:
                month = m.group(1).lower()[:4].rstrip(".")
                dt = datetime(int(m.group(3)), _MONTHS[month], int(m.group(2)))
        except (ValueError, KeyError):
            continue
        return dt, raw
    return None


def _find_time(text: str) -> Optional[Tuple[int, int, str]]:
    """Return (hour, minute, exact matched text) or None."""
    m = _TIME_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(0)
    try:
        if m.group(1):
            hour = int(m.group(1))
            minute = int(m.group(3) or 0)
            meridiem = (m.group(4) or "").replace(".", "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
        else:
            hour = int(m.group(5))
            minute = int(m.group(6))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute, raw


def _find_timezone(text: str) -> Tuple[Optional[str], Optional[int]]:
    """Return (label, offset_minutes). Label is None when the email is silent."""
    m = _OFFSET_PATTERN.search(text)
    if m:
        minutes = int(m.group(2)) * 60 + int(m.group(3))
        if m.group(1) == "-":
            minutes = -minutes
        label = f"UTC{m.group(1)}{int(m.group(2)):02d}:{int(m.group(3)):02d}"
        return label, minutes
    m = _ABBREV_PATTERN.search(text)
    if m:
        label = m.group(1).upper()
        return label, _TIMEZONE_ABBREVIATIONS.get(label)
    return None, None


# Words that turn a date into something other than an interview. A deadline is
# a real, correctly-extracted date that must NOT become a calendar event, so it
# is rejected here rather than being allowed to masquerade as an interview.
_DEADLINE_MARKERS = (
    "deadline", "due", "submit by", "submit before", "before", "by when", "expires",
    "expiring", "no later than", "respond by", "reply by", "complete by", "complete before",
)


def _looks_like_deadline(body_text: str, date_text: str) -> bool:
    """
    True when the matched date is attached to a deadline rather than a meeting.

    Only the surrounding text is inspected, so an unrelated "before" further up
    the email cannot suppress a genuine interview time.
    """
    index = body_text.lower().find(date_text.lower())
    if index < 0:
        return False
    window = body_text[max(0, index - 90): index + len(date_text) + 60].lower()
    return any(marker in window for marker in _DEADLINE_MARKERS)


def extract_explicit_datetime(body_text: str) -> Optional[Dict]:
    """
    Find an interview datetime that the email actually states.

    Requires a date AND a time, both present verbatim in the body. Returns None
    for "we would like to interview you soon", which is the whole point: an
    unscheduled interview must never become a calendar event.
    """
    if not body_text:
        return None
    text = body_text

    found_date = _find_date(text)
    found_time = _find_time(text)
    if not found_date or not found_time:
        return None

    naive_date, date_text = found_date
    hour, minute, time_text = found_time

    # Reject a date in the past: an interview that already happened is not
    # something to schedule, and treating it as future would be a guess.
    naive = naive_date.replace(hour=hour, minute=minute)
    if naive < datetime.now() - timedelta(hours=12):
        return None

    # A deadline is not an interview. Reject it before it can be scheduled.
    if _looks_like_deadline(text, date_text):
        return None

    tz_label, tz_offset = _find_timezone(text)

    result = {
        "starts_at_local": naive,
        "date_text": date_text.strip(),
        "time_text": time_text.strip(),
        "timezone_text": tz_label,
        "timezone_confirmed": tz_offset is not None,
        "matched_text": f"{date_text.strip()} {time_text.strip()}",
    }
    if tz_offset is not None:
        tzinfo = timezone(timedelta(minutes=tz_offset))
        result["starts_at"] = naive.replace(tzinfo=tzinfo)
    else:
        # Per the agreed timezone rule: never assume. The caller holds the
        # calendar event back until the user confirms the zone.
        result["starts_at"] = None
    return result


# ===================================================================
# Gemini analysis
# ===================================================================

def _coerce_steps(raw: object) -> List[str]:
    """Keep only usable, non-duplicate, length-capped steps."""
    if not isinstance(raw, list):
        return []
    steps: List[str] = []
    seen = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        # Normalise for dedupe so "1. Do X" and "Do X" do not both survive.
        key = re.sub(r"^\d+[\.\)]\s*", "", text).strip().lower()
        key = re.sub(r"\s+", " ", key)
        if not key or key in seen:
            continue
        seen.add(key)
        steps.append(text[:MAX_STEP_CHARS])
        if len(steps) >= MAX_NEXT_STEPS:
            break
    return steps


def _strip_json_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _grounded_text(value: object, body_text: str) -> Optional[str]:
    """
    Accept a model-supplied string ONLY if it appears verbatim in the email.

    This closes the remaining hallucination surface for the interview object.
    The date and time are re-derived from the body, but the meeting link,
    location and interview type are free-text fields the model fills in, so
    each one is verified against the original email before it is stored. A
    field the model invented is dropped, not displayed with a caveat.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    haystack = body_text.lower()
    if text.lower() in haystack:
        return text
    # Tolerate trailing punctuation the model may have added.
    trimmed = text.rstrip(".,;:)]")
    if trimmed and trimmed.lower() in haystack:
        return trimmed
    return None


def analyze_reply_next_steps(
    subject: str,
    body_text: str,
    company: str,
    opportunity_title: str = "",
    classification: str = "",
) -> Dict:
    """
    Extract grounded next steps (and any explicitly stated interview) via Gemini.

    Never raises. Returns a dict with at least `state`, which is one of:
      analyzed | skipped | failed
    """
    fallback = {
        "state": "skipped",
        "next_steps": [],
        "requires_action": False,
        "interview": None,
        "interview_mentioned_without_schedule": False,
        "error": "",
    }

    if not settings.gemini_api_key:
        fallback["error"] = "Gemini is not configured."
        return fallback
    if not (body_text or "").strip():
        fallback["error"] = "The email has no readable body to analyze."
        return fallback

    instruction = _NEXT_STEPS_INSTRUCTION.replace("{COMPANY_NAME}", (company or "this company").strip())
    header = []
    if opportunity_title:
        header.append(f"Opportunity: {opportunity_title}")
    if subject:
        header.append(f"Reply subject: {subject}")
    prompt = "\n".join(header + ["", instruction, _ANALYSIS_SCHEMA_HINT, "", "Reply body:", body_text[:6000]])

    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.primary_model,
            contents=prompt,
        )
        raw = _strip_json_fence(getattr(response, "text", "") or "")
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("Gemini did not return a JSON object.")
    except Exception as e:  # noqa: BLE001 - analysis must never break sync
        logger.warning("Next-step analysis failed: %s", e)
        fallback["state"] = "failed"
        fallback["error"] = str(e)[:300]
        return fallback

    steps = _coerce_steps(parsed.get("next_steps"))
    interview = None
    mentioned = bool(parsed.get("interview_mentioned_without_schedule"))

    # Re-derive the datetime from the EMAIL, not from the model. The model's
    # date_text/time_text are only accepted when they literally appear in the
    # body, so a fabricated schedule is discarded here.
    candidate = parsed.get("interview")
    if isinstance(candidate, dict):
        extracted = extract_explicit_datetime(body_text)
        if extracted and not mentioned:
            interview = {
                "starts_at": extracted["starts_at"],
                "starts_at_local": extracted["starts_at_local"].isoformat(),
                "date_text": extracted["date_text"],
                "time_text": extracted["time_text"],
                "timezone": extracted["timezone_text"],
                "timezone_confirmed": extracted["timezone_confirmed"],
                "interview_type": _grounded_text(candidate.get("interview_type"), body_text),
                "meeting_link": _grounded_text(candidate.get("meeting_link"), body_text),
                "location": _grounded_text(candidate.get("location"), body_text),
            }
        else:
            mentioned = True

    return {
        "state": "analyzed",
        "next_steps": steps,
        "requires_action": bool(parsed.get("requires_action")) or bool(steps),
        "interview": interview,
        "interview_mentioned_without_schedule": mentioned,
        "error": "",
    }


async def analyze_reply_next_steps_async(*args, **kwargs) -> Dict:
    """Thread-offloaded wrapper, matching reply_tracker.classify_reply_async."""
    return await asyncio.to_thread(analyze_reply_next_steps, *args, **kwargs)


# ===================================================================
# Persistence
# ===================================================================

def _iso(value: Any) -> Optional[str]:
    """
    Normalise a timestamp to an ISO string.

    Accepts a datetime OR the ISO string Postgres/PostgREST already returns,
    because every timestamp arriving from the database is a string. A naive
    value is treated as UTC, which is a storage convention only: it never
    invents a user-facing timezone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _is_duplicate(error: Exception) -> bool:
    text = str(error).lower()
    return "23505" in text or "duplicate key" in text


def _is_missing_table(error: Exception) -> bool:
    text = str(error).lower()
    return "pgrst205" in text or "does not exist" in text or "schema cache" in text


def record_timeline_event(
    supabase,
    user_id: str,
    opportunity_id: str,
    event_type: str,
    title: str,
    detail: str = "",
    occurred_at: Optional[datetime] = None,
    dedupe_key: str = "",
    source_reply_id: Optional[str] = None,
) -> Optional[Dict]:
    """
    Append one timeline event, at most once per dedupe_key.

    Returns the row on insert, None when it was a duplicate or the table is not
    migrated yet. Never raises.
    """
    if not dedupe_key:
        return None
    payload = {
        "user_id": user_id,
        "opportunity_id": opportunity_id,
        "event_type": event_type,
        "title": title,
        "detail": detail or None,
        "source_reply_id": source_reply_id or None,
        "occurred_at": _iso(occurred_at),
        "dedupe_key": dedupe_key,
    }
    try:
        result = supabase.table(TIMELINE_TABLE).insert(payload).execute()
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        if _is_duplicate(e):
            return None
        if _is_missing_table(e):
            logger.info("Timeline table not migrated yet; skipping event %s", event_type)
            return None
        logger.warning("Could not record timeline event %s: %s", event_type, e)
        return None


def upsert_interview(
    supabase,
    user_id: str,
    opportunity_id: str,
    *,
    dedupe_key: str,
    starts_at: datetime,
    timezone_text: Optional[str],
    timezone_confirmed: bool,
    interview_type: Optional[str] = None,
    meeting_link: Optional[str] = None,
    location: Optional[str] = None,
    notes: str = "",
    source_reply_id: Optional[str] = None,
) -> Optional[Dict]:
    """
    Store an interview, at most once per dedupe_key. Never raises.
    """
    payload = {
        "user_id": user_id,
        "opportunity_id": opportunity_id,
        "source_reply_id": source_reply_id or None,
        "starts_at": _iso(starts_at),
        "timezone": timezone_text or None,
        "timezone_confirmed": bool(timezone_confirmed),
        "interview_type": interview_type or None,
        "meeting_link": meeting_link or None,
        "location": location or None,
        "notes": notes or None,
        "calendar_status": "not_created",
        "reminder_status": "not_set",
        "dedupe_key": dedupe_key,
    }
    try:
        result = supabase.table(INTERVIEW_TABLE).insert(payload).execute()
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        if _is_duplicate(e):
            return None
        if _is_missing_table(e):
            logger.info("Interview table not migrated yet.")
            return None
        logger.warning("Could not store interview: %s", e)
        return None


def store_reply_analysis(
    supabase,
    user_id: str,
    reply: Dict,
    analysis: Dict,
    company: str = "",
) -> Dict:
    """
    Persist one reply's analysis, its timeline events and any interview.

    Each step is independently guarded so a partial failure still leaves the
    parts that succeeded visible, and so the reply itself is never lost.
    """
    reply_id = reply.get("id")
    opportunity_id = reply.get("opportunity_id")
    out = {"state": analysis.get("state", "skipped"), "timeline": [], "interview": None}

    if not reply_id or not opportunity_id:
        out["state"] = "skipped"
        return out

    interview = analysis.get("interview") or {}
    patch = {
        "next_steps": analysis.get("next_steps") or [],
        "analysis_state": analysis.get("state", "skipped"),
        "analysis_error": (analysis.get("error") or None),
        "analyzed_at": _iso(datetime.now(timezone.utc)),
        "requires_action": bool(analysis.get("requires_action")),
        "interview_at": _iso(interview.get("starts_at")) if interview.get("starts_at") else None,
        "interview_timezone": interview.get("timezone"),
        "interview_type": interview.get("interview_type"),
        "meeting_link": interview.get("meeting_link"),
        "interview_location": interview.get("location"),
        "timezone_confirmed": bool(interview.get("timezone_confirmed")),
    }

    # The reply row itself must be updated first and is allowed to fail loudly
    # in logs only; the email_replies row already exists and is never deleted.
    try:
        supabase.table(REPLY_TABLE).update(patch).eq("id", reply_id).eq("user_id", user_id).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not store analysis for reply %s: %s", reply_id, e)
        if _is_missing_table(e):
            out["state"] = "migration_required"
            return out

    received_at = reply.get("received_at")

    # NOTE: the classification event itself (application received, interview
    # scheduled, rejection, ...) is deliberately NOT persisted here. It is
    # derived at read time from email_replies in get_application_workspace, so
    # recording it as well would show every such event twice.

    if analysis.get("next_steps"):
        stored = record_timeline_event(
            supabase, user_id, opportunity_id, "next_steps_extracted", "Next steps extracted",
            detail="; ".join(analysis["next_steps"][:3]),
            occurred_at=received_at,
            dedupe_key=f"reply:{reply_id}:next_steps",
            source_reply_id=reply_id,
        )
        if stored:
            out["timeline"].append("next_steps_extracted")

    # An interview row needs a definite timestamp. When the email gave no
    # timezone we still record the wall-clock time the email stated, but mark
    # it unconfirmed so no calendar event is created until the user agrees.
    starts_at = interview.get("starts_at") or interview.get("starts_at_local")
    if interview and starts_at:
        normalized = starts_at if isinstance(starts_at, datetime) else _parse_local(starts_at)
        row = upsert_interview(
            supabase, user_id, opportunity_id,
            dedupe_key=f"reply:{reply_id}:interview",
            starts_at=normalized,
            timezone_text=interview.get("timezone"),
            timezone_confirmed=bool(interview.get("timezone_confirmed")),
            interview_type=interview.get("interview_type"),
            meeting_link=interview.get("meeting_link"),
            location=interview.get("location"),
            notes=f"Stated in the email as {interview.get('date_text','')} {interview.get('time_text','')}".strip(),
            source_reply_id=reply_id,
        )
        if row:
            out["interview"] = row
            # Named distinctly from the derived "interview_scheduled" event so
            # the two can never render as the same timeline entry twice.
            stored = record_timeline_event(
                supabase, user_id, opportunity_id, "interview_details_captured",
                "Interview date captured",
                detail=interview.get("matched_text") or "",
                occurred_at=normalized,
                dedupe_key=f"reply:{reply_id}:interview_event",
                source_reply_id=reply_id,
            )
            if stored:
                out["timeline"].append("interview_details_captured")

    elif analysis.get("interview_mentioned_without_schedule"):
        stored = record_timeline_event(
            supabase, user_id, opportunity_id, "interview_unconfirmed", "Interview mentioned, not scheduled",
            detail="The email did not state a definite date and time, so no interview was scheduled.",
            occurred_at=received_at,
            dedupe_key=f"reply:{reply_id}:interview_unconfirmed",
            source_reply_id=reply_id,
        )
        if stored:
            out["timeline"].append("interview_unconfirmed")

    return out


def _parse_local(value: str) -> datetime:
    """Parse a naive ISO local time, assuming UTC only as a storage convention."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ===================================================================
# Calendar
# ===================================================================

def create_calendar_event_for_interview(
    supabase,
    user_id: str,
    interview: Dict,
    company: str = "",
    position: str = "",
) -> Dict:
    """
    Create the calendar event for one interview.

    Refuses to guess: it only proceeds when the stored start already carries a
    confirmed timezone. Every failure mode is reported in the returned dict and
    mirrored onto the interview row, and none of them raises.
    """
    interview_id = interview.get("id")
    out = {"created": False, "status": "not_created", "error": "", "event_id": None, "html_link": None}

    def _mark(status: str, error: str = "") -> Dict:
        out["status"] = status
        out["error"] = error
        if interview_id:
            try:
                supabase.table(INTERVIEW_TABLE).update(
                    {"calendar_status": status, "calendar_error": error or None}
                ).eq("id", interview_id).eq("user_id", user_id).execute()
            except Exception:  # noqa: BLE001
                logger.warning("Could not record calendar status for %s", interview_id)
        return out

    if not interview_id:
        return _mark("not_created", "Unknown interview.")

    if interview.get("calendar_event_id"):
        return {"created": True, "status": "created", "error": "",
                "event_id": interview.get("calendar_event_id"),
                "html_link": interview.get("calendar_html_link")}

    if not interview.get("timezone_confirmed"):
        return _mark("awaiting_timezone_confirmation",
                     "The email did not state a timezone. Confirm the timezone to create the event.")

    try:
        connection = reply_tracker.load_connection(supabase, user_id)
        if not connection:
            return _mark("not_connected", "Connect Google to create calendar events.")
        if not calendar_client.has_calendar_grant(connection.get("scopes")):
            return _mark("scope_missing",
                         "This connection predates calendar access. Reconnect Google to grant it.")
        access_token = reply_tracker.ensure_access_token(supabase, connection)
    except MailboxAuthError as e:
        reply_tracker.mark_needs_reconnect(supabase, user_id)
        return _mark("auth_error", str(e))
    except MailboxError as e:
        return _mark("token_error", str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("Calendar token step failed: %s", e)
        return _mark("token_error", str(e))

    starts_at = reply_tracker._parse_iso(interview.get("starts_at"))
    if starts_at is None:
        return _mark("no_start_time", "This interview has no start time.")

    duration = timedelta(minutes=60)
    title = " — ".join(p for p in [
        "Interview",
        (position or "").strip(),
        (company or "").strip(),
    ] if p)

    description_parts = []
    if position:
        description_parts.append(f"Position: {position}")
    if company:
        description_parts.append(f"Company: {company}")
    if interview.get("interview_type"):
        description_parts.append(f"Interview type: {interview['interview_type']}")
    if interview.get("meeting_link"):
        description_parts.append(f"Meeting link: {interview['meeting_link']}")
    if interview.get("location"):
        description_parts.append(f"Location: {interview['location']}")
    description_parts.append("Added by CareerPulse from a company reply.")

    try:
        created = calendar_client.create_interview_event(
            access_token,
            summary=title or "Interview",
            description="\n".join(description_parts),
            location=interview.get("location") or "",
            start_at_iso=starts_at.isoformat(),
            end_at_iso=(starts_at + duration).isoformat(),
            timezone=interview.get("timezone") or "",
            meeting_link=interview.get("meeting_link") or "",
        )
    except MailboxAuthError as e:
        return _mark("auth_error", str(e))
    except MailboxRateLimited as e:
        return _mark("rate_limited", str(e))
    except MailboxProviderError as e:
        return _mark("provider_error", str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("Calendar event creation failed: %s", e)
        return _mark("provider_error", str(e))

    event_id = created.get("id")
    out["created"] = bool(event_id)
    out["event_id"] = event_id
    out["html_link"] = created.get("html_link")
    out["status"] = "created" if event_id else "provider_error"
    if event_id:
        try:
            supabase.table(INTERVIEW_TABLE).update(
                {
                    "calendar_event_id": event_id,
                    "calendar_status": "created",
                    "calendar_error": None,
                    "reminder_status": "surfaced",
                }
            ).eq("id", interview_id).eq("user_id", user_id).execute()
        except Exception:  # noqa: BLE001
            logger.warning("Could not persist calendar event id for %s", interview_id)
        record_timeline_event(
            supabase, user_id, interview.get("opportunity_id") or "", "calendar_event_created",
            "Calendar event created",
            detail=created.get("html_link") or "",
            occurred_at=starts_at,
            dedupe_key=f"interview:{interview_id}:calendar_created",
        )
    return out


# ===================================================================
# Orchestration
# ===================================================================

def _reply_company_and_title(reply: Dict) -> Tuple[str, str]:
    company = reply_tracker._company_from_opportunity(reply.get("opportunity_id") or "")
    title = reply_tracker._title_from_opportunity(reply.get("opportunity_id") or "")
    return company, title


def process_reply_analysis(supabase, reply: Dict) -> Dict:
    """
    Analyse and persist a single reply. Never raises.

    Used both by the batch path and by the manual re-analyse route.
    """
    reply_id = reply.get("id")
    if not reply_id:
        return {"state": "skipped", "reason": "no id"}

    company, title = _reply_company_and_title(reply)
    analysis = analyze_reply_next_steps(
        reply.get("subject") or "",
        reply.get("body_text") or "",
        company=company,
        opportunity_title=title,
        classification=reply.get("classification") or "",
    )
    if analysis.get("state") != "analyzed":
        # Record the failure so PART 17 transparency shows why there are no steps.
        try:
            supabase.table(REPLY_TABLE).update(
                {"analysis_state": analysis.get("state"), "analysis_error": (analysis.get("error") or None)[:300]}
            ).eq("id", reply_id).eq("user_id", reply.get("user_id")).execute()
        except Exception:  # noqa: BLE001
            pass
        return {"state": analysis.get("state"), "error": analysis.get("error", "")}

    result = store_reply_analysis(supabase, reply.get("user_id"), reply, analysis, company=company)

    if result.get("interview"):
        interview = dict(result["interview"])
        interview["opportunity_id"] = reply.get("opportunity_id")
        # Automatic, and failure-isolated: a calendar outage leaves the reply,
        # the next steps and the timeline fully intact.
        calendar = create_calendar_event_for_interview(
            supabase, reply.get("user_id"), interview, company=company, position=title
        )
        result["calendar"] = calendar
    else:
        result["calendar"] = {"created": False, "status": "not_applicable", "error": ""}
    return result


def list_interviews(supabase, user_id: str, opportunity_id: str) -> List[Dict]:
    """Every interview recorded for ONE application, soonest first. Never raises."""
    try:
        result = supabase.table(INTERVIEW_TABLE).select("*") \
            .eq("user_id", user_id).eq("opportunity_id", opportunity_id) \
            .order("starts_at", asc=True).execute()
        return getattr(result, "data", None) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list interviews for %s: %s", opportunity_id, e)
        return []


def confirm_interview_timezone(
    supabase, user_id: str, interview_id: str, timezone_text: str
) -> Optional[Dict]:
    """
    Record the timezone the user confirmed, then create the calendar event.

    This is the only way an interview whose email had no stated timezone ever
    reaches a calendar, which is what keeps PART 14 honest.
    """
    try:
        result = supabase.table(INTERVIEW_TABLE).select("*") \
            .eq("id", interview_id).eq("user_id", user_id).limit(1).execute()
        rows = getattr(result, "data", None) or []
        if not rows:
            return None
        interview = rows[0]
        interview["timezone"] = (timezone_text or "").strip() or None
        interview["timezone_confirmed"] = True
        supabase.table(INTERVIEW_TABLE).update(
            {"timezone": interview["timezone"], "timezone_confirmed": True}
        ).eq("id", interview_id).eq("user_id", user_id).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not confirm timezone for %s: %s", interview_id, e)
        return None

    company, position = _reply_company_and_title({"opportunity_id": interview.get("opportunity_id")})
    calendar = create_calendar_event_for_interview(
        supabase, user_id, interview, company=company, position=position
    )
    interview["calendar_status"] = calendar.get("status")
    interview["calendar_event_id"] = calendar.get("event_id")
    return interview


def list_timeline_events(supabase, user_id: str, opportunity_id: str) -> List[Dict]:
    """Persisted workspace events for ONE application, oldest first. Never raises."""
    try:
        result = supabase.table(TIMELINE_TABLE).select("*") \
            .eq("user_id", user_id).eq("opportunity_id", opportunity_id) \
            .order("occurred_at", asc=True).execute()
        return getattr(result, "data", None) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list timeline for %s: %s", opportunity_id, e)
        return []


def get_application_workspace(supabase, user_id: str, opportunity_id: str) -> Dict:
    """
    Everything CareerPulse knows about ONE application.

    Every query is filtered by user_id AND opportunity_id, so two applications
    at the same company, or two similar job titles, can never bleed into each
    other. Nothing is derived from company name.

    Never raises: a missing table degrades that section only.
    """
    empty = {
        "opportunity_id": opportunity_id,
        "found": False,
        "overview": {},
        "timeline": [],
        "replies": [],
        "next_steps": [],
        "interviews": [],
        "warnings": [],
    }

    if not opportunity_id:
        return empty

    # Reuse the existing application view model for the header/status so there
    # is exactly one definition of "current status" in the product.
    application = None
    try:
        for candidate in reply_tracker.list_applications(supabase, user_id):
            if candidate.get("opportunity_id") == opportunity_id:
                application = candidate
                break
    except Exception as e:  # noqa: BLE001
        empty["warnings"].append(f"Overview unavailable: {e}")
        return empty

    if not application:
        return empty

    company = application.get("company") or ""
    title = application.get("title") or ""

    overview = dict(application)
    overview["company"] = company
    overview["position"] = title

    # Replies: scoped to this application only.
    try:
        reply_rows = reply_tracker.list_replies(supabase, user_id, limit=100)
        replies = [r for r in reply_rows if r.get("opportunity_id") == opportunity_id]
    except Exception as e:  # noqa: BLE001
        replies = []
        empty["warnings"].append(f"Replies unavailable: {e}")

    replies.sort(key=lambda r: str(r.get("received_at") or ""), reverse=True)

    # Next steps: newest first, only what Gemini actually extracted.
    next_steps: List[Dict] = []
    for reply in replies:
        steps = reply.get("next_steps") or []
        if not steps:
            continue
        next_steps.append({
            "reply_id": reply.get("id"),
            "sender_name": reply.get("sender_name"),
            "sender_email": reply.get("sender_email"),
            "subject": reply.get("subject"),
            "received_at": reply.get("received_at"),
            "steps": steps,
            "analysis_state": reply.get("analysis_state"),
            "analysis_error": reply.get("analysis_error"),
            "interview_at": reply.get("interview_at"),
            "interview_timezone": reply.get("interview_timezone"),
            "timezone_confirmed": reply.get("timezone_confirmed"),
        })

    # Timeline: the sent/replied spine comes from the existing tables so it can
    # never duplicate, and the workspace events are appended.
    timeline: List[Dict] = []
    try:
        sent_rows, _ = reply_tracker.load_sent_emails(supabase, user_id)
        for row in sent_rows:
            if row.get("opportunity_id") != opportunity_id:
                continue
            timeline.append({
                "event_type": "application_sent",
                "title": "Application submitted",
                "detail": row.get("subject") or "",
                "occurred_at": row.get("sent_at"),
                "source": "email_logs",
            })
    except Exception as e:  # noqa: BLE001
        empty["warnings"].append(f"Timeline (sent) unavailable: {e}")

    for reply in replies:
        mapped = _CLASSIFICATION_EVENTS.get(reply.get("classification") or "")
        if not mapped:
            continue
        event_type, event_title = mapped
        timeline.append({
            "event_type": event_type,
            "title": event_title,
            "detail": reply.get("classification_summary") or "",
            "occurred_at": reply.get("received_at"),
            "source": "email_replies",
        })

    for event in list_timeline_events(supabase, user_id, opportunity_id):
        timeline.append({
            "event_type": event.get("event_type"),
            "title": event.get("title"),
            "detail": event.get("detail"),
            "occurred_at": event.get("occurred_at"),
            "source": "application_timeline_events",
        })

    timeline.sort(key=lambda e: str(e.get("occurred_at") or ""))

    interviews = list_interviews(supabase, user_id, opportunity_id)

    # Connection flags so the UI can explain "Calendar connection required" or
    # "Gmail disconnected" instead of showing an empty Interview tab. No token
    # or secret is ever returned - only two booleans and the sync timestamps.
    gmail_connected = False
    calendar_available = False
    last_sync_at = None
    last_sync_error = None
    try:
        connection = reply_tracker.load_connection(supabase, user_id)
        if connection:
            gmail_connected = (
                connection.get("status") == "connected" and bool(connection.get("mailbox_email"))
            )
            calendar_available = calendar_client.has_calendar_grant(connection.get("scopes"))
            last_sync_at = connection.get("last_sync_at")
            last_sync_error = connection.get("last_sync_error")
    except Exception:  # noqa: BLE001 - never fail a detail page over status
        pass

    return {
        "opportunity_id": opportunity_id,
        "found": True,
        "overview": overview,
        "timeline": timeline,
        "replies": replies,
        "next_steps": next_steps,
        "interviews": interviews,
        "warnings": empty["warnings"],
        "gmail_connected": gmail_connected,
        "calendar_available": calendar_available,
        "last_sync_at": last_sync_at,
        "last_sync_error": last_sync_error,
    }


async def process_new_replies(supabase, user_id: str, reply_ids: Optional[List[str]] = None) -> Dict:
    """
    Analyse every reply that does not yet have grounded next steps.

    Called after a sync. Wrapped end to end so the caller can never fail because
    of the workspace, and capped so one large sync cannot fan out unbounded.
    """
    summary = {"processed": 0, "analyzed": 0, "failed": 0, "skipped": 0, "interviews": 0}
    try:
        query = supabase.table(REPLY_TABLE).select(
            "id, user_id, opportunity_id, subject, body_text, classification, "
            "classification_summary, received_at, next_steps, analysis_state"
        ).eq("user_id", user_id)

        if reply_ids:
            query = query.in_("id", reply_ids)
        else:
            query = query.is_("analysis_state", None)

        result = await asyncio.to_thread(lambda: query.limit(25).execute())
        rows = getattr(result, "data", None) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load replies for analysis: %s", e)
        return summary

    for reply in rows:
        summary["processed"] += 1
        try:
            # Duplicate protection (PART 10). A reply that has already been
            # analyzed is never sent to Gemini again, no matter how many times
            # a sync re-offers it. This is the check that makes repeated syncs
            # idempotent, and it also covers a reply whose analysis legitimately
            # produced zero steps.
            if (reply.get("analysis_state") or "").strip() == "analyzed":
                summary["skipped"] += 1
                continue
            # process_reply_analysis is synchronous (it performs blocking DB and
            # network work), so it must be offloaded rather than awaited.
            outcome = await asyncio.to_thread(process_reply_analysis, supabase, reply)
            state = outcome.get("state")
            if state == "analyzed":
                summary["analyzed"] += 1
                if outcome.get("interview"):
                    summary["interviews"] += 1
            elif state == "failed":
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Reply analysis failed for %s: %s", reply.get("id"), e)
            summary["failed"] += 1
    return summary
