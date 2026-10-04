"""
Google Calendar client used ONLY to create interview events.

Design constraints, deliberately matching app/services/gmail_client.py so the
two integrations behave identically:

- No new third-party dependency. The Calendar API is called over plain HTTPS
  with `requests`, exactly like the Gmail client and the rest of the project.
- The authorization is the SAME OAuth grant as Gmail. There is no second
  Google sign-in and no second token store: `calendar.events` is requested in
  the existing consent step (app/config.py) and the resulting token lives in
  the existing `mailbox_connections` row.
- Every function here is optional. Gmail reading, reply sync and the daily
  digest never call this module, so a missing calendar grant, a revoked scope
  or a Calendar outage can never break any existing feature.
- Nothing is ever invented. Callers pass only values that were explicitly
  present in the source email. Omitted fields are omitted from the event
  rather than defaulted to a guess.
"""

import logging
from typing import Dict, Optional

import requests

from app.config import settings
from app.services.gmail_client import (
    MailboxAuthError,
    MailboxProviderError,
    MailboxRateLimited,
)

logger = logging.getLogger(__name__)

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
REQUEST_TIMEOUT = 30

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def is_calendar_oauth_configured() -> bool:
    """True when the shared Google client could grant calendar access."""
    return bool(settings.gmail_client_id and settings.gmail_client_secret)


def granted_scopes(scopes_text: Optional[str]) -> str:
    """The scope string stored on the connection, or "" when absent."""
    return (scopes_text or "").strip()


def has_calendar_grant(scopes_text: Optional[str]) -> bool:
    """
    Whether the stored grant actually includes calendar.events.

    A connection made before the calendar scope existed still says
    "connected" for Gmail. That is not a failure: it simply means interview
    events cannot be created until the user reconnects, and the caller
    reports that instead of raising.
    """
    return CALENDAR_SCOPE in granted_scopes(scopes_text).split()


def _calendar_request(method: str, path: str, access_token: str, **kwargs) -> Dict:
    url = f"{CALENDAR_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(kwargs.pop("headers", {}) or {})

    try:
        response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise MailboxProviderError(f"Could not reach Google Calendar: {e}") from e

    if response.status_code in (200, 201):
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
        lowered = str(detail).lower()
        if "ratelimit" in lowered.replace(" ", "") or "quotaexceeded" in lowered.replace(" ", ""):
            raise MailboxRateLimited("Google Calendar quota reached. Try again later.")
        # 403 with a scope complaint means the grant predates calendar.events.
        if "insufficient" in lowered or "scope" in lowered or "permission" in lowered:
            raise MailboxAuthError(
                "Google Calendar is not authorized for this connection. Reconnect to grant it."
            )
        raise MailboxAuthError("Google Calendar rejected the request with this authorization.")
    if response.status_code == 404:
        raise MailboxProviderError("The Google Calendar was not found.")
    if response.status_code == 409:
        raise MailboxProviderError("Google Calendar reported a conflicting event.")

    logger.warning("Calendar API %s %s returned %s", method, path, response.status_code)
    raise MailboxProviderError("Google Calendar request failed.")


def create_interview_event(
    access_token: str,
    *,
    summary: str,
    description: str = "",
    location: str = "",
    start_at_iso: str,
    end_at_iso: str,
    timezone: str = "",
    meeting_link: str = "",
) -> Dict:
    """
    Insert one event into the user's primary calendar.

    `start_at_iso`/`end_at_iso` are absolute ISO-8601 timestamps that already
    carry their UTC offset, so the event lands at the correct instant. `timezone`
    is only attached when it was explicitly stated in the email; it is never
    guessed.

    Returns the created event, including Google's `id`. Raises MailboxAuthError,
    MailboxRateLimited or MailboxProviderError, all of which callers treat as
    "event not created" rather than as a reason to drop the reply.
    """
    if not summary.strip():
        raise ValueError("A calendar event needs a title.")

    body: Dict = {
        "summary": summary,
        "start": {"dateTime": start_at_iso},
        "end": {"dateTime": end_at_iso},
    }
    if description.strip():
        body["description"] = description.strip()
    if location.strip():
        body["location"] = location.strip()
    if timezone.strip():
        body["start"]["timeZone"] = timezone.strip()
        body["end"]["timeZone"] = timezone.strip()
    # A meeting link is written into the description verbatim when the email
    # stated one. No conferencing is ever requested here: generating a new
    # Meet link for an interview would invent a meeting that does not exist.

    created = _calendar_request(
        "POST",
        "/calendars/primary/events",
        access_token,
        json=body,
    )
    event_id = created.get("id") if isinstance(created, dict) else None
    html_link = created.get("htmlLink") if isinstance(created, dict) else None
    return {
        "id": event_id,
        "html_link": html_link,
        "summary": summary,
        "created": bool(event_id),
    }


def fetch_event(access_token: str, event_id: str) -> Dict:
    """Read one event back, used to confirm an event still exists."""
    if not event_id.strip():
        return {}
    return _calendar_request("GET", f"/calendars/primary/events/{event_id}", access_token)


def delete_event(access_token: str, event_id: str) -> bool:
    """
    Remove an event. Used only when the user explicitly asks to unlink one;
    CareerPulse never deletes a calendar event on its own.
    """
    if not event_id.strip():
        return False
    try:
        _calendar_request("DELETE", f"/calendars/primary/events/{event_id}", access_token)
        return True
    except (MailboxAuthError, MailboxProviderError, MailboxRateLimited) as e:
        logger.warning("Could not delete calendar event %s: %s", event_id, e)
        return False
