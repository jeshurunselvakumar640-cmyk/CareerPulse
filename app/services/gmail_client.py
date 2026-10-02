"""
Gmail API client used ONLY to read company replies.

Design constraints:
- Sending remains on SMTP (app/services/email_service.py). This module never
  sends, modifies, labels, or deletes mail.
- OAuth is authorization-code with offline access so a refresh token is issued.
  Only read-only scopes are requested.
- No new third-party dependency: the Google endpoints are called over plain
  HTTPS with `requests`, matching the rest of the project's runtime.
- Raw provider errors are translated into a small set of safe error codes that
  the API layer maps to user-facing messages.
"""
import base64
import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Tuple

import requests

from app.config import settings

logger = logging.getLogger("careerpulse.mailbox.gmail")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

REQUEST_TIMEOUT = 30


class MailboxError(Exception):
    """Base error for mailbox operations."""

    code = "mailbox_error"


class MailboxNotConfigured(MailboxError):
    code = "not_configured"


class MailboxAuthError(MailboxError):
    code = "auth_error"


class MailboxRateLimited(MailboxError):
    code = "rate_limited"


class MailboxProviderError(MailboxError):
    code = "provider_error"


# -------------------------------------------------------------------
# DATA SHAPES
# -------------------------------------------------------------------

@dataclass
class ParsedMessage:
    """A single inbound message reduced to what CareerPulse needs."""

    provider: str
    provider_message_id: str
    thread_id: str
    internet_message_id: str
    in_reply_to: str
    references: List[str]
    sender_email: str
    sender_name: str
    recipient_email: str
    subject: str
    body_text: str
    snippet: str
    received_at: Optional[datetime]
    has_attachments: bool = False
    attachment_names: List[str] = field(default_factory=list)


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: Optional[datetime]
    scopes: str
    mailbox_email: str = ""


# -------------------------------------------------------------------
# OAUTH
# -------------------------------------------------------------------

def is_mailbox_oauth_configured() -> bool:
    return bool(settings.gmail_client_id and settings.gmail_client_secret)


def build_authorization_url(state: str, redirect_uri: str) -> str:
    """
    Build the Google consent URL.

    `access_type=offline` + a prompt that allows reuse means the user consents
    once and CareerPulse keeps a refresh token. `include_granted_scopes=true`
    lets a returning user approve only newly requested scopes.
    """
    if not is_mailbox_oauth_configured():
        raise MailboxNotConfigured(
            "Gmail OAuth is not configured on this server. Add GOOGLE_CLIENT_ID "
            "and GOOGLE_CLIENT_SECRET to the environment."
        )

    params = {
        "client_id": settings.gmail_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.gmail_scopes),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    from urllib.parse import urlencode

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _post_token(payload: Dict[str, str]) -> Dict:
    try:
        response = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise MailboxProviderError(f"Could not reach Google: {e}") from e

    if response.status_code == 200:
        try:
            return response.json()
        except ValueError as e:
            raise MailboxProviderError("Google returned an unreadable token response.") from e

    try:
        err = response.json()
    except ValueError:
        err = {}

    # Never surface raw provider text to the browser.
    logger.warning("Google token endpoint returned %s (%s)", response.status_code, err.get("error"))

    if response.status_code in (400, 401):
        raise MailboxAuthError(err.get("error_description") or "Google rejected the authorization request.")
    if response.status_code == 429:
        raise MailboxRateLimited("Google is rate limiting authorization requests.")
    raise MailboxProviderError("Google token exchange failed.")


def exchange_authorization_code(code: str, redirect_uri: str) -> TokenBundle:
    """Exchange an authorization code for tokens (first connection)."""
    if not is_mailbox_oauth_configured():
        raise MailboxNotConfigured("Gmail OAuth is not configured on this server.")

    data = _post_token({
        "code": code,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })

    access_token = data.get("access_token", "")
    if not access_token:
        raise MailboxAuthError("Google did not return an access token.")

    expires_in = data.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + _timedelta_seconds(expires_in)

    bundle = TokenBundle(
        access_token=access_token,
        refresh_token=data.get("refresh_token", ""),
        expires_at=expires_at,
        scopes=data.get("scope", ""),
    )
    bundle.mailbox_email = fetch_mailbox_email(access_token)
    return bundle


def refresh_access_token(refresh_token: str) -> TokenBundle:
    """Exchange a stored refresh token for a fresh access token."""
    if not refresh_token:
        raise MailboxAuthError("No stored refresh token. Please reconnect Gmail.")

    data = _post_token({
        "refresh_token": refresh_token,
        "client_id": settings.gmail_client_id,
        "client_secret": settings.gmail_client_secret,
        "grant_type": "refresh_token",
    })

    access_token = data.get("access_token", "")
    if not access_token:
        raise MailboxAuthError("Google did not return a new access token.")

    expires_in = data.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.now(timezone.utc) + _timedelta_seconds(expires_in)

    return TokenBundle(
        access_token=access_token,
        # Google omits refresh_token on refresh responses; keep the existing one.
        refresh_token=data.get("refresh_token", "") or refresh_token,
        expires_at=expires_at,
        scopes=data.get("scope", ""),
    )


def fetch_mailbox_email(access_token: str) -> str:
    """Resolve the connected mailbox address (read-only userinfo scope)."""
    try:
        response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            return (response.json() or {}).get("email", "")
    except requests.RequestException as e:
        logger.warning("Could not resolve mailbox address: %s", e)
    return ""


# -------------------------------------------------------------------
# API REQUESTS
# -------------------------------------------------------------------

def _gmail_request(method: str, path: str, access_token: str, **kwargs) -> Dict:
    url = f"{GMAIL_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(kwargs.pop("headers", {}) or {})

    try:
        response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise MailboxProviderError(f"Could not reach Gmail: {e}") from e

    if response.status_code == 200:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    if response.status_code in (401, 403):
        detail = ""
        try:
            detail = (response.json() or {}).get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        if "403" in str(response.status_code) and "rateLimitExceeded" in detail or "quotaExceeded" in detail:
            raise MailboxRateLimited("Gmail API quota reached. Try again later.")
        raise MailboxAuthError("Gmail rejected the request with this authorization.")
    if response.status_code == 404:
        raise MailboxProviderError("The Gmail message or thread no longer exists.")
    if response.status_code == 429:
        raise MailboxRateLimited("Gmail is rate limiting requests. Try again shortly.")

    logger.warning("Gmail API %s %s returned %s", method, path, response.status_code)
    raise MailboxProviderError("Gmail API request failed.")


def search_messages(access_token: str, query: str, max_results: int = 25) -> List[Dict]:
    """Run a Gmail search and return message stubs (id + threadId)."""
    if not query.strip():
        return []
    data = _gmail_request(
        "GET",
        "/messages",
        access_token,
        params={"q": query, "maxResults": max(1, min(int(max_results), 100))},
    )
    return data.get("messages", []) or []


def get_message(access_token: str, message_id: str) -> Dict:
    return _gmail_request("GET", f"/messages/{message_id}", access_token, params={"format": "full"})


def get_thread(access_token: str, thread_id: str) -> Dict:
    """Fetch a thread's message stubs, used to discover thread linkage."""
    return _gmail_request("GET", f"/threads/{thread_id}", access_token, params={"format": "metadata"})


def thread_message_ids(access_token: str, thread_id: str) -> List[str]:
    """
    Return the Gmail provider ids present in a thread.

    Used to confirm a reply belongs to the same thread as a CareerPulse-sent
    message even when the reply omits In-Reply-To/References.
    """
    try:
        thread = get_thread(access_token, thread_id)
    except MailboxError:
        return []
    return [m.get("id") for m in (thread.get("messages") or []) if m.get("id")]


# -------------------------------------------------------------------
# MIME PARSING
# -------------------------------------------------------------------

_QUOTED_PRINTABLE_SOFT_BREAK = re.compile(r"=\r?\n")
_HTML_SCRIPT_STYLE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_HTML_BREAK = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6])[^>]*>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_WHITESPACE = re.compile(r"[ \t]+")
_HTML_BLANK_LINES = re.compile(r"\n{3,}")


def _decode_body(data: str) -> str:
    """Gmail returns base64url without padding."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError) as e:
        logger.warning("Could not decode a message body segment: %s", e)
        return ""


def _decode_attachment_text(part: Dict) -> str:
    """Best-effort text extraction for a message that is only text/html."""
    body = part.get("body") or {}
    if body.get("attachmentId") or not body.get("data"):
        return ""
    return _decode_body(body.get("data", ""))


def html_to_text(markup: str) -> str:
    """
    Convert HTML mail into readable plain text.

    Incoming HTML is never rendered in the browser, so this conversion plus
    client-side escaping is the sanitization strategy.
    """
    if not markup:
        return ""
    text = _HTML_SCRIPT_STYLE.sub(" ", markup)
    text = _HTML_BREAK.sub("\n", text)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_WHITESPACE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _HTML_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _unfold(value: str) -> str:
    return (value or "").replace("\r\n", " ").replace("\n", " ").replace("\t", " ").strip()


def _extract_part_text(part: Dict) -> str:
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data") or ""

    if mime == "text/plain" and data:
        return _decode_body(data)
    if mime == "text/html" and data:
        return html_to_text(_decode_body(data))
    return ""


def _collect_text_and_attachments(parts: Iterable[Dict]) -> Tuple[str, List[str]]:
    plain_chunks: List[str] = []
    html_chunks: List[str] = []
    attachments: List[str] = []

    for part in parts or []:
        mime = (part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")

        if filename and (attachment_id or mime not in ("text/plain", "text/html")):
            # Metadata only: the file itself is never downloaded.
            attachments.append(filename)
            continue

        if mime == "multipart/alternative":
            nested_plain, nested_html, nested_attach = _collect_text_and_attachments(part.get("parts") or [])
            plain_chunks.append(nested_plain)
            html_chunks.append(nested_html)
            attachments.extend(nested_attach)
            continue

        if mime == "text/plain":
            text = _extract_part_text(part)
            if text:
                plain_chunks.append(text)
        elif mime == "text/html":
            text = _extract_part_text(part)
            if text:
                html_chunks.append(text)
        elif part.get("parts"):
            nested_plain, nested_html, nested_attach = _collect_text_and_attachments(part.get("parts") or [])
            plain_chunks.append(nested_plain)
            html_chunks.append(nested_html)
            attachments.extend(nested_attach)

    body_text = "\n\n".join(c.strip() for c in plain_chunks if c and c.strip())
    if not body_text:
        body_text = "\n\n".join(c.strip() for c in html_chunks if c and c.strip())
    return body_text, attachments


def _header_value(headers: List[Dict], name: str) -> str:
    target = name.lower()
    for header in headers or []:
        if (header.get("name") or "").lower() == target:
            return _unfold(header.get("value", ""))
    return ""


def _parse_address(value: str) -> Tuple[str, str]:
    """Split `Display Name <addr@host>` into (address, display name)."""
    if not value:
        return "", ""
    match = re.search(r"<?([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)>?", value)
    if not match:
        return "", value.strip()
    address = match.group(1)
    name = value[: match.start()].strip().strip('"').strip()
    if name.endswith(","):
        name = name[:-1]
    return address, name


def _parse_received_at(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_gmail_message(raw: Dict) -> Optional[ParsedMessage]:
    """Reduce a Gmail `messages.get` payload to a ParsedMessage."""
    if not raw or not raw.get("id"):
        return None

    try:
        payload = raw.get("payload") or {}
        headers = payload.get("headers") or []
        parts = payload.get("parts") or []
        body_text, attachment_names = _collect_text_and_attachments(parts)

        if not body_text:
            body_text = html_to_text(_decode_attachment_text(payload))

        internet_message_id = _header_value(headers, "Message-ID") or _header_value(headers, "Message-Id")
        in_reply_to = _header_value(headers, "In-Reply-To")
        references_raw = _header_value(headers, "References")
        references = [
            token
            for token in re.findall(r"<[^<>\s]+>", references_raw)
        ]
        if internet_message_id and not internet_message_id.startswith("<"):
            internet_message_id = f"<{internet_message_id.strip()}>"

        from_address, from_name = _parse_address(_header_value(headers, "From"))
        to_address, _ = _parse_address(_header_value(headers, "To"))

        return ParsedMessage(
            provider="gmail",
            provider_message_id=raw.get("id", ""),
            thread_id=raw.get("threadId", "") or "",
            internet_message_id=internet_message_id,
            in_reply_to=in_reply_to,
            references=references,
            sender_email=from_address,
            sender_name=from_name,
            recipient_email=to_address,
            subject=_header_value(headers, "Subject"),
            body_text=body_text,
            snippet=(raw.get("snippet") or "").strip(),
            received_at=_parse_received_at(_header_value(headers, "Date")),
            has_attachments=bool(attachment_names),
            attachment_names=[name for name in attachment_names if name][:10],
        )
    except Exception as e:  # defensive: a malformed message must not stop a sync
        logger.warning("Skipped a malformed Gmail message: %s", e)
        return None


def _timedelta_seconds(value):
    from datetime import timedelta
    return timedelta(seconds=float(value))
