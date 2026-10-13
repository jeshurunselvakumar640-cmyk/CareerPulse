"""
Comprehensive Email System Audit Test Suite.
Validates email templates, MIME construction, idempotency logic, and endpoint behavior.
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.services.email_service import (
    build_message_id,
    _build_message,
    verify_smtp_connection,
)
from app.services.email_template import (
    build_digest_html,
    build_digest_text,
    esc,
    safe_url,
    clean_inline,
)
from app.services.daily_digest import (
    is_digest_already_sent_today,
    load_local_digest_history,
)

def test_mime_construction_with_html():
    msg_id = build_message_id()
    assert msg_id.startswith("<") and msg_id.endswith(">")
    assert "careerpulse-" in msg_id

    msg = _build_message(
        to_email="test@example.com",
        subject="CareerPulse Test Subject",
        body="Plain text body with special characters: & < > \" '",
        html_body="<html><body><h1>Test &amp; Special</h1></body></html>",
        message_id=msg_id,
    )

    assert msg["To"] == "test@example.com"
    assert msg["Subject"] == "CareerPulse Test Subject"
    assert msg["Message-ID"] == msg_id
    assert msg["MIME-Version"] == "1.0"
    assert msg.is_multipart()
    parts = msg.get_payload()
    assert len(parts) == 2
    # First part must be text/plain for RFC compliance
    assert parts[0].get_content_type() == "text/plain"
    # Second part must be text/html
    assert parts[1].get_content_type() == "text/html"

def test_mime_construction_plain_only():
    msg = _build_message(
        to_email="test@example.com",
        subject="Plain Only",
        body="Plain text message",
        html_body=None,
    )
    assert msg["To"] == "test@example.com"
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"

def test_template_escaping_and_sanitization():
    # Test HTML escaping
    assert esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert esc("Tom & Jerry") == "Tom &amp; Jerry"
    assert esc('"Quotes"') == "&quot;Quotes&quot;"
    assert esc(None) == ""

    # Test URL validation
    assert safe_url("https://example.com/job?id=123") == "https://example.com/job?id=123"
    assert safe_url("http://example.com") == "http://example.com"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("data:text/html,<html>") is None
    assert safe_url("") is None
    assert safe_url(None) is None

    # Test clean_inline
    assert clean_inline("<p>Text with <script>bad()</script> content</p>") == "Text with content"

def test_digest_template_rendering_complex():
    sample_stories = [
        {
            "title": "Google DeepMind Unveils Next-Gen AI Architecture & Benchmarks",
            "source_name": "DeepMind",
            "source_url": "https://deepmind.google/research/breakthrough",
            "published_at": "2026-10-13T10:00:00Z",
            "summary": "Researchers announced a major frontier model showing 40% efficiency gains in reasoning tasks.",
            "why_it_matters": "Enables faster edge deployment for autonomous systems.",
            "category": "Artificial Intelligence",
        }
    ]

    sample_opportunities = [
        {
            "title": "Backend Engineering Intern (Python & Cloud)",
            "company": "Acme Robotics & Systems Inc.",
            "location": "Mumbai · Remote",
            "employment_type": "Internship",
            "url": "https://boards.greenhouse.io/acme/jobs/12345",
            "deadline": "2026-11-15",
            "description": "Work on distributed systems and high-throughput APIs.",
            "match": {
                "tier": "Gold",
                "score": 92,
                "score_percent": 92,
                "matched_skills": ["Python", "FastAPI", "SQL"],
                "missing_skills": ["Docker", "Kubernetes"],
            },
            "required_skills": ["Python", "FastAPI", "SQL"],
            "preferred_skills": ["Docker"],
        }
    ]

    sample_gaps = ["Docker", "Kubernetes"]

    html = build_digest_html(
        candidate_name="Jeshurun Selvakumar",
        stories=sample_stories,
        opportunities=sample_opportunities,
        skill_gaps=sample_gaps,
        period_label="October 12 – October 13, 2026",
    )

    text = build_digest_text(
        candidate_name="Jeshurun Selvakumar",
        stories=sample_stories,
        opportunities=sample_opportunities,
        skill_gaps=sample_gaps,
        period_label="October 12 – October 13, 2026",
    )

    # HTML Assertions
    assert "Jeshurun" in html
    assert "Google DeepMind" in html
    assert "Acme Robotics" in html
    assert "https://boards.greenhouse.io/acme/jobs/12345" in html
    assert "Gold" in html
    assert "92%" in html
    assert "Docker" in html
    assert "CareerPulse" in html
    assert len(html) > 5000

    # Text Assertions
    assert "Jeshurun" in text
    assert "Google DeepMind" in text
    assert "Acme Robotics" in text
    assert "92%" in text
    assert len(text) > 1000

def test_smtp_mock_send():
    """Validates that send_email executes SMTP protocol cleanly without real network sending."""
    from app.services.email_service import send_email

    with patch("smtplib.SMTP") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance

        # Test sending
        msg_id = send_email(
            to_email="recipient@example.com",
            subject="Audit Test",
            body="Audit test body",
            html_body="<html><body>Audit test HTML</body></html>",
        )

        assert msg_id is not None
        assert mock_instance.ehlo.called
        assert mock_instance.starttls.called
        assert mock_instance.login.called
        assert mock_instance.send_message.called

        # Verify sent message
        sent_msg = mock_instance.send_message.call_args[0][0]
        assert sent_msg["To"] == "recipient@example.com"
        assert sent_msg["Subject"] == "Audit Test"

def test_idempotency_detection():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # 1. Historical past date should NOT trigger idempotency
    assert not is_digest_already_sent_today("test_user_xyz", "nonexistent_recipient_12345@test.com")

if __name__ == "__main__":
    tests = [
        test_mime_construction_with_html,
        test_mime_construction_plain_only,
        test_template_escaping_and_sanitization,
        test_digest_template_rendering_complex,
        test_smtp_mock_send,
        test_idempotency_detection,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\nResult: {passed}/{len(tests)} email audit tests passed.")
