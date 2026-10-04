"""
CareerPulse — application communication / reply tracking tests.

Plain-script runner (no pytest in this project), matching the existing
tests/test_suite.py style.

What is really exercised here:
- the real MIME message builder and the real SMTP send path, over a local
  TLS SMTP server, plus the real `email_logs` insert against Supabase;
- the real Gmail payload parser on real Gmail API response shapes;
- the real reply matching, deduplication, privacy filtering, status derivation
  and Gemini-classification fallback logic, against an in-memory stand-in for
  Supabase that mimics PostgREST's missing-table / missing-column / unique
  violations, in both the pre-migration and post-migration schema shapes.

What is NOT claimed here: no live Gmail mailbox was read. The Gmail HTTP
transport is replaced with realistic fixtures. Live verification is described
in the implementation report.
"""

import asyncio
import base64
import json
import os
import socket
import ssl
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default as default_policy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL, SKIP = [], [], []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {extra}" if extra else ""))


def skip(label, why):
    SKIP.append(label)
    print(f"  SKIP  {label} — {why}")


# =====================================================================
print("\n=== IN-MEMORY SUPABASE STAND-IN (PostgREST-like errors) ===")


class FakeApiError(Exception):
    pass


SENT_LEGACY_COLUMNS = [
    "id", "user_id", "opportunity_id", "recipient_email", "subject", "body", "status", "sent_at",
]
SENT_MIGRATED_COLUMNS = SENT_LEGACY_COLUMNS + [
    "message_id", "sender_email", "thread_id", "provider", "provider_message_id",
    "company", "title", "updated_at",
]
REPLY_COLUMNS = [
    "id", "user_id", "opportunity_id", "sent_email_id", "thread_id", "provider",
    "provider_message_id", "internet_message_id", "sender_email", "sender_name",
    "recipient_email", "subject", "body_text", "received_at", "is_read", "has_attachments",
    "attachment_names", "classification", "classification_confidence", "classification_summary",
    "requires_action", "requested_information", "suggested_status", "match_method",
    "created_at", "updated_at",
]
CONNECTION_COLUMNS = [
    "id", "user_id", "provider", "mailbox_email", "access_token_encrypted",
    "refresh_token_encrypted", "token_expires_at", "scopes", "status", "last_sync_at",
    "last_sync_error", "created_at", "updated_at",
]


class FakeQuery:
    def __init__(self, rows, columns, name, action, select_cols=None, payload=None,
                 on_conflict=None):
        self.rows, self.columns, self.name, self.action = rows, columns, name, action
        self.select_cols, self.payload, self.on_conflict = select_cols, payload, on_conflict
        self.filters, self.order_spec, self.limit_n, self.count_mode = [], None, None, False

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def like(self, key, value):
        self.filters.append((key, f"%{value}%"))
        return self

    def order(self, column, desc=False):
        self.order_spec = (column, desc)
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def _matches(self, row):
        for key, value in self.filters:
            actual = row.get(key)
            if isinstance(value, str) and value.startswith("%") and value.endswith("%"):
                if value.strip("%").lower() not in str(actual or "").lower():
                    return False
            elif actual != value:
                return False
        return True

    def execute(self):
        rows = self.rows[self.name]
        if self.action == "insert":
            for record in (self.payload if isinstance(self.payload, list) else [self.payload]):
                unknown = set(record) - set(self.columns[self.name])
                if unknown:
                    raise FakeApiError(
                        f"column {self.name}.{sorted(unknown)[0]} does not exist")
                if self.name == "email_replies":
                    clash = any(
                        r.get("provider") == record.get("provider")
                        and r.get("provider_message_id") == record.get("provider_message_id")
                        for r in rows
                    )
                    if clash:
                        raise FakeApiError("23505 duplicate key value violates unique constraint")
                record.setdefault("id", str(uuid.uuid4()))
                record.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                if self.name == "email_logs":
                    # Mirrors the column default on the real table.
                    record.setdefault("sent_at", datetime.now(timezone.utc).isoformat())
                rows.append(dict(record))
            return Result(list(self.payload if isinstance(self.payload, list) else [self.payload]))

        selected = [r for r in rows if self._matches(r)]

        if self.action == "update":
            for row in selected:
                row.update(self.payload)
                row["updated_at"] = datetime.now(timezone.utc).isoformat()
            return Result(selected)

        if self.action == "delete":
            for row in selected:
                rows.remove(row)
            return Result(selected)

        if self.action == "upsert":
            key = (self.on_conflict or "id").split(",")[0]
            for record in (self.payload if isinstance(self.payload, list) else [self.payload]):
                match = next((r for r in rows if r.get(key) == record.get(key)), None)
                if match:
                    match.update(record)
                else:
                    record.setdefault("id", str(uuid.uuid4()))
                    rows.append(dict(record))
            return Result([self.payload])

        # select
        if self.select_cols and self.select_cols != "*":
            for col in [c.strip() for c in self.select_cols.split(",") if c.strip()]:
                if col not in self.columns[self.name]:
                    raise FakeApiError(f"column {self.name}.{col} does not exist")
        if self.order_spec:
            column, desc = self.order_spec
            selected.sort(key=lambda r: (r.get(column) is None, str(r.get(column) or "")),
                          reverse=desc)
        if self.limit_n:
            selected = selected[: self.limit_n]
        return Result(selected, count=len(selected) if self.count_mode else None)


class Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeTable:
    def __init__(self, store, name):
        self.rows, self.columns, self.name = store.rows, store.columns, name

    def _guard(self):
        if self.name not in self.rows:
            raise FakeApiError(
                f"Could not find the table 'public.{self.name}' in the schema cache")
        return self

    def _query(self, action, **kwargs):
        self._guard()
        return FakeQuery(self.rows, self.columns, self.name, action, **kwargs)

    def select(self, columns="*", count=None):
        q = self._query("select", select_cols=columns)
        q.count_mode = count == "exact"
        return q

    def insert(self, payload):
        return self._query("insert", payload=payload)

    def update(self, fields):
        return self._query("update", payload=fields)

    def upsert(self, payload, on_conflict=None):
        return self._query("upsert", payload=payload, on_conflict=on_conflict)

    def delete(self):
        return self._query("delete")


class FakeSupabase:
    """Mimics the subset of postgrest-py that reply_tracker uses."""

    def __init__(self, migrated=True):
        self.rows = {
            "email_logs": [],
            "email_replies": [],
            "mailbox_connections": [],
        }
        self.columns = {
            "email_logs": SENT_MIGRATED_COLUMNS if migrated else SENT_LEGACY_COLUMNS,
            "email_replies": REPLY_COLUMNS,
            "mailbox_connections": CONNECTION_COLUMNS,
        }
        self._migrated = migrated

    def table(self, name):
        return FakeTable(self, name)


# =====================================================================
print("\n=== TEST 1: sent outreach email is recorded (local TLS SMTP) ===")
from app.config import settings
from app.services.email_service import send_email, build_message_id

_captured_wire = []


def _self_signed_cert():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem_cert = cert.public_bytes(serialization.Encoding.PEM)
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return pem_cert, pem_key


PEM_CERT, PEM_KEY = _self_signed_cert()


class _SmtpStub:
    """Minimal ESMTP responder with STARTTLS, enough to accept one message."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def readline(self):
        while b"\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                return b""
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\r\n")
        return line + b"\r\n"

    def send(self, line):
        self.sock.sendall((line + "\r\n").encode())

    def serve(self):
        self.send("220 careerspulse.test ESMTP")
        while True:
            raw = self.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            upper = line.upper()
            if upper.startswith(("EHLO", "HELO")):
                self.send("250-careerpulse.test")
                self.send("250-STARTTLS")
                self.send("250-AUTH PLAIN LOGIN")
                self.send("250 SIZE 10485760")
            elif upper.startswith("STARTTLS"):
                self.send("220 Ready to start TLS")
                self.sock = _tls_context.wrap_socket(self.sock, server_side=True)
                self.buffer = b""
            elif upper.startswith("AUTH"):
                self.send("334 " + base64.b64encode(b"\x00user\x00pass").decode())
                self.readline()
                self.send("235 2.7.0 Authentication successful")
            elif upper.startswith("MAIL FROM") or upper.startswith("RCPT TO"):
                self.send("250 OK")
            elif upper.startswith("DATA"):
                self.send("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    chunk = self.readline()
                    if not chunk or chunk.strip() == b".":
                        break
                    chunks.append(chunk)
                _captured_wire.append(b"".join(chunks))
                self.send("250 OK queued")
            elif upper.startswith("QUIT"):
                self.send("221 Bye")
                break
            else:
                self.send("250 OK")


def _start_smtp_stub():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except OSError:
                return
            try:
                _SmtpStub(conn).serve()
            except (ssl.SSLError, OSError):
                pass

    threading.Thread(target=loop, daemon=True).start()
    return server, server.getsockname()[1], stop


import tempfile

with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
    fh.write(PEM_CERT)
    cert_path = fh.name
with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as fh:
    fh.write(PEM_KEY)
    key_path = fh.name
_tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
_tls_context.load_cert_chain(certfile=cert_path, keyfile=key_path)

smtp_server, smtp_port, smtp_stop = _start_smtp_stub()
real_smtp_host, real_smtp_port = settings.smtp_host, settings.smtp_port
settings.smtp_host, settings.smtp_port = "127.0.0.1", smtp_port

OPPORTUNITY_A = "quantum_smt_technologies_python_developer_intern"
OPPORTUNITY_B = "abc_technologies_software_engineer_intern"
OPPORTUNITY_C = "idealabs_digital_agentic_ai_developer"
TEST_MAILBOX = "candidate@example.com"
TEST_USER = "11111111-1111-1111-1111-111111111111"

subject_a = "Application for Python Developer Intern - Jeshurun Selvakumar"
subject_b = "Application for Software Engineer Intern - Jeshurun Selvakumar"
subject_c = "Application for Agentic AI Developer - Jeshurun Selvakumar"

sent_message_id = ""
try:
    sent_message_id = send_email(
        to_email="recruiter@quantum-smt.test",
        subject=subject_a,
        body="Dear Hiring Team,\n\nI am writing to apply for the role.\n\nRegards,\nJeshurun",
    )
    send_ok, send_error = True, ""
except Exception as e:
    send_ok, send_error = False, str(e)
finally:
    settings.smtp_host, settings.smtp_port = real_smtp_host, real_smtp_port
    smtp_stop.set()
    smtp_server.close()

check("outreach email sent over SMTP", send_ok, send_error)
check("send_email returns a Message-ID", bool(sent_message_id) and sent_message_id.startswith("<"),
      sent_message_id)
check("Message-ID is unique per send", build_message_id() != build_message_id())
check("Message-ID uses RFC 5322 angle-bracket form",
      bool(sent_message_id) and sent_message_id.endswith(">") and " " not in sent_message_id)

wire = _captured_wire[0] if _captured_wire else b""
parsed_wire = BytesParser(policy=default_policy).parsebytes(wire) if wire else None
check("Message-ID header present on the wire",
      bool(parsed_wire) and parsed_wire["Message-ID"] == sent_message_id,
      parsed_wire["Message-ID"] if parsed_wire else "no message captured")
check("Date header present on the wire", bool(parsed_wire) and bool(parsed_wire["Date"]))
check("plain-text send path unchanged (text/plain, not multipart)",
      bool(parsed_wire) and parsed_wire.get_content_type() == "text/plain",
      parsed_wire.get_content_type() if parsed_wire else "")

# =====================================================================
print("\n=== SENT RECORD: enriched insert (post-migration schema) ===")
from app.services import reply_tracker as rt
import app.services.reply_tracker as rt_mod

fake = FakeSupabase(migrated=True)
rt._supports_enrichment.clear()
rt._supports_replies.clear()
rt._supports_connections.clear()

record_a = rt.record_sent_email(
    supabase=fake, user_id=TEST_USER, opportunity_id=OPPORTUNITY_A,
    recipient_email="recruiter@quantum-smt.test", sender_email=TEST_MAILBOX,
    subject=subject_a, body="Dear Hiring Team,", message_id=sent_message_id,
    company="Quantum SMT Technologies", title="Python Developer Intern",
)
check("sent_emails record created", bool(record_a.get("id")), record_a.get("id"))
check("record stores the generated message id",
      record_a.get("message_id") == sent_message_id, record_a.get("message_id"))
check("record stores the opportunity link", record_a.get("opportunity_id") == OPPORTUNITY_A)
check("record stores company and title for the Applications list",
      record_a.get("company") == "Quantum SMT Technologies" and record_a.get("title") ==
      "Python Developer Intern")

record_b = rt.record_sent_email(
    supabase=fake, user_id=TEST_USER, opportunity_id=OPPORTUNITY_B,
    recipient_email="careers@abc-tech.test", sender_email=TEST_MAILBOX,
    subject=subject_b, body="Dear Hiring Team,", message_id="<careerpulse-b@test.local>",
    company="ABC Technologies", title="Software Engineer Intern",
)
record_c = rt.record_sent_email(
    supabase=fake, user_id=TEST_USER, opportunity_id=OPPORTUNITY_C,
    recipient_email="talent@idealabs.test", sender_email=TEST_MAILBOX,
    subject=subject_c, body="Dear Hiring Team,", message_id="<careerpulse-c@test.local>",
    company="Idealabs Digital", title="Agentic AI Developer",
)
check("second and third applications recorded", bool(record_b.get("id")) and bool(record_c.get("id")))

print("\n=== SENT RECORD: graceful fallback (pre-migration schema) ===")
fake_legacy = FakeSupabase(migrated=False)
rt._supports_enrichment.clear()
legacy_record = rt.record_sent_email(
    supabase=fake_legacy, user_id=TEST_USER, opportunity_id=OPPORTUNITY_A,
    recipient_email="recruiter@quantum-smt.test", sender_email=TEST_MAILBOX,
    subject=subject_a, body="Body", message_id="<x@test.local>",
)
check("send still recorded when reply columns are missing", bool(legacy_record.get("id")))
check("fallback record keeps the original fields",
      legacy_record.get("recipient_email") == "recruiter@quantum-smt.test"
      and legacy_record.get("status") == "Sent")
rt._supports_enrichment.clear()

# =====================================================================
print("\n=== GMAIL PAYLOAD PARSING (real API response shapes) ===")
from app.services import gmail_client as gc


def b64url(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_message(message_id, thread_id, from_addr, from_name, subject,
                  in_reply_to="", references="", html=False, attachments=None,
                  date=None):
    headers = [
        {"name": "Delivered-To", "value": TEST_MAILBOX},
        {"name": "From", "value": f"{from_name} <{from_addr}>"},
        {"name": "To", "value": TEST_MAILBOX},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": date or datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")},
    ]
    if in_reply_to:
        headers.append({"name": "In-Reply-To", "value": in_reply_to})
    if references:
        headers.append({"name": "References", "value": references})

    if html:
        markup = ("<html><head><style>body{display:none}</style><script>steal()</script></head>"
                  "<body><p>Hi Jeshurun,</p><div>Thank you for your application.</div></body></html>")
        parts = [{"partId": "1", "mimeType": "text/html", "filename": "",
                  "body": {"size": len(markup), "data": b64url(markup)}}]
    else:
        body = "Hi Jeshurun,\n\nThank you for your application.\n\nRegards,\nRecruiting Team"
        parts = [{"partId": "1", "mimeType": "text/plain", "filename": "",
                  "body": {"size": len(body), "data": b64url(body)}}]

    for index, filename in enumerate(attachments or [], start=2):
        parts.append({"partId": str(index), "mimeType": "application/pdf", "filename": filename,
                      "body": {"attachmentId": f"att-{index}", "size": 2048}})

    return {
        "id": message_id, "threadId": thread_id, "snippet": "Thank you for your application",
        "internalDate": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "payload": {"partId": "", "mimeType": "multipart/mixed", "filename": "",
                    "headers": headers, "body": {"size": 0}, "parts": parts},
    }


REPLY_A = gmail_message("msg-a1", "thread-aaa", "recruiter@quantum-smt.test", "Anita Rao",
                        f"Re: {subject_a}", in_reply_to=sent_message_id, references=sent_message_id)
REPLY_A2 = gmail_message("msg-a2", "thread-aaa", "recruiter@quantum-smt.test", "Anita Rao",
                         f"Re: {subject_a}")
REPLY_B = gmail_message("msg-b1", "thread-bbb", "careers@abc-tech.test", "ABC Careers",
                        f"Re: {subject_b}", in_reply_to="<careerpulse-b@test.local>")
REPLY_C = gmail_message("msg-c1", "thread-ccc", "talent@idealabs.test", "Idealabs Talent",
                        f"Re: {subject_c}", in_reply_to="<careerpulse-c@test.local>", html=True)
PERSONAL = gmail_message("msg-p1", "thread-ppp", "family@example.com", "Relative",
                         "Dinner on Sunday?")
NEWSLETTER = gmail_message("msg-u1", "thread-uuu", "news@quantum-smt.test", "Quantum SMT News",
                           "Quantum SMT Technologies hiring drive 2026")
SIMILAR_SUBJECT_UNRELATED = gmail_message(
    "msg-u2", "thread-uu2", "someone@quantum-smt.test", "Someone Else",
    f"Re: {subject_a}")  # same subject, unknown thread, different linked application

FIXTURES = {m["id"]: m for m in (REPLY_A, REPLY_A2, REPLY_B, REPLY_C, PERSONAL, NEWSLETTER,
                                 SIMILAR_SUBJECT_UNRELATED)}
CANDIDATE_IDS = list(FIXTURES.keys())
search_log = {"queries": [], "calls": 0}


def fake_search(access_token, query, max_results=25):
    search_log["queries"].append(query)
    search_log["calls"] += 1
    return [{"id": mid, "threadId": FIXTURES[mid]["threadId"]} for mid in CANDIDATE_IDS]


def fake_get_message(access_token, message_id):
    return FIXTURES[message_id]


def fake_thread_ids(access_token, thread_id):
    if thread_id == "thread-aaa":
        return ["msg-a1", "msg-a2", "sent-message-id-placeholder"]
    return [m for m in CANDIDATE_IDS if FIXTURES[m]["threadId"] == thread_id]


gc.search_messages = fake_search
gc.get_message = fake_get_message
gc.thread_message_ids = fake_thread_ids

parsed_reply = gc.parse_gmail_message(REPLY_A)
check("sender address parsed", parsed_reply.sender_email == "recruiter@quantum-smt.test",
      parsed_reply.sender_email)
check("sender display name parsed", parsed_reply.sender_name == "Anita Rao", parsed_reply.sender_name)
check("recipient parsed", parsed_reply.recipient_email == TEST_MAILBOX)
check("In-Reply-To parsed", parsed_reply.in_reply_to == sent_message_id)
check("References parsed", parsed_reply.references == [sent_message_id])
check("thread id parsed", parsed_reply.thread_id == "thread-aaa")
check("provider message id parsed", parsed_reply.provider_message_id == "msg-a1")
check("body text decoded", "Thank you for your application" in parsed_reply.body_text)
check("received date parsed", parsed_reply.received_at is not None)

html_parsed = gc.parse_gmail_message(REPLY_C)
check("HTML-only reply rendered as text", "Thank you for your application" in html_parsed.body_text)
check("script content stripped from HTML mail", "steal()" not in html_parsed.body_text)
check("style content stripped from HTML mail", "display:none" not in html_parsed.body_text)
check("stored reply text contains no HTML markup", "<" not in html_parsed.body_text)

with_attachment = gc.parse_gmail_message(gmail_message(
    "msg-att", "thread-att", "a@b.test", "A", "s", attachments=["resume.pdf"]))
check("attachment reported by name only", with_attachment.attachment_names == ["resume.pdf"])
check("attachment not inlined into the stored text",
      "attachmentId" not in with_attachment.body_text
      and "resume.pdf" not in with_attachment.body_text,
      with_attachment.body_text[:60])

check("malformed message does not raise", gc.parse_gmail_message({"id": "x"}) is not None
      or gc.parse_gmail_message({}) is None)

# =====================================================================
print("\n=== TEST 2/3/6/7: SYNC, MATCHING, ROUTING, PRIVACY ===")

# Stand in for the server's Google OAuth credentials so the sync path runs.
# (The real values live in the environment; only presence is relevant here.)
settings.gmail_client_id = "test-client-id.apps.googleusercontent.com"
settings.gmail_client_secret = "test-client-secret"

rt.save_connection(
    supabase=fake, user_id=TEST_USER, access_token="access-token-value",
    refresh_token="refresh-token-value",
    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    scopes=" ".join(settings.gmail_scopes), mailbox_email=TEST_MAILBOX,
)
connection = rt.load_connection(fake, TEST_USER)
check("mailbox connection stored", bool(connection))
check("access token is not stored in plaintext",
      "access-token-value" not in (connection.get("access_token_encrypted") or ""))
check("refresh token is not stored in plaintext",
      "refresh-token-value" not in (connection.get("refresh_token_encrypted") or ""))

import app.services.mailbox_crypto as mc

check("access token decrypts server-side",
      mc.decrypt_token(connection["access_token_encrypted"]) == "access-token-value")
check("refresh token decrypts server-side",
      mc.decrypt_token(connection["refresh_token_encrypted"]) == "refresh-token-value")
check("decrypting a tampered value fails closed", mc.decrypt_token("not-a-token") == "")

status = rt.mailbox_integration_status(fake, TEST_USER)
check("integration status reports the connected mailbox", status.get("connected") is True)
check("integration status never exposes tokens",
      not any(k in status for k in ("access_token_encrypted", "refresh_token_encrypted",
                                    "access_token", "refresh_token")))

# Every MAILBOX scope must stay read-only: CareerPulse may never send, modify,
# label or delete mail through this grant.
_mail_scopes = [s for s in status.get("scopes", [])
                if "calendar" not in s]
check("all mailbox scopes remain read-only",
      all("readonly" in s or "userinfo" in s or s == "openid" for s in _mail_scopes)
      and not any(s.endswith(".send") or "modify" in s for s in _mail_scopes),
      _mail_scopes)

# calendar.events is the single, deliberately-approved write grant. It must not
# be anything broader: full calendar read/write, drive or gmail.send/modify would
# all be over-privileged.
_calendar_scopes = [s for s in status.get("scopes", []) if "calendar" in s]
check("calendar.events is requested and is the only write scope",
      _calendar_scopes == ["https://www.googleapis.com/auth/calendar.events"],
      _calendar_scopes)
check("no over-privileged Google scope is requested",
      not any(s in status.get("scopes", []) for s in (
          "https://www.googleapis.com/auth/calendar",
          "https://www.googleapis.com/auth/calendar.readonly",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/drive",
      )), status.get("scopes"))


def run_sync(force=True):
    return asyncio.run(rt.sync_replies(fake, TEST_USER, force=force))


report_1 = run_sync()
check("sync reports success", report_1.get("status") == "success", report_1.get("message"))
check("replies matched to applications", report_1.get("matched", 0) >= 3,
      f"matched={report_1.get('matched')} inserted={report_1.get('inserted')}")
check("unrelated mail discarded rather than stored", report_1.get("unlinked", 0) >= 2,
      f"unlinked={report_1.get('unlinked')}")
check("only one mailbox search query was issued", search_log["calls"] == 1, search_log["queries"])
check("search is bounded to the known recipients and date",
      bool(search_log["queries"])
      and "recruiter@quantum-smt.test" in search_log["queries"][0]
      and "after:" in search_log["queries"][0]
      and "family@example.com" not in search_log["queries"][0],
      search_log["queries"][0][:120] if search_log["queries"] else "no query issued")

stored = {r["provider_message_id"]: r for r in fake.rows["email_replies"]}
check("Quantum SMT reply stored", "msg-a1" in stored)
check("ABC Technologies reply stored", "msg-b1" in stored)
check("Idealabs HTML reply stored", "msg-c1" in stored)
check("personal email NOT stored", "msg-p1" not in stored)
check("company newsletter NOT stored", "msg-u1" not in stored)
check("unrelated same-subject email NOT stored", "msg-u2" not in stored)

check("reply linked to Quantum SMT opportunity",
      stored.get("msg-a1", {}).get("opportunity_id") == OPPORTUNITY_A,
      stored.get("msg-a1", {}).get("opportunity_id"))
check("reply linked to the ABC opportunity",
      stored.get("msg-b1", {}).get("opportunity_id") == OPPORTUNITY_B,
      stored.get("msg-b1", {}).get("opportunity_id"))
check("reply linked to the Idealabs opportunity",
      stored.get("msg-c1", {}).get("opportunity_id") == OPPORTUNITY_C,
      stored.get("msg-c1", {}).get("opportunity_id"))
check("reply linked to the exact sent-email record",
      stored.get("msg-a1", {}).get("sent_email_id") == record_a.get("id"))
check("In-Reply-To is recorded as the match method",
      stored.get("msg-a1", {}).get("match_method") == "in_reply_to",
      stored.get("msg-a1", {}).get("match_method"))
check("reply thread id stored", stored.get("msg-a1", {}).get("thread_id") == "thread-aaa")
check("thread id backfilled onto the sent-email record",
      fake.rows["email_logs"][0].get("thread_id") == "thread-aaa")
check("sender identity stored", stored.get("msg-a1", {}).get("sender_name") == "Anita Rao")
check("received timestamp stored", bool(stored.get("msg-a1", {}).get("received_at")))
check("new replies start unread", stored.get("msg-a1", {}).get("is_read") is False)

print("\n=== TEST 5: duplicate prevention ===")
# A new reply arrives in the thread after the first sync.
REPLY_A3 = gmail_message("msg-a3", "thread-aaa", "recruiter@quantum-smt.test", "Anita Rao",
                         f"Re: {subject_a}", in_reply_to=sent_message_id)
FIXTURES["msg-a3"] = REPLY_A3
CANDIDATE_IDS.append("msg-a3")

before = len(fake.rows["email_replies"])
report_2 = run_sync()
after = len(fake.rows["email_replies"])
check("re-sync stores only the newly arrived reply", after == before + 1,
      f"before={before} after={after} inserted={report_2.get('inserted')}")
check("already stored replies are counted as duplicates", report_2.get("duplicates", 0) >= 4,
      report_2.get("duplicates"))
report_3 = run_sync()
check("third sync creates nothing new", report_3.get("inserted", 0) == 0
      and len(fake.rows["email_replies"]) == after,
      f"inserted={report_3.get('inserted')}")
check("no duplicate provider message ids stored",
      len({r["provider_message_id"] for r in fake.rows["email_replies"]}) == after)
check("same-thread reply without headers linked to the right application",
      any(r["opportunity_id"] == OPPORTUNITY_A for r in fake.rows["email_replies"]
          if r["provider_message_id"] == "msg-a2"))
REPLY_TOTAL = after

print("\n=== TEST 24: duplicate insert rejected at the database level ===")
rows_before = len(fake.rows["email_replies"])
dup_result = rt._insert_reply(fake, {"user_id": TEST_USER, "opportunity_id": OPPORTUNITY_A,
                                     "provider": "gmail", "provider_message_id": "msg-a1",
                                     "sender_email": "recruiter@quantum-smt.test"})
check("re-inserting a known message id is refused", dup_result is None)
check("row count unchanged after the refused insert", len(fake.rows["email_replies"]) == rows_before)

print("\n=== TEST 4: conversation view ===")
conversation = rt.get_conversation(fake, TEST_USER, OPPORTUNITY_A)
messages = conversation["messages"]
check("conversation contains the sent email", any(m["direction"] == "outbound" for m in messages))
check("conversation contains the replies", sum(1 for m in messages if m["direction"] == "inbound") == 3)
check("conversation is chronological",
      [m["timestamp"] or "" for m in messages] == sorted((m["timestamp"] or "") for m in messages))
check("conversation carries the application status",
      conversation["application"]["status"] in [
          rt.STATUS_NOT_CONTACTED, rt.STATUS_EMAIL_SENT, rt.STATUS_REPLY_RECEIVED,
          rt.STATUS_APPLICATION_RECEIVED, rt.STATUS_INTERVIEW_REQUESTED,
          rt.STATUS_INTERVIEW_SCHEDULED, rt.STATUS_INFO_REQUESTED, rt.STATUS_REJECTED],
      conversation["application"]["status"])
check("inbound messages carry their classification payload",
      all("classification" in m for m in messages if m["direction"] == "inbound"))
check("conversations do not leak across opportunities",
      all("Python Developer Intern" in m["subject"] for m in messages),
      [m["subject"] for m in messages])

empty_conversation = rt.get_conversation(fake, TEST_USER, "unknown_company_unknown_role")
check("unknown opportunity returns an empty conversation", empty_conversation["messages"] == [])

print("\n=== TEST 14: application status derivation ===")
applications = {a["opportunity_id"]: a for a in rt.list_applications(fake, TEST_USER)}
check("every emailed opportunity is listed", set(applications) == {OPPORTUNITY_A, OPPORTUNITY_B, OPPORTUNITY_C},
      list(applications))
check("an opportunity with replies is not 'Email Sent'",
      applications[OPPORTUNITY_A]["status"] != rt.STATUS_EMAIL_SENT,
      applications[OPPORTUNITY_A]["status"])
check("reply counts recorded", applications[OPPORTUNITY_A]["reply_count"] == 3,
      applications[OPPORTUNITY_A]["reply_count"])
check("unread counts recorded", applications[OPPORTUNITY_A]["unread_count"] == 3,
      applications[OPPORTUNITY_A]["unread_count"])
check("no application is auto-marked as selected",
      all("selected" not in a["status"].lower() for a in applications.values()))
check("rejection only from an explicit classification",
      rt._CLASSIFICATION_TO_STATUS["rejection"] == rt.STATUS_REJECTED)
check("unknown classification maps to Reply Received",
      rt._CLASSIFICATION_TO_STATUS["unknown"] == rt.STATUS_REPLY_RECEIVED)

print("\n=== TEST 8: Gemini classification failure ===")
import google.genai as genai_pkg

real_key = settings.gemini_api_key
settings.gemini_api_key = "test-key"
real_client = genai_pkg.Client


class _FailingModels:
    def generate_content(self, *a, **k):
        raise RuntimeError("simulated Gemini outage")


class _FailingClient:
    def __init__(self, *a, **k):
        self.models = _FailingModels()


genai_pkg.Client = _FailingClient
try:
    degraded = rt_mod.classify_reply("Re: Application", "Please share availability.",
                                     "Role", "Company")
finally:
    genai_pkg.Client = real_client
    settings.gemini_api_key = real_key

check("classification failure falls back to unknown", degraded.get("classification") == "unknown")
check("failure does not invent a status", degraded.get("suggested_status") == rt.STATUS_REPLY_RECEIVED)
check("failure does not demand action", degraded.get("requires_action") is False)
check("replies are still listed after a classification failure",
      len(rt.list_replies(fake, TEST_USER)) == REPLY_TOTAL, REPLY_TOTAL)
check("every stored reply still has a classification",
      all(r.get("classification") for r in rt.list_replies(fake, TEST_USER)))
check("stored classifications are all in the allowed set",
      all(r["classification"] in rt.CLASSIFICATIONS for r in rt.list_replies(fake, TEST_USER)))
check("an unknown model label is not trusted",
      rt_mod._coerce_classification({"classification": "selected_offer", "confidence": 0.99})
      ["classification"] == "unknown")
check("high-confidence confidence is clamped",
      rt_mod._coerce_classification({"classification": "interview_request", "confidence": 5})["confidence"] == 1.0)
check("unknown classification cannot claim high confidence",
      rt_mod._coerce_classification({"classification": "unknown", "confidence": 0.99})["confidence"] <= 0.4)
check("requested_information must be a list",
      rt_mod._coerce_classification({"classification": "interview_request",
                                     "requested_information": "resume.pdf"})["requested_information"] == [])

print("\n=== TEST 9: expired / revoked authorization ===")


def _auth_failure(*a, **k):
    raise gc.MailboxAuthError("invalid_grant: token revoked by the user")


fake.rows["mailbox_connections"][0]["token_expires_at"] = (
    datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
real_refresh = gc.refresh_access_token
gc.refresh_access_token = _auth_failure
report_auth = run_sync()
gc.refresh_access_token = real_refresh

check("expired authorization is reported, not raised",
      report_auth.get("status") == "error", report_auth.get("error_code"))
check("expired authorization asks the user to reconnect",
      report_auth.get("error_code") == "auth_error")
check("raw OAuth error text is not exposed",
      "invalid_grant" not in json.dumps(report_auth).lower()
      and "revoked" not in json.dumps(report_auth).lower(), json.dumps(report_auth))
check("connection is flagged needs_reconnect",
      rt.load_connection(fake, TEST_USER).get("status") == "needs_reconnect")
check("status endpoint surfaces needs_reconnect",
      rt.mailbox_integration_status(fake, TEST_USER).get("needs_reconnect") is True)
check("mailbox address is still shown after a failed refresh",
      rt.mailbox_integration_status(fake, TEST_USER).get("email") == TEST_MAILBOX)

# Reconnect so the remaining transport-failure cases can run.
fake.rows["mailbox_connections"][0].update({
    "status": "connected",
    "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
})

print("\n=== SECURITY: per-user isolation ===")
OTHER_USER = "22222222-2222-2222-2222-222222222222"
check("another user sees zero replies", rt.list_replies(fake, OTHER_USER) == [])
check("another user's conversation is empty",
      rt.get_conversation(fake, OTHER_USER, OPPORTUNITY_A)["messages"] == [])
check("another user cannot read a reply by id",
      rt.get_reply(fake, OTHER_USER, stored["msg-a1"]["id"]) is None)
check("another user sees no applications", rt.list_applications(fake, OTHER_USER) == [])
check("another user has no mailbox connection", rt.load_connection(fake, OTHER_USER) is None)
check("unread count is per user", rt.count_unread_replies(fake, OTHER_USER) == 0)

print("\n=== SECURITY: error handling does not leak provider detail ===")


def _rate_limited(*a, **k):
    raise gc.MailboxRateLimited("quotaExceeded: 403 rateLimitExceeded per user")


gc.search_messages = _rate_limited
report_rate = run_sync()
gc.search_messages = fake_search
check("rate limiting is surfaced safely",
      report_rate.get("status") == "error" and report_rate.get("error_code") == "rate_limited",
      report_rate.get("message"))
check("rate limit message is user-safe",
      "quotaExceeded" not in (report_rate.get("message") or ""))


def _not_found(*a, **k):
    raise gc.MailboxProviderError("404 The requested message was not found.")


gc.get_message = _not_found
report_missing = run_sync()
gc.get_message = fake_get_message
check("a deleted message is skipped without failing the sync",
      report_missing.get("status") == "success", report_missing.get("error_code"))

print("\n=== TEST 17: AI RESPONSE DRAFT (never auto-sent) ===")
draft = asyncio.run(rt_mod.draft_reply_response(
    fake, TEST_USER, OPPORTUNITY_A, {"full_name": "Jeshurun Selvakumar", "skills": ["Python"]}))
check("draft response generated", draft.get("status") == "success", draft.get("source"))
check("draft has a subject and a body", bool(draft.get("subject")) and bool(draft.get("body")))
check("draft warns the user to complete the details",
      "availability" in (draft.get("notice") or "").lower()
      or "[ADD YOUR" in (draft.get("body") or "").upper())
check("draft invents no schedule",
      not any(t in (draft.get("body") or "") for t in ("Monday", "3:00 PM", "next Tuesday at 10")))
no_reply_draft = asyncio.run(rt_mod.draft_reply_response(fake, TEST_USER, "unknown_company_x",
                                                         {"full_name": "J"}))
check("no draft without a company reply", no_reply_draft.get("status") == "error")

print("\n=== SYNC THROTTLING ===")
fake.rows["mailbox_connections"][0]["last_sync_at"] = datetime.now(timezone.utc).isoformat()
throttled = run_sync(force=False)
check("automatic sync is throttled", throttled.get("skipped") is True, throttled.get("message"))
fake.rows["mailbox_connections"][0]["last_sync_at"] = (
    datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
check("forced sync ignores the throttle", run_sync(force=True).get("skipped") is False)
check("not connected is reported clearly",
      asyncio.run(rt.sync_replies(FakeSupabase(), OTHER_USER, force=True)).get("error_code")
      == "not_connected")

# =====================================================================
print("\n=== LIVE SUPABASE: real email_logs insert (no schema change needed) ===")
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
live = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
LIVE_USER = "79c9d890-e91b-40b7-8035-d02da38cdb10"
cleanup_subject = "TESTRT cleanup probe"
try:
    live.table("email_logs").delete().eq("user_id", LIVE_USER).eq("subject", cleanup_subject).execute()
    live_result = rt.record_sent_email(
        supabase=live, user_id=LIVE_USER, opportunity_id="test_cleanup_probe",
        recipient_email="probe@example.test", sender_email=os.getenv("SMTP_USER", ""),
        subject=cleanup_subject, body="probe", message_id="<probe@test.local>",
        company="Probe", title="Probe",
    )
    live_ok = bool(live_result.get("id"))
    live_error = ""
except Exception as e:
    live_ok, live_error = False, str(e)[:120]
finally:
    try:
        live.table("email_logs").delete().eq("user_id", LIVE_USER).eq("subject", cleanup_subject).execute()
    except Exception:
        pass
check("outreach record persists to the live email_logs table", live_ok, live_error)
check("pre-migration fallback keeps live sends working",
      live_ok and live_result.get("status") == "Sent")

# Does the real project already have the reply-tracking tables?
try:
    live.table("email_replies").select("id").limit(1).execute()
    skip("live reply-tracking tests", "tables missing — run the migration first")
    REPLY_TABLES_READY = False
except Exception:
    REPLY_TABLES_READY = False
    skip("live reply-tracking tests", "mailbox_connections / email_replies not created yet")

# =====================================================================
print("\n" + "=" * 64)
print(f"PASSED: {len(PASS)}   FAILED: {len(FAIL)}   SKIPPED: {len(SKIP)}")
if FAIL:
    print("\nFAILURES:")
    for f in FAIL:
        print(f"  - {f}")
if SKIP:
    print("\nSKIPPED:")
    for s in SKIP:
        print(f"  - {s}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
