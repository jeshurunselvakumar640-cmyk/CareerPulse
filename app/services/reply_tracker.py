"""
Application communication tracking.

Responsibilities (all additive — nothing here changes how mail is sent):
- Persist a durable record of every outreach email CareerPulse sends,
  including a real RFC 5322 Message-ID.
- Read the user's connected mailbox and detect replies to those emails.
- Link each reply to the correct opportunity using the strongest available
  evidence (thread id → In-Reply-To → References → sender/subject fallback).
- Store each reply once (provider + provider message id is unique).
- Classify replies with Gemini without ever rewriting or auto-sending them.

Privacy: only messages addressed to a known CareerPulse recipient and sent
after the earliest recorded application are fetched. Anything that cannot be
linked to a tracked application is discarded, never stored and never shown.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.services import gmail_client
from app.services.gmail_client import (
    MailboxAuthError,
    MailboxError,
    MailboxNotConfigured,
    MailboxProviderError,
    MailboxRateLimited,
    ParsedMessage,
)
from app.services.mailbox_crypto import decrypt_token, encrypt_token

logger = logging.getLogger("careerpulse.mailbox.tracker")

# Bounded work per sync. A sync is a user-triggered or slow-interval job, never
# a tight poll loop.
MAX_SENT_EMAILS_SCANNED = 100
SENT_LOOKBACK_DAYS = 180
MAX_CANDIDATE_MESSAGES = 50
MAX_NEW_REPLIES_PER_SYNC = 25
MAX_SYNC_SECONDS = 90

CLASSIFICATIONS = [
    "positive_response",
    "interview_request",
    "interview_scheduled",
    "additional_information_requested",
    "application_received",
    "rejection",
    "follow_up_required",
    "generic_acknowledgement",
    "unknown",
]

# Application communication states. "Selected" is intentionally absent: a
# reply is never treated as an offer without a human reading it.
STATUS_NOT_CONTACTED = "Not Contacted"
STATUS_EMAIL_SENT = "Email Sent"
STATUS_REPLY_RECEIVED = "Reply Received"
STATUS_APPLICATION_RECEIVED = "Application Received"
STATUS_INTERVIEW_REQUESTED = "Interview Requested"
STATUS_INTERVIEW_SCHEDULED = "Interview Scheduled"
STATUS_INFO_REQUESTED = "Additional Information Requested"
STATUS_REJECTED = "Rejected"

_CLASSIFICATION_TO_STATUS = {
    "interview_request": STATUS_INTERVIEW_REQUESTED,
    "interview_scheduled": STATUS_INTERVIEW_SCHEDULED,
    "additional_information_requested": STATUS_INFO_REQUESTED,
    "rejection": STATUS_REJECTED,
    "application_received": STATUS_APPLICATION_RECEIVED,
    "positive_response": STATUS_REPLY_RECEIVED,
    "follow_up_required": STATUS_REPLY_RECEIVED,
    "generic_acknowledgement": STATUS_APPLICATION_RECEIVED,
    "unknown": STATUS_REPLY_RECEIVED,
}

# Which applications the AI has decided the user should look at next.
_ACTION_REQUIRED_CLASSIFICATIONS = {
    "interview_request",
    "interview_scheduled",
    "additional_information_requested",
    "follow_up_required",
}

MAILBOX_TABLE = "mailbox_connections"
REPLY_TABLE = "email_replies"
SENT_TABLE = "email_logs"

_ENRICHED_SENT_COLUMNS = (
    "id,user_id,opportunity_id,recipient_email,subject,body,status,sent_at,"
    "message_id,sender_email,thread_id,provider,provider_message_id,company,title"
)
_LEGACY_SENT_COLUMNS = "id,user_id,opportunity_id,recipient_email,subject,body,status,sent_at"

# Process-level capability cache. Supabase reports a missing table/column as a
# PostgREST error, so we probe once and remember the answer for this process.
_supports_enrichment: Dict[str, bool] = {}
_supports_replies: Dict[str, bool] = {}
_supports_connections: Dict[str, bool] = {}


class MigrationRequired(MailboxError):
    """A reply-tracking table is missing, so the migration has not been run."""

    code = "migration_required"

    def __init__(self, message: str, missing: str = ""):
        super().__init__(message)
        self.missing = missing


# -------------------------------------------------------------------
# SMALL HELPERS
# -------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _error_is_missing_relation(error: Exception) -> bool:
    message = str(error)
    return "PGRST205" in message or "does not exist" in message or "schema cache" in message


def _error_is_missing_column(error: Exception) -> bool:
    message = str(error)
    return "PGRST204" in message or "column" in message and "does not exist" in message


def _normalize_subject(subject: str) -> str:
    text = (subject or "").strip()
    text = re.sub(r"^(re|fw|fwd|aw|sv)\s*(\[\d+\])*\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _looks_like_missing_reply_table(error: Exception) -> bool:
    return REPLY_TABLE in str(error) and _error_is_missing_relation(error)


# -------------------------------------------------------------------
# SENT EMAIL RECORDS
# -------------------------------------------------------------------

def record_sent_email(
    supabase,
    user_id: str,
    opportunity_id: str,
    recipient_email: str,
    sender_email: str,
    subject: str,
    body: str,
    message_id: str,
    company: str = "",
    title: str = "",
    provider: str = "gmail",
) -> Dict:
    """
    Persist a sent outreach email.

    Uses the existing `email_logs` table (it already holds user_id,
    opportunity_id, recipient_email, subject, body, status, sent_at). If the
    migration that adds message_id/thread_id has not been applied, the legacy
    insert is used so sending never breaks.
    """
    base_payload = {
        "user_id": user_id,
        "opportunity_id": opportunity_id,
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "status": "Sent",
    }
    enriched_payload = {
        **base_payload,
        "sender_email": sender_email,
        "message_id": message_id,
        "provider": provider,
        "company": company,
        "title": title,
    }

    if _supports_enrichment.get(SENT_TABLE, True):
        try:
            result = supabase.table(SENT_TABLE).insert(enriched_payload).execute()
            _supports_enrichment[SENT_TABLE] = True
            rows = getattr(result, "data", None) or []
            return rows[0] if rows else enriched_payload
        except Exception as e:
            if not _error_is_missing_column(e):
                logger.warning("Sent email record insert failed: %s", e)
                return {}
            _supports_enrichment[SENT_TABLE] = False
            logger.info(
                "email_logs is missing the reply-tracking columns; using the legacy insert."
            )

    # Legacy shape — preserves the previous, already-working behaviour.
    try:
        result = supabase.table(SENT_TABLE).insert(base_payload).execute()
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else base_payload
    except Exception as e:
        logger.warning("Supabase sent email logging warning: %s", e)
        return {}


def link_sent_email_thread(supabase, sent_row_id: str, thread_id: str) -> None:
    """Backfill the Gmail thread id once a reply reveals the thread."""
    if not sent_row_id or not thread_id:
        return
    if not _supports_enrichment.get(SENT_TABLE, True):
        return
    try:
        supabase.table(SENT_TABLE).update({"thread_id": thread_id}).eq("id", sent_row_id).execute()
    except Exception as e:
        logger.debug("Could not backfill thread id: %s", e)


def load_sent_emails(supabase, user_id: str) -> Tuple[List[Dict], bool]:
    """
    Load the user's tracked sent emails (newest first).

    Returns (rows, supports_enrichment).
    """
    if _supports_enrichment.get(SENT_TABLE, True):
        try:
            result = (
                supabase.table(SENT_TABLE)
                .select(_ENRICHED_SENT_COLUMNS)
                .eq("user_id", user_id)
                .order("sent_at", desc=True)
                .limit(MAX_SENT_EMAILS_SCANNED)
                .execute()
            )
            _supports_enrichment[SENT_TABLE] = True
            return (result.data or []), True
        except Exception as e:
            if not _error_is_missing_column(e):
                logger.warning("Could not load sent emails: %s", e)
                return [], _supports_enrichment.get(SENT_TABLE, True)
            _supports_enrichment[SENT_TABLE] = False

    try:
        result = (
            supabase.table(SENT_TABLE)
            .select(_LEGACY_SENT_COLUMNS)
            .eq("user_id", user_id)
            .order("sent_at", desc=True)
            .limit(MAX_SENT_EMAILS_SCANNED)
            .execute()
        )
        return (result.data or []), False
    except Exception as e:
        logger.warning("Could not load sent emails: %s", e)
        return [], False


# -------------------------------------------------------------------
# MAILBOX CONNECTION
# -------------------------------------------------------------------

def mailbox_integration_status(supabase, user_id: str) -> Dict:
    """Never returns tokens — only what the UI needs to render the card."""
    status = {
        "configured": gmail_client.is_mailbox_oauth_configured(),
        "connected": False,
        "needs_reconnect": False,
        "provider": None,
        "email": None,
        "scopes": list(settings.gmail_scopes),
        "last_sync_at": None,
        "last_sync_error": None,
        "storage_available": True,
        "sent_email_tracking": bool(_supports_enrichment.get(SENT_TABLE, True)),
    }

    if not status["configured"]:
        return status

    connection = load_connection(supabase, user_id)
    if not connection:
        return status

    status.update({
        # A connection counts as connected only when it is active AND its Gmail
        # identity was verified. An unresolved address is reported as
        # not connected so the UI offers a reconnect instead of a false success.
        "connected": connection.get("status") == "connected" and bool(
            (connection.get("mailbox_email") or "").strip()
        ),
        "needs_reconnect": connection.get("status") == "needs_reconnect",
        "provider": connection.get("provider"),
        "email": (connection.get("mailbox_email") or "").strip() or None,
        "last_sync_at": connection.get("last_sync_at"),
        "last_sync_error": connection.get("last_sync_error"),
    })
    return status


def load_connection(supabase, user_id: str) -> Optional[Dict]:
    if not _supports_connections.get(MAILBOX_TABLE, True):
        return None
    try:
        result = (
            supabase.table(MAILBOX_TABLE)
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        _supports_connections[MAILBOX_TABLE] = True
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as e:
        if _error_is_missing_relation(e):
            _supports_connections[MAILBOX_TABLE] = False
            return None
        logger.warning("Could not read the mailbox connection: %s", e)
        return None


def save_connection(
    supabase,
    user_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: Optional[datetime],
    scopes: str,
    mailbox_email: str,
    provider: str = "gmail",
) -> None:
    """Store a mailbox connection with tokens encrypted at rest."""
    if not _supports_connections.get(MAILBOX_TABLE, True):
        raise MigrationRequired(
            "The mailbox integration tables have not been created yet.",
            missing=MAILBOX_TABLE,
        )

    payload = {
        "user_id": user_id,
        "provider": provider,
        # Never store a meaningless empty string; the column is nullable.
        "mailbox_email": (mailbox_email or "").strip() or None,
        # Never stored in plaintext.
        "access_token_encrypted": encrypt_token(access_token),
        "refresh_token_encrypted": encrypt_token(refresh_token),
        "token_expires_at": _iso(expires_at),
        "scopes": scopes,
        "status": "connected",
        "last_sync_at": None,
        "last_sync_error": None,
    }

    try:
        supabase.table(MAILBOX_TABLE).upsert(payload, on_conflict="user_id").execute()
    except Exception as e:
        if _error_is_missing_relation(e):
            _supports_connections[MAILBOX_TABLE] = False
            raise MigrationRequired(
                "The mailbox integration tables have not been created yet.",
                missing=MAILBOX_TABLE,
            ) from e
        logger.error("Could not save the mailbox connection: %s", e)
        raise MailboxProviderError("Could not save the mailbox connection.")


def update_connection_fields(supabase, user_id: str, fields: Dict) -> None:
    if not fields or not _supports_connections.get(MAILBOX_TABLE, True):
        return
    try:
        supabase.table(MAILBOX_TABLE).update(fields).eq("user_id", user_id).execute()
    except Exception as e:
        logger.debug("Mailbox connection update skipped: %s", e)


def disconnect_mailbox(supabase, user_id: str) -> bool:
    if not _supports_connections.get(MAILBOX_TABLE, True):
        return False
    try:
        supabase.table(MAILBOX_TABLE).delete().eq("user_id", user_id).execute()
        return True
    except Exception as e:
        if _error_is_missing_relation(e):
            _supports_connections[MAILBOX_TABLE] = False
            return False
        logger.warning("Could not remove the mailbox connection: %s", e)
        return False


def ensure_access_token(supabase, connection: Dict) -> str:
    """
    Return a usable access token, refreshing it when needed.

    On an invalid/expired/revoked grant the connection is flagged
    needs_reconnect so the UI can offer "Reconnect Gmail".
    """
    if not connection:
        raise MailboxAuthError("No mailbox is connected.")

    if connection.get("status") == "needs_reconnect":
        raise MailboxAuthError("Your Gmail connection needs to be renewed.")

    access_token = decrypt_token(connection.get("access_token_encrypted") or "")
    expires_at = _parse_iso(connection.get("token_expires_at"))
    now = _now()

    if not (access_token and (expires_at is None or expires_at > now + timedelta(minutes=2))):
        refresh_token = decrypt_token(connection.get("refresh_token_encrypted") or "")
        bundle = gmail_client.refresh_access_token(refresh_token)
        access_token = bundle.access_token
        update_connection_fields(supabase, connection["user_id"], {
            "access_token_encrypted": encrypt_token(bundle.access_token),
            "token_expires_at": _iso(bundle.expires_at),
            "status": "connected",
        })

    # Repair connections stored before the mailbox address was verified.
    # Only a real, non-empty address is written back.
    if not (connection.get("mailbox_email") or "").strip():
        try:
            resolved = gmail_client.fetch_mailbox_email(access_token)
        except MailboxError as e:
            logger.warning(
                "Could not resolve the mailbox address while repairing the "
                "connection (%s).", getattr(e, "code", "mailbox_error"),
            )
        else:
            if resolved:
                update_connection_fields(
                    supabase, connection["user_id"], {"mailbox_email": resolved}
                )
                connection["mailbox_email"] = resolved
                logger.info("Repaired the stored mailbox address for this connection.")

    return access_token


# -------------------------------------------------------------------
# REPLY MATCHING
# -------------------------------------------------------------------

def _row_message_ids(row: Dict) -> List[str]:
    raw = (row.get("message_id") or "").strip()
    if not raw:
        return []
    if not raw.startswith("<"):
        raw = f"<{raw}>"
    return [raw.lower()]


def _extract_header_ids(value: str) -> List[str]:
    if not value:
        return []
    found = re.findall(r"<[^<>\s]+>", value)
    if not found:
        cleaned = value.strip()
        if cleaned:
            found = [cleaned if cleaned.startswith("<") else f"<{cleaned}>"]
    return [item.lower() for item in found]


def _within_window(parsed: ParsedMessage, row: Dict, tolerance_days: int = 2) -> bool:
    """A reply should not precede the application it answers."""
    sent_at = _parse_iso(row.get("sent_at"))
    received = parsed.received_at
    if not sent_at or not received:
        return True
    return received >= sent_at - timedelta(days=tolerance_days)


def match_reply(
    sent_rows: List[Dict],
    parsed: ParsedMessage,
    thread_message_ids: Optional[List[str]] = None,
) -> Optional[Dict]:
    """
    Link a reply to the sent CareerPulse email it answers.

    Evidence is used strongest-first:
      1. Gmail thread id equality.
      2. In-Reply-To containing our stored Message-ID.
      3. References containing our stored Message-ID.
      4. Gmail thread membership (our provider message id in that thread).
      5. Sender + recipient + normalized subject, restricted to the newest
         matching application to that exact address.
    Returns the matched sent row, or None when nothing links confidently.
    """
    if not sent_rows:
        return None

    thread_ids = {t for t in (thread_message_ids or []) if t}

    # 1. Thread id
    if parsed.thread_id:
        for row in sent_rows:
            if row.get("thread_id") and row["thread_id"] == parsed.thread_id:
                return row

    # 2 & 3. In-Reply-To, then References
    header_ids: List[str] = []
    header_ids.extend(_extract_header_ids(parsed.in_reply_to))
    header_ids.extend([ref.lower() for ref in parsed.references])
    if header_ids:
        header_set = set(header_ids)
        for row in sent_rows:
            if header_set & set(_row_message_ids(row)):
                return row

    # 4. Thread membership
    if parsed.thread_id and thread_ids:
        for row in sent_rows:
            provider_id = row.get("provider_message_id")
            if provider_id and provider_id in thread_ids:
                return row

    # 5. Sender + recipient + subject, newest application to that address only.
    normalized = _normalize_subject(parsed.subject)
    if parsed.sender_email and normalized:
        by_recipient = [
            row for row in sent_rows
            if (row.get("recipient_email") or "").lower() == parsed.sender_email.lower()
        ]
        for row in by_recipient:  # already newest-first
            if _within_window(parsed, row) and _normalize_subject(row.get("subject")) == normalized:
                return row

    return None


# -------------------------------------------------------------------
# AI REPLY CLASSIFICATION
# -------------------------------------------------------------------

_UNKNOWN_CLASSIFICATION = {
    "classification": "unknown",
    "confidence": 0.0,
    "summary": "This reply was not analysed.",
    "requires_action": False,
    "requested_information": [],
    "suggested_status": STATUS_REPLY_RECEIVED,
}

_CLASSIFICATION_SCHEMA_HINT = """Return strict JSON with keys:
- "classification": one of %s
- "confidence": number between 0 and 1
- "summary": one short sentence describing what the sender is communicating
- "requires_action": boolean
- "requested_information": array of strings the candidate must provide
- "suggested_status": one of %s

Rules:
- Never rewrite, quote at length, or modify the original message.
- If the message is ambiguous, use "unknown" with low confidence.
- Never output an offer, selection, or acceptance unless the message states it.
- Never invent dates, names, or details that are not in the message.""" % (
    ", ".join(CLASSIFICATIONS),
    ", ".join([STATUS_REPLY_RECEIVED, STATUS_APPLICATION_RECEIVED, STATUS_INTERVIEW_REQUESTED,
               STATUS_INTERVIEW_SCHEDULED, STATUS_INFO_REQUESTED, STATUS_REJECTED]),
)


def _coerce_classification(raw: Dict) -> Dict:
    classification = str(raw.get("classification") or "").strip().lower()
    if classification not in CLASSIFICATIONS:
        classification = "unknown"

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    if classification == "unknown":
        confidence = min(confidence, 0.4)

    requested = raw.get("requested_information")
    if not isinstance(requested, list):
        requested = []
    requested = [str(item).strip()[:200] for item in requested if str(item).strip()][:10]

    summary = str(raw.get("summary") or "").strip()[:400] or "No summary available."

    suggested = _CLASSIFICATION_TO_STATUS.get(classification, STATUS_REPLY_RECEIVED)

    return {
        "classification": classification,
        "confidence": confidence,
        "summary": summary,
        "requires_action": bool(
            raw.get("requires_action")
            or classification in _ACTION_REQUIRED_CLASSIFICATIONS
        ),
        "requested_information": requested,
        "suggested_status": suggested,
    }


def classify_reply(subject: str, body_text: str, opportunity_title: str, company: str) -> Dict:
    """
    Ask Gemini what the reply means.

    Any failure returns the neutral "unknown" classification. The reply itself
    is always stored and displayed regardless of this result.
    """
    if not settings.gemini_api_key:
        return dict(_UNKNOWN_CLASSIFICATION)

    prompt = f"""You are analysing a recruiter or company reply to a job application
email that CareerPulse sent on behalf of a candidate. Classify the reply only.

Opportunity: {opportunity_title}
Company: {company}
Reply subject: {subject}

Reply body:
{body_text[:6000]}

{_CLASSIFICATION_SCHEMA_HINT}"""

    def _call() -> Dict:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.primary_model or "gemini-2.5-flash",
            contents=prompt,
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("```",1)[1]
            if text.lower().startswith("json"):
                text = text[4:]
        return json.loads(text.strip())

    try:
        return _coerce_classification(_call())
    except Exception as e:
        logger.warning("Reply classification unavailable, defaulting to unknown: %s", e)
        return dict(_UNKNOWN_CLASSIFICATION)


async def classify_reply_async(subject: str, body_text: str, opportunity_title: str, company: str) -> Dict:
    """Gemini classification without blocking the sync loop."""
    return await asyncio.to_thread(classify_reply, subject, body_text, opportunity_title, company)


# -------------------------------------------------------------------
# SYNC
# -------------------------------------------------------------------

def _build_gmail_query(sent_rows: List[Dict]) -> str:
    """
    Build a search that only touches mail relevant to tracked applications.

    It is bounded by the earliest application date and by the known recipients,
    so unrelated personal mail is never requested from Gmail.
    """
    recipients = []
    for row in sent_rows:
        address = (row.get("recipient_email") or "").strip().lower()
        if address and address not in recipients:
            recipients.append(address)

    if not recipients:
        return ""

    oldest = None
    for row in sent_rows:
        sent_at = _parse_iso(row.get("sent_at"))
        if sent_at and (oldest is None or sent_at < oldest):
            oldest = sent_at

    if oldest is None:
        return ""

    # One day of slack so a reply sent in the sender's timezone still matches.
    after = (oldest - timedelta(days=1)).astimezone(timezone.utc)
    after_token = after.strftime("%Y/%m/%d")

    from_clause = " OR ".join(f"from:{address}" for address in recipients)
    return f"in:anywhere after:{after_token} ({from_clause})"


@dataclass
class SyncReport:
    status: str = "success"
    checked: int = 0
    matched: int = 0
    inserted: int = 0
    duplicates: int = 0
    unlinked: int = 0
    failed: int = 0
    skipped: bool = False
    message: str = ""
    unread_count: int = 0
    error_code: str = ""
    classification_failures: int = 0
    new_reply_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "status": self.status,
            "checked": self.checked,
            "matched": self.matched,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "unlinked": self.unlinked,
            "failed": self.failed,
            "skipped": self.skipped,
            "message": self.message,
            "unread_count": self.unread_count,
            "error_code": self.error_code,
            "new_reply_ids": self.new_reply_ids,
        }


def _existing_provider_ids(supabase, user_id: str) -> set:
    if not _supports_replies.get(REPLY_TABLE, True):
        return set()
    try:
        result = (
            supabase.table(REPLY_TABLE)
            .select("provider_message_id")
            .eq("user_id", user_id)
            .limit(2000)
            .execute()
        )
        _supports_replies[REPLY_TABLE] = True
        return {row.get("provider_message_id") for row in (result.data or []) if row.get("provider_message_id")}
    except Exception as e:
        if _error_is_missing_relation(e):
            _supports_replies[REPLY_TABLE] = False
            return set()
        logger.warning("Could not read existing replies: %s", e)
        return set()


def _insert_reply(supabase, payload: Dict) -> Optional[Dict]:
    if not _supports_replies.get(REPLY_TABLE, True):
        raise MigrationRequired(
            "The reply tracking tables have not been created yet.",
            missing=REPLY_TABLE,
        )
    try:
        result = supabase.table(REPLY_TABLE).insert(payload).execute()
        _supports_replies[REPLY_TABLE] = True
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else None
    except Exception as e:
        # Unique violation on (provider, provider_message_id) = already synced.
        if "23505" in str(e) or "duplicate key" in str(e).lower():
            return None
        if _error_is_missing_relation(e):
            _supports_replies[REPLY_TABLE] = False
            raise MigrationRequired(
                "The reply tracking tables have not been created yet.",
                missing=REPLY_TABLE,
            ) from e
        logger.warning("Could not store a reply: %s", e)
        return None


async def sync_replies(supabase, user_id: str, force: bool = False) -> Dict:
    """
    Check the connected mailbox for new replies to CareerPulse applications.

    Never raises for provider problems: the returned report carries a safe
    message the UI can render verbatim.
    """
    report = SyncReport()

    if not gmail_client.is_mailbox_oauth_configured():
        report.status = "error"
        report.error_code = MailboxNotConfigured.code
        report.message = (
            "Gmail integration is not configured on this server. "
            "An administrator needs to add the Google OAuth credentials."
        )
        return report.to_dict()

    connection = load_connection(supabase, user_id)
    if not connection:
        report.status = "error"
        report.error_code = "not_connected"
        report.message = "Connect your Gmail account to receive company replies."
        return report.to_dict()

    if not _supports_replies.get(REPLY_TABLE, True):
        report.status = "error"
        report.error_code = "migration_required"
        report.message = "Reply tracking is not available until the database migration is applied."
        return report.to_dict()

    # Throttle automatic syncs so opening the app cannot hammer Gmail.
    if not force:
        last_sync = _parse_iso(connection.get("last_sync_at"))
        interval = timedelta(seconds=max(30, settings.mailbox_sync_min_interval))
        if last_sync and (_now() - last_sync) < interval:
            report.status = "success"
            report.skipped = True
            report.message = "Checked recently. Use Sync Replies to check again now."
            return report.to_dict()

    try:
        access_token = ensure_access_token(supabase, connection)
    except MailboxNotConfigured as e:
        report.status = "error"
        report.error_code = e.code
        report.message = "Gmail integration is not configured on this server."
        return report.to_dict()
    except MailboxAuthError as e:
        mark_needs_reconnect(supabase, user_id)
        report.status = "error"
        report.error_code = "auth_error"
        report.message = "Your Gmail connection needs to be renewed. Please reconnect Gmail."
        logger.info("Mailbox sync needs reconnect: %s", e)
        return report.to_dict()
    except MailboxError as e:
        report.status = "error"
        report.error_code = e.code
        report.message = "Gmail could not be reached. Please try again shortly."
        logger.warning("Mailbox token refresh failed: %s", e)
        return report.to_dict()

    sent_rows, _ = load_sent_emails(supabase, user_id)
    if not sent_rows:
        report.status = "success"
        report.message = "No outreach emails have been sent from CareerPulse yet."
        update_connection_fields(supabase, user_id, {
            "last_sync_at": _iso(_now()),
            "last_sync_error": None,
        })
        return report.to_dict()

    query = _build_gmail_query(sent_rows)
    if not query:
        report.status = "success"
        report.message = "No relevant mailbox activity to check."
        return report.to_dict()

    mailbox_email = (connection.get("mailbox_email") or "").lower()
    existing_ids = _existing_provider_ids(supabase, user_id)
    sent_provider_ids = {row.get("provider_message_id") for row in sent_rows if row.get("provider_message_id")}

    try:
        candidates = gmail_client.search_messages(access_token, query, MAX_CANDIDATE_MESSAGES)
    except MailboxAuthError:
        mark_needs_reconnect(supabase, user_id)
        report.status = "error"
        report.error_code = "auth_error"
        report.message = "Your Gmail connection needs to be renewed. Please reconnect Gmail."
        return report.to_dict()
    except MailboxRateLimited:
        report.status = "error"
        report.error_code = "rate_limited"
        report.message = "Gmail is busy right now. Please try again in a few minutes."
        return report.to_dict()
    except MailboxError as e:
        report.status = "error"
        report.error_code = e.code
        report.message = "Gmail could not be reached. Please try again shortly."
        logger.warning("Gmail search failed: %s", e)
        return report.to_dict()

    report.checked = len(candidates)
    thread_cache: Dict[str, List[str]] = {}

    for stub in candidates[:MAX_CANDIDATE_MESSAGES]:
        if report.inserted >= MAX_NEW_REPLIES_PER_SYNC:
            break

        provider_id = stub.get("id")
        if not provider_id:
            continue
        # Duplicate prevention: skip anything already stored or already seen.
        if provider_id in existing_ids or provider_id in sent_provider_ids:
            report.duplicates += 1
            continue

        try:
            raw = gmail_client.get_message(access_token, provider_id)
        except MailboxError as e:
            report.failed += 1
            logger.debug("Skipped an unreadable Gmail message %s: %s", provider_id, e)
            continue

        parsed = gmail_client.parse_gmail_message(raw)
        if not parsed:
            report.failed += 1
            continue

        # Never treat the user's own mail as a company reply.
        if mailbox_email and parsed.sender_email.lower() == mailbox_email:
            continue
        if not parsed.sender_email:
            report.unlinked += 1
            continue

        if parsed.thread_id and parsed.thread_id not in thread_cache:
            try:
                thread_cache[parsed.thread_id] = gmail_client.thread_message_ids(access_token, parsed.thread_id)
            except MailboxError:
                thread_cache[parsed.thread_id] = []

        matched = match_reply(sent_rows, parsed, thread_cache.get(parsed.thread_id, []))
        if not matched:
            # Privacy: unlinkable mail is discarded, never stored.
            report.unlinked += 1
            continue

        report.matched += 1
        opportunity_id = matched.get("opportunity_id") or ""
        company = matched.get("company") or _company_from_opportunity(opportunity_id)
        title = matched.get("title") or _title_from_opportunity(opportunity_id)

        classification = await classify_reply_async(
            parsed.subject, parsed.body_text, title, company
        )
        if classification.get("classification") == "unknown" and settings.gemini_api_key:
            report.classification_failures += 1

        payload = {
            "user_id": user_id,
            "opportunity_id": opportunity_id,
            "sent_email_id": matched.get("id"),
            "thread_id": parsed.thread_id or None,
            "provider": parsed.provider,
            "provider_message_id": parsed.provider_message_id,
            "internet_message_id": parsed.internet_message_id or None,
            "sender_email": parsed.sender_email,
            "sender_name": parsed.sender_name or None,
            "recipient_email": parsed.recipient_email or mailbox_email,
            "subject": parsed.subject,
            "body_text": parsed.body_text[:20000],
            "received_at": _iso(parsed.received_at or _now()),
            "is_read": False,
            "has_attachments": parsed.has_attachments,
            "attachment_names": parsed.attachment_names,
            "classification": classification.get("classification"),
            "classification_confidence": classification.get("confidence"),
            "classification_summary": classification.get("summary"),
            "requires_action": classification.get("requires_action"),
            "requested_information": classification.get("requested_information"),
            "suggested_status": classification.get("suggested_status"),
            "match_method": _match_method(matched, parsed),
        }

        try:
            inserted = _insert_reply(supabase, payload)
        except MigrationRequired as e:
            report.status = "error"
            report.error_code = e.code
            report.message = "Reply tracking is not available until the database migration is applied."
            return report.to_dict()

        if inserted is None:
            report.duplicates += 1
            existing_ids.add(provider_id)
            continue

        if not matched.get("thread_id") and parsed.thread_id:
            link_sent_email_thread(supabase, matched.get("id"), parsed.thread_id)
            matched["thread_id"] = parsed.thread_id

        report.inserted += 1
        existing_ids.add(provider_id)
        if inserted.get("id"):
            report.new_reply_ids.append(inserted["id"])

    update_connection_fields(supabase, user_id, {
        "last_sync_at": _iso(_now()),
        "last_sync_error": None,
    })
    report.unread_count = count_unread_replies(supabase, user_id)
    if report.inserted == 0 and report.unlinked == 0:
        report.message = "No new company replies since the last check."
    return report.to_dict()


def _match_method(matched: Dict, parsed: ParsedMessage) -> str:
    if matched.get("thread_id") and parsed.thread_id == matched.get("thread_id"):
        return "thread_id"
    header_ids = set(_extract_header_ids(parsed.in_reply_to)) | {
        ref.lower() for ref in parsed.references
    }
    if header_ids & set(_row_message_ids(matched)):
        return "in_reply_to" if parsed.in_reply_to else "references"
    if parsed.thread_id and matched.get("provider_message_id"):
        return "thread_membership"
    return "headers_fallback"


def _company_from_opportunity(opportunity_id: str) -> str:
    if not opportunity_id:
        return ""
    company, _, title = opportunity_id.partition("_")
    return company.replace("_", " ").strip().title()


def _title_from_opportunity(opportunity_id: str) -> str:
    if not opportunity_id:
        return ""
    _, _, title = opportunity_id.partition("_")
    return title.replace("_", " ").strip().title()


def mark_needs_reconnect(supabase, user_id: str) -> None:
    update_connection_fields(supabase, user_id, {"status": "needs_reconnect"})


# -------------------------------------------------------------------
# READ MODELS
# -------------------------------------------------------------------

_REPLY_SELECT = (
    "id,user_id,opportunity_id,sent_email_id,thread_id,provider,provider_message_id,"
    "internet_message_id,sender_email,sender_name,recipient_email,subject,body_text,"
    "received_at,is_read,has_attachments,attachment_names,classification,"
    "classification_confidence,classification_summary,requires_action,"
    "requested_information,suggested_status,match_method,created_at"
)


def _replies_table_ready(supabase) -> bool:
    if _supports_replies.get(REPLY_TABLE, True):
        try:
            supabase.table(REPLY_TABLE).select("id").limit(1).execute()
            _supports_replies[REPLY_TABLE] = True
        except Exception as e:
            if _error_is_missing_relation(e):
                _supports_replies[REPLY_TABLE] = False
            else:
                _supports_replies[REPLY_TABLE] = True
    return _supports_replies.get(REPLY_TABLE, False)


def count_unread_replies(supabase, user_id: str) -> int:
    if not _replies_table_ready(supabase):
        return 0
    try:
        result = (
            supabase.table(REPLY_TABLE)
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("is_read", False)
            .execute()
        )
        return getattr(result, "count", None) or 0
    except Exception as e:
        logger.debug("Unread reply count unavailable: %s", e)
        return 0


def list_replies(supabase, user_id: str, limit: int = 50, unread_only: bool = False) -> List[Dict]:
    if not _replies_table_ready(supabase):
        return []
    try:
        query = (
            supabase.table(REPLY_TABLE)
            .select(_REPLY_SELECT)
            .eq("user_id", user_id)
            .order("received_at", desc=True)
            .limit(max(1, min(int(limit), 200)))
        )
        if unread_only:
            query = query.eq("is_read", False)
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.warning("Could not list replies: %s", e)
        return []


def get_reply(supabase, user_id: str, reply_id: str) -> Optional[Dict]:
    """Ownership is enforced server-side: replies are always filtered by user."""
    if not _replies_table_ready(supabase):
        return None
    try:
        result = (
            supabase.table(REPLY_TABLE)
            .select(_REPLY_SELECT)
            .eq("id", reply_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning("Could not load a reply: %s", e)
        return None


def mark_reply_read(supabase, user_id: str, reply_id: str) -> bool:
    if not _replies_table_ready(supabase):
        return False
    try:
        supabase.table(REPLY_TABLE).update({"is_read": True}).eq("id", reply_id).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logger.warning("Could not mark a reply as read: %s", e)
        return False


def list_applications(supabase, user_id: str) -> List[Dict]:
    """
    Build the Applications list: one entry per tracked opportunity with its
    latest communication state.
    """
    sent_rows, _ = load_sent_emails(supabase, user_id)
    replies = list_replies(supabase, user_id, limit=200)

    replies_by_opportunity: Dict[str, List[Dict]] = {}
    for reply in replies:
        replies_by_opportunity.setdefault(reply.get("opportunity_id") or "", []).append(reply)

    applications: Dict[str, Dict] = {}

    for row in sent_rows:
        opportunity_id = row.get("opportunity_id") or ""
        if not opportunity_id:
            continue
        sent_at = _parse_iso(row.get("sent_at"))
        existing = applications.get(opportunity_id)
        if existing is None or (sent_at and (existing.get("_sent_at") is None or sent_at > existing["_sent_at"])):
            applications[opportunity_id] = {
                "opportunity_id": opportunity_id,
                "company": row.get("company") or _company_from_opportunity(opportunity_id),
                "title": row.get("title") or _title_from_opportunity(opportunity_id),
                "status": STATUS_EMAIL_SENT,
                "last_sent_at": _iso(sent_at),
                "recipient_email": row.get("recipient_email"),
                "subject": row.get("subject"),
                "sent_count": 0,
                "reply_count": 0,
                "unread_count": 0,
                "last_reply_at": None,
                "last_reply_preview": None,
                "classification": None,
                "_sent_at": sent_at,
            }

        applications[opportunity_id]["sent_count"] += 1

    for opportunity_id, group in replies_by_opportunity.items():
        if not opportunity_id:
            continue
        entry = applications.get(opportunity_id)
        if entry is None:
            first = group[0]
            entry = {
                "opportunity_id": opportunity_id,
                "company": _company_from_opportunity(opportunity_id),
                "title": _title_from_opportunity(opportunity_id),
                "status": STATUS_REPLY_RECEIVED,
                "last_sent_at": None,
                "recipient_email": first.get("sender_email"),
                "subject": first.get("subject"),
                "sent_count": 0,
                "reply_count": 0,
                "unread_count": 0,
                "last_reply_at": None,
                "last_reply_preview": None,
                "classification": None,
                "_sent_at": None,
            }
            applications[opportunity_id] = entry

        latest = group[0]
        entry["reply_count"] += len(group)
        entry["unread_count"] += sum(1 for r in group if not r.get("is_read"))
        entry["last_reply_at"] = latest.get("received_at")
        entry["last_reply_preview"] = (latest.get("classification_summary") or latest.get("body_text") or "")[:220]
        entry["classification"] = latest.get("classification")
        suggested = latest.get("suggested_status") or _CLASSIFICATION_TO_STATUS.get(
            latest.get("classification") or "unknown", STATUS_REPLY_RECEIVED
        )
        if entry["sent_count"] == 0:
            entry["status"] = suggested
        else:
            # A reply is only allowed to advance the state, never to downgrade
            # a rejection/interview signal back to a generic "sent".
            if suggested != STATUS_REJECTED or entry["status"] in (STATUS_EMAIL_SENT, STATUS_REPLY_RECEIVED):
                entry["status"] = suggested

    for entry in applications.values():
        entry.pop("_sent_at", None)

    return sorted(
        applications.values(),
        key=lambda item: (item.get("last_reply_at") or item.get("last_sent_at") or ""),
        reverse=True,
    )


def get_conversation(supabase, user_id: str, opportunity_id: str) -> Dict:
    """
    Return the chronological message history for one opportunity: the outreach
    emails CareerPulse sent plus every reply linked to them.
    """
    sent_rows, _ = load_sent_emails(supabase, user_id)
    messages: List[Dict] = []

    for row in sent_rows:
        if (row.get("opportunity_id") or "") != opportunity_id:
            continue
        messages.append({
            "direction": "outbound",
            "id": row.get("id"),
            "subject": row.get("subject"),
            "body_text": row.get("body"),
            "to_email": row.get("recipient_email"),
            "from_email": row.get("sender_email"),
            "timestamp": _iso(_parse_iso(row.get("sent_at"))),
            "message_id": row.get("message_id"),
            "status": row.get("status"),
        })

    replies = list_replies(supabase, user_id, limit=200)
    for reply in replies:
        if (reply.get("opportunity_id") or "") != opportunity_id:
            continue
        messages.append({
            "direction": "inbound",
            "id": reply.get("id"),
            "subject": reply.get("subject"),
            "body_text": reply.get("body_text"),
            "to_email": reply.get("recipient_email"),
            "from_email": reply.get("sender_email"),
            "from_name": reply.get("sender_name"),
            "timestamp": reply.get("received_at"),
            "message_id": reply.get("internet_message_id"),
            "is_read": reply.get("is_read"),
            "has_attachments": reply.get("has_attachments"),
            "attachment_names": reply.get("attachment_names") or [],
            "classification": reply.get("classification"),
            "classification_confidence": reply.get("classification_confidence"),
            "classification_summary": reply.get("classification_summary"),
            "requires_action": reply.get("requires_action"),
            "requested_information": reply.get("requested_information") or [],
            "suggested_status": reply.get("suggested_status"),
            "match_method": reply.get("match_method"),
        })

    messages.sort(key=lambda m: (m.get("timestamp") or ""))

    application = next(
        (item for item in list_applications(supabase, user_id) if item["opportunity_id"] == opportunity_id),
        {
            "opportunity_id": opportunity_id,
            "company": _company_from_opportunity(opportunity_id),
            "title": _title_from_opportunity(opportunity_id),
            "status": STATUS_REPLY_RECEIVED if messages else STATUS_NOT_CONTACTED,
        },
    )

    return {
        "opportunity_id": opportunity_id,
        "application": application,
        "messages": messages,
    }


# -------------------------------------------------------------------
# AI RESPONSE DRAFT (never auto-sent)
# -------------------------------------------------------------------

PLACEHOLDER_NOTICE = (
    "I can draft a response, but I need you to fill in any scheduling details "
    "yourself. Nothing has been sent."
)

_RESPONSE_DRAFT_PROMPT = """You help a candidate write a reply to a recruiter.
Write a draft the candidate can edit. You must never send anything yourself.

Constraints:
- Use ONLY facts present below. Never invent availability, dates, times,
  achievements, skills, experience, company facts, or recruiter names.
- If the reply asks for availability, times, documents, or a call slot, insert
  the placeholder "[ADD YOUR AVAILABILITY]" or "[ATTACH THE REQUESTED DOCUMENT]"
  instead of inventing a value.
- Keep it under 200 words, professional and concise.
- Do not include a subject line or any commentary about these instructions.

Return strict JSON with keys "subject" and "body"."""

_FALLBACK_DRAFT = {
    "subject": "",
    "body": (
        "Dear {salutation},\n\n"
        "Thank you for your response regarding the {title} role at {company}.\n\n"
        "[ADD YOUR RESPONSE HERE — for example your availability, or the "
        "documents or details the company asked for.]\n\n"
        "Regards,\n{candidate}"
    ),
}


async def draft_reply_response(
    supabase,
    user_id: str,
    opportunity_id: str,
    user_metadata: Dict,
) -> Dict:
    """
    Generate an editable draft response to the latest company reply.

    The draft is only returned to the user; nothing is sent. The user must
    review it and explicitly use the existing email composer to send.
    """
    conversation = get_conversation(supabase, user_id, opportunity_id)
    messages = conversation.get("messages") or []
    inbound = [m for m in messages if m.get("direction") == "inbound"]
    outbound = [m for m in messages if m.get("direction") == "outbound"]

    if not inbound:
        return {
            "status": "error",
            "detail": "There is no company reply to respond to yet.",
        }

    latest_reply = inbound[-1]
    original = outbound[0] if outbound else {}
    application = conversation.get("application") or {}
    company = application.get("company") or "the company"
    title = application.get("title") or "the role"
    candidate = (user_metadata or {}).get("full_name") or (user_metadata or {}).get("name") or "Candidate"

    if not settings.gemini_api_key:
        body = _FALLBACK_DRAFT["body"].format(
            salutation="Hiring Team",
            title=title,
            company=company,
            candidate=candidate,
        )
        return {
            "status": "success",
            "subject": f"Re: {latest_reply.get('subject') or ''}".strip(),
            "body": body,
            "notice": PLACEHOLDER_NOTICE,
            "source": "template",
        }

    prompt = f"""{_RESPONSE_DRAFT_PROMPT}

Candidate name: {candidate}
Candidate email: {(user_metadata or {}).get('email', '')}
Candidate skills: {', '.join((user_metadata or {}).get('skills') or [])}
Opportunity: {title}
Company: {company}

Original application email sent by CareerPulse:
Subject: {original.get('subject', '')}
{str(original.get('body_text') or '')[:3000]}

Company reply to respond to:
Subject: {latest_reply.get('subject', '')}
{str(latest_reply.get('body_text') or '')[:3000]}"""

    def _call() -> Dict:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.primary_model or "gemini-2.5-flash",
            contents=prompt,
        )
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("```", 1)[1]
            if text.lower().startswith("json"):
                text = text[4:]
        return json.loads(text.strip())

    try:
        parsed = _call()
        subject = str(parsed.get("subject") or "").strip() or f"Re: {latest_reply.get('subject') or ''}".strip()
        body = str(parsed.get("body") or "").strip()
        if not body:
            raise ValueError("empty draft body")
    except Exception as e:
        logger.warning("AI response draft unavailable, using the safe template: %s", e)
        return {
            "status": "success",
            "subject": f"Re: {latest_reply.get('subject') or ''}".strip(),
            "body": _FALLBACK_DRAFT["body"].format(
                salutation="Hiring Team",
                title=title,
                company=company,
                candidate=candidate,
            ),
            "notice": PLACEHOLDER_NOTICE,
            "source": "template",
        }

    notice = PLACEHOLDER_NOTICE
    if "[ADD YOUR" in body.upper() or "[" in body:
        notice = (
            "This draft contains a placeholder you must complete. "
            "CareerPulse never fills in availability or attachments for you."
        )

    return {
        "status": "success",
        "subject": subject,
        "body": body,
        "notice": notice,
        "source": "gemini",
    }
