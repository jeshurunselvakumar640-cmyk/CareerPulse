import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime, make_msgid
from typing import Dict, Any, Optional

from app.config import settings


def build_message_id() -> str:
    """
    Generate a unique RFC 5322 Message-ID for an outbound message.

    A real Message-ID is what lets a later company reply reference this exact
    email (In-Reply-To / References), so it must be unique per message.
    """
    return make_msgid(idstring=f"careerpulse-{uuid.uuid4().hex}", domain="careerpulse.local")


def _build_message(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    message_id: Optional[str] = None,
) -> MIMEMultipart:
    """
    Builds a multipart/alternative message when an HTML part is supplied,
    otherwise a plain-text-only message (unchanged legacy behavior).
    """
    if html_body:
        message = MIMEMultipart("alternative")
        # Plain text must be the FIRST part for correct client fallback.
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        message = MIMEText(body, "plain", "utf-8")

    message["From"] = settings.smtp_user
    message["To"] = to_email
    message["Subject"] = subject

    # Threading metadata. The Message-ID is unique per send; the Date header is
    # explicit so the stored record matches what the recipient receives.
    message["Message-ID"] = message_id or build_message_id()
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["MIME-Version"] = "1.0"
    return message


def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """
    Send an email using Gmail SMTP and MIME.
    When html_body is provided the message is multipart/alternative
    (text/plain + text/html). SMTP transport and credentials are unchanged.

    Returns the Message-ID that was used, so the caller can store it and match
    the company's reply later.
    """

    smtp_host = settings.smtp_host
    smtp_port = settings.smtp_port
    smtp_user = settings.smtp_user
    smtp_password = settings.smtp_password

    if not smtp_user:
        raise ValueError(
            "SMTP_USER is not configured"
        )

    if not smtp_password:
        raise ValueError(
            "SMTP_PASSWORD is not configured"
        )

    if not to_email:
        raise ValueError(
            "Recipient email is required"
        )

    message = _build_message(to_email, subject, body, html_body, message_id)
    resolved_message_id = message["Message-ID"]

    try:
        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=30,
        ) as server:

            server.ehlo()

            server.starttls()

            server.ehlo()

            server.login(
                smtp_user,
                smtp_password,
            )

            server.send_message(
                message
            )

    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "SMTP authentication failed. "
            "Check SMTP_USER and SMTP_PASSWORD."
        )

    except smtplib.SMTPException as e:
        raise RuntimeError(
            f"SMTP email sending failed: {str(e)}"
        )

    except OSError as e:
        raise RuntimeError(
            f"Unable to connect to SMTP server: {str(e)}"
        )

    return resolved_message_id


def verify_smtp_connection() -> Dict[str, Any]:
    """
    Verifies that the SMTP server is reachable and credentials are valid
    without sending an email.
    """
    if not settings.smtp_user or not settings.smtp_password:
        return {"status": "error", "message": "SMTP credentials not configured in environment"}

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            return {"status": "success", "message": "SMTP transport operational and authenticated"}
    except smtplib.SMTPAuthenticationError:
        return {"status": "error", "message": "SMTP authentication failed. Check credentials."}
    except Exception as e:
        return {"status": "error", "message": f"SMTP connection error: {str(e)}"}

