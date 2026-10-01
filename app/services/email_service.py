import smtplib
from typing import Dict, Any
from email.mime.text import MIMEText

from app.config import settings


def send_email(
    to_email: str,
    subject: str,
    body: str,
) -> None:
    """
    Send a plain-text email using Gmail SMTP and MIME.
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

    message = MIMEText(
        body,
        "plain",
        "utf-8",
    )

    message["From"] = smtp_user
    message["To"] = to_email
    message["Subject"] = subject

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

