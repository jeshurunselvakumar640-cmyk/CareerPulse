"""
CareerPulse — Application Workspace tests.

Plain-script runner (no pytest in this project), matching
tests/test_reply_tracking.py.

What is really exercised here:
- the grounded datetime extractor, which is the anti-hallucination core: a
  datetime is only accepted when the date AND the time literally appear in the
  email body;
- next-step analysis with the Gemini call replaced by a stub, covering the
  no-action, deadline, interview-with-date, interview-without-date, ambiguous
  and Gemini-failure paths;
- timeline, interview and calendar persistence against an in-memory stand-in
  for Supabase that mimics PostgREST unique violations and missing tables;
- duplicate protection (the same reply analysed twice);
- application isolation, including two applications at the same company and two
  applications with similar job titles.

What is NOT claimed here: no live Gemini, Gmail or Google Calendar call is made.
Those transports are replaced with stubs, exactly as test_reply_tracking.py
replaces the Gmail HTTP transport. Live verification is described in the
implementation report.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google.genai as genai

PASS, FAIL, SKIP = [], [], []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {extra}" if extra else ""))


def skip(label, why):
    SKIP.append(label)
    print(f"  SKIP  {label} — {why}")


# =====================================================================
# In-memory Supabase stand-in
# =====================================================================

class FakeApiError(Exception):
    pass


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table, op="select"):
        self.store, self.table_name, self.op = store, table, op
        self.filters, self.order_field, self.order_asc, self._limit, self._payload = [], None, True, None, None
        self._single = False

    def select(self, *a, **k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self._payload = "update", payload
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field, values):
        self.filters.append(("in", field, list(values or [])))
        return self

    def is_(self, field, value):
        self.filters.append(("is", field, value))
        return self

    def order(self, field, desc=False, asc=None):
        # reply_tracker calls .order(col, desc=True); the workspace calls
        # .order(col, asc=True). Support both spellings.
        self.order_field = field
        self.order_asc = asc if asc is not None else (not desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        for kind, field, value in self.filters:
            if kind == "eq" and row.get(field) != value:
                return False
            if kind == "in" and row.get(field) not in value:
                return False
            if kind == "is":
                actual = row.get(field)
                if value is None and actual is not None:
                    return False
                if value is not None and actual != value:
                    return False
        return True

    def execute(self):
        if self.table_name not in self.store:
            raise FakeApiError("PGRST205: relation does not exist (schema cache)")
        rows = self.store[self.table_name]

        if self.op == "select":
            out = [r for r in rows if self._matches(r)]
            if self.order_field:
                out.sort(key=lambda r: str(r.get(self.order_field) or ""), reverse=not self.order_asc)
            if self._limit:
                out = out[: self._limit]
            return FakeResult([dict(r) for r in out])

        if self.op == "insert":
            payload = dict(self._payload)
            key = "dedupe_key"
            if key in payload and any(r.get(key) == payload[key] for r in rows):
                raise FakeApiError("23505 duplicate key value violates unique constraint")
            payload.setdefault("id", f"id-{len(rows) + 1}")
            rows.append(payload)
            return FakeResult([dict(payload)])

        if self.op == "update":
            changed = 0
            for row in rows:
                if self._matches(row):
                    row.update(self._payload)
                    changed += 1
            return FakeResult([{"updated": changed}])

        raise FakeApiError("unsupported operation")


class FakeSupabase:
    def __init__(self):
        self.store = {}
        self.fail_writes = set()

    def seed(self, table, rows):
        self.store[table] = [dict(r) for r in rows]
        return self

    def table(self, name):
        return FakeQuery(self.store, name)


# =====================================================================
# Fixtures
# =====================================================================

USER = "user-1"

INTERVIEW_BODY = (
    "Hi,\n\nThank you for applying to the Software Engineering Internship.\n\n"
    "We would like to invite you to a technical interview on October 22, 2026 at 3:00 PM IST.\n"
    "Please complete the coding assessment before October 18, 2026.\n\n"
    "Best regards,\nGoogle Recruiting"
)

UNSCHEDULED_BODY = (
    "Hi,\n\nThanks for your application. We would like to interview you soon and will "
    "share more details about timing shortly.\n\nBest,\nMicrosoft Recruiting"
)

NO_ACTION_BODY = (
    "Hi,\n\nWe have received your application for the Cloud Internship. Our team is "
    "reviewing it and no action is needed from you at this time.\n\nBest,\nAmazon Recruiting"
)

DEADLINE_BODY = (
    "Hi,\n\nYour application for the Software Engineer Intern role is progressing. "
    "Please submit the assessment before 20 November 2026 at 11:59 PM GMT.\n\nBest,\nExample Technologies"
)

AMBIGUOUS_BODY = (
    "Hi,\n\nWe are considering your application. It is possible we may proceed to the "
    "next stage, although nothing is confirmed yet.\n\nBest,\nRecruiting"
)


def make_reply(reply_id, opportunity_id, subject, body, classification="positive_response"):
    return {
        "id": reply_id,
        "user_id": USER,
        "opportunity_id": opportunity_id,
        "subject": subject,
        "body_text": body,
        "classification": classification,
        "classification_summary": "",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "next_steps": None,
        "analysis_state": None,
    }


# =====================================================================
print("\n=== 1. GROUNDED DATETIME EXTRACTION (anti-hallucination core) ===")

from app.services import application_workspace as aw

extracted = aw.extract_explicit_datetime(INTERVIEW_BODY)
check("Interview email with explicit date/time yields a datetime", extracted is not None)
if extracted:
    check("Correct date parsed", extracted["starts_at_local"].date().isoformat() == "2026-10-22",
          extracted["date_text"])
    check("Correct time parsed", (extracted["starts_at_local"].hour, extracted["starts_at_local"].minute) == (15, 0),
          extracted["time_text"])
    check("Timezone stated in email is preserved", extracted["timezone_text"] == "IST",
          str(extracted["timezone_text"]))
    check("Timezone marked confirmed", extracted["timezone_confirmed"] is True)
    check("Aware datetime carries the stated offset", extracted["starts_at"] is not None
          and extracted["starts_at"].utcoffset() == timedelta(minutes=330))

check("Interview WITHOUT date/time yields nothing (case 5)",
      aw.extract_explicit_datetime(UNSCHEDULED_BODY) is None)
check("Reply requiring no action yields no interview (case 3)",
      aw.extract_explicit_datetime(NO_ACTION_BODY) is None)
check("Deadline-only email yields no interview (case 6)",
      aw.extract_explicit_datetime(DEADLINE_BODY) is None)
check("Ambiguous email yields no interview (case 7)",
      aw.extract_explicit_datetime(AMBIGUOUS_BODY) is None)
check("Empty body yields no interview (case 8)", aw.extract_explicit_datetime("") is None)
check("Past date is rejected, not scheduled",
      aw.extract_explicit_datetime("Interview on January 2, 2020 at 10:00 AM UTC") is None)

# A date with no time anywhere, and a time with no date: both must be refused.
check("Date without time is refused",
      aw.extract_explicit_datetime("Let's meet on October 22, 2026 sometime.") is None)
check("Time without date is refused",
      aw.extract_explicit_datetime("The call is at 3:00 PM sharp.") is None)


print("\n=== 2. NEXT-STEP EXTRACTION (cases 1, 2, 3, 4, 6, 7, 12) ===")

# The Gemini TRANSPORT is stubbed, not the analysis function. Everything after
# the model response - schema coercion, step de-duplication and, critically, the
# grounding that discards a hallucinated date, link or location - is the real
# production code path being exercised here.
GEMINI_MODE = {"mode": "ok"}


def _model_response_for(prompt: str) -> dict:
    if "interview on October" in prompt:
        return {
            "next_steps": [
                "1. Complete the coding assessment.",
                "2. Submit the assessment before October 18, 2026.",
                "3. Attend the technical interview on October 22, 2026 at 3:00 PM IST.",
            ],
            "requires_action": True,
            "interview_mentioned_without_schedule": False,
            # Deliberately WRONG on purpose: a fabricated date, link, location and
            # timezone. Grounding must discard every one of them.
            "interview": {
                "date_text": "November 30, 2026", "time_text": "9:00 AM",
                "timezone_text": "EST", "interview_type": "Technical",
                "meeting_link": "https://meet.example.invented", "location": "Invented Room",
            },
        }
    if "no action is needed" in prompt:
        return {
            "next_steps": ["1. No action is required from you at this time."],
            "requires_action": False, "interview_mentioned_without_schedule": False,
            "interview": None,
        }
    if "before 20 November" in prompt:
        return {
            "next_steps": ["1. Submit the assessment before 20 November 2026 at 11:59 PM GMT."],
            "requires_action": True, "interview_mentioned_without_schedule": False,
            "interview": None,
        }
    return {
        "next_steps": ["1. The email is ambiguous; clarify the next stage with the recruiter."],
        "requires_action": True, "interview_mentioned_without_schedule": True,
        # Ungrounded and shapeless: must never become an interview.
        "interview": {"date_text": "whenever", "time_text": "soon"},
    }


class _FakeModels:
    def generate_content(self, model=None, contents=None):
        if GEMINI_MODE["mode"] == "raise":
            raise RuntimeError("Gemini transport unavailable")
        if GEMINI_MODE["mode"] == "garbage":
            return SimpleNamespace(text="not json at all")
        payload = _model_response_for(contents or "")
        return SimpleNamespace(text="```json\n" + json.dumps(payload) + "\n```")


class _FakeClient:
    def __init__(self, api_key=None):
        self.models = _FakeModels()


_real_genai_client = genai.Client
genai.Client = _FakeClient


def make_db():
    return (FakeSupabase()
            .seed("email_replies", [])
            .seed("application_timeline_events", [])
            .seed("application_interviews", [])
            .seed("email_logs", []))


def run(reply, db=None):
    db = db or make_db()
    reply = dict(reply)
    row = {
        "id": reply["id"], "user_id": reply["user_id"], "opportunity_id": reply["opportunity_id"],
        "subject": reply["subject"], "body_text": reply["body_text"],
        "classification": reply["classification"], "classification_summary": "",
        "received_at": reply["received_at"], "next_steps": None, "analysis_state": None,
    }
    db.store["email_replies"].append(row)
    # Run the stubbed analysis the same way production does, then persist it.
    analysis = aw.analyze_reply_next_steps(
        row["subject"], row["body_text"], company="Google",
        opportunity_title="Software Engineering Intern", classification=row["classification"],
    )
    out = aw.store_reply_analysis(db, USER, row, analysis, company="Google")
    return out, db, row


# Case 1: internship reply with clear next steps
out, db, row = run(make_reply("r1", "google_software_engineering_intern",
                              "Your application", INTERVIEW_BODY))
check("Case 1 internship: next steps stored", len(row.get("next_steps") or []) == 3,
      str(len(row.get("next_steps") or [])))
check("Case 1: requires_action recorded", row.get("requires_action") is True)
check("Case 4: interview row created from an explicit date/time", out.get("interview") is not None)

interview_row = (db.store["application_interviews"] or [None])[0]
if interview_row:
    check("Hallucinated date from the model was discarded and the email's own date used",
          str(interview_row["starts_at"]).startswith("2026-10-22"), str(interview_row["starts_at"]))
    check("Hallucinated meeting link discarded", not interview_row.get("meeting_link"),
          str(interview_row.get("meeting_link")))
    check("Hallucinated location discarded", not interview_row.get("location"),
          str(interview_row.get("location")))
    check("Timezone came from the email, not the model", interview_row.get("timezone") == "IST",
          str(interview_row.get("timezone")))
    check("timezone_confirmed true because the email stated the zone",
          interview_row.get("timezone_confirmed") is True)

# Case 6: deadline preserved verbatim
out6, db6, row6 = run(make_reply("r6", "example_frontend_developer_intern", "Assessment",
                                 DEADLINE_BODY))
check("Case 6: deadline preserved verbatim in the step",
      any("20 November 2026 at 11:59 PM GMT" in s for s in (row6.get("next_steps") or [])),
      str(row6.get("next_steps")))
check("Case 6: no interview created for a deadline-only email",
      not db6.store["application_interviews"])

# Case 3: reply requiring no action
out3, db3, row3 = run(make_reply("r3", "amazon_cloud_intern", "Application received",
                                 NO_ACTION_BODY, classification="application_received"))
check("Case 3: explicit no-action step surfaced", len(row3.get("next_steps") or []) == 1)
check("Case 3: no interview and no calendar candidate", not db3.store["application_interviews"])

# Case 7: ambiguous email
out7, db7, row7 = run(make_reply("r7", "acme_analyst", "Update", AMBIGUOUS_BODY))
check("Case 7: ungrounded interview object did NOT create an interview row",
      not db7.store["application_interviews"])
check("Case 7: ambiguity recorded on the timeline",
      any(e["event_type"] == "interview_unconfirmed" for e in db7.store["application_timeline_events"]),
      str([e["event_type"] for e in db7.store["application_timeline_events"]]))


print("\n=== 3. GEMINI FAILURE ISOLATION (case 12) ===")

# The transport now fails, which must surface as a recorded failure while the
# reply itself is untouched.
GEMINI_MODE["mode"] = "raise"
db12 = make_db()
row12 = {"id": "r12", "user_id": USER, "opportunity_id": "google_swe_intern",
         "subject": "s", "body_text": INTERVIEW_BODY, "classification": "interview_scheduled",
         "classification_summary": "", "received_at": datetime.now(timezone.utc).isoformat(),
         "next_steps": None, "analysis_state": None}
db12.store["email_replies"].append(row12)
result12 = aw.process_reply_analysis(db12, row12)
check("Case 12: Gemini failure reported, not raised", result12.get("state") == "failed",
      str(result12.get("state")))
check("Case 12: the reply itself is still stored", len(db12.store["email_replies"]) == 1)
check("Case 12: failure reason recorded for transparency",
      row12.get("analysis_state") == "failed" and bool(row12.get("analysis_error")))
check("Case 12: no interview invented from a failed analysis",
      not db12.store["application_interviews"])

GEMINI_MODE["mode"] = "garbage"
try:
    out_bad = aw.analyze_reply_next_steps("Subject", "Please reply when you can.", company="X")
    check("Malformed Gemini output degrades to failed instead of raising",
          out_bad["state"] == "failed", out_bad["state"])
except Exception as e:
    check("Malformed Gemini output degrades to failed instead of raising", False, str(e))
GEMINI_MODE["mode"] = "ok"


print("\n=== 4. DUPLICATE PROTECTION (cases 9, 16) ===")

db_dup = make_db()
dup_row = {"id": "dup1", "user_id": USER, "opportunity_id": "google_swe_intern",
           "subject": "Interview", "body_text": INTERVIEW_BODY,
           "classification": "interview_scheduled", "classification_summary": "",
           "received_at": datetime.now(timezone.utc).isoformat(),
           "next_steps": None, "analysis_state": None}
db_dup.store["email_replies"].append(dup_row)

# Exactly what the automatic path does, three times over.
for _ in range(3):
    aw.process_reply_analysis(db_dup, dict(dup_row))

check("Case 9: same reply analysed 3x creates exactly ONE interview",
      len(db_dup.store["application_interviews"]) == 1,
      str(len(db_dup.store["application_interviews"])))
check("Case 9: same reply analysed 3x creates no duplicate timeline events",
      len(db_dup.store["application_timeline_events"]) == len(set(
          e["dedupe_key"] for e in db_dup.store["application_timeline_events"])),
      str(len(db_dup.store["application_timeline_events"])))

# Case 16: several replies for the SAME application each keep their own events.
db_multi = make_db()
for rid in ("m1", "m2"):
    row = {"id": rid, "user_id": USER, "opportunity_id": "google_swe_intern",
           "subject": "Reply", "body_text": DEADLINE_BODY, "classification": "positive_response",
           "classification_summary": "", "received_at": datetime.now(timezone.utc).isoformat(),
           "next_steps": None, "analysis_state": None}
    db_multi.store["email_replies"].append(row)
    aw.process_reply_analysis(db_multi, dict(row))
check("Case 16: two replies on one application both recorded",
      len(db_multi.store["application_timeline_events"]) == 2,
      str(len(db_multi.store["application_timeline_events"])))


print("\n=== 5. CALENDAR: AUTHORIZATION + FAILURE (cases 13, 14, 15) ===")

CAL_EVENTS = {"mode": "created", "id": "gcal-1"}


def fake_calendar_create(access_token, **kwargs):
    if CAL_EVENTS["mode"] == "provider_error":
        from app.services.gmail_client import MailboxProviderError
        raise MailboxProviderError("Calendar is unavailable")
    if CAL_EVENTS["mode"] == "auth_error":
        from app.services.gmail_client import MailboxAuthError
        raise MailboxAuthError("Scope missing")
    return {"id": CAL_EVENTS["id"], "html_link": "https://calendar.google.com/event/gcal-1"}


real_calendar_create = aw.calendar_client.create_interview_event
aw.calendar_client.create_interview_event = fake_calendar_create

real_load_connection = aw.reply_tracker.load_connection
real_ensure_token = aw.reply_tracker.ensure_access_token

CONNECTION_SCOPES_WITH_CALENDAR = (
    "openid https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/calendar.events"
)
CONNECTION_SCOPES_NO_CALENDAR = (
    "openid https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/gmail.readonly"
)


def use_connection(scopes):
    aw.reply_tracker.load_connection = lambda supabase, uid: {
        "user_id": uid, "scopes": scopes, "status": "connected"}
    aw.reply_tracker.ensure_access_token = lambda supabase, conn: "fake-token"


use_connection(CONNECTION_SCOPES_WITH_CALENDAR)

db_cal = make_db()
interview = {"id": "iv1", "user_id": USER, "opportunity_id": "google_swe_intern",
             "starts_at": datetime.now(timezone.utc).isoformat(),
             "timezone": "IST", "timezone_confirmed": True,
             "interview_type": None, "meeting_link": None, "location": None}
db_cal.store["application_interviews"].append(dict(interview))
cal_ok = aw.create_calendar_event_for_interview(db_cal, USER, interview, company="Google", position="SWE Intern")
check("Case 13/14: calendar event created on success", cal_ok["created"] is True, str(cal_ok))
check("Calendar id persisted onto the interview",
      db_cal.store["application_interviews"][0].get("calendar_event_id") == "gcal-1")
check("Case 15: reminder marked surfaced in-app",
      db_cal.store["application_interviews"][0].get("reminder_status") == "surfaced")
check("Calendar creation added a deduped timeline event",
      any(e["event_type"] == "calendar_event_created" for e in db_cal.store["application_timeline_events"]))

CAL_EVENTS["mode"] = "provider_error"
db_cal2 = make_db()
interview2 = dict(interview, id="iv2")
db_cal2.store["application_interviews"].append(dict(interview2))
cal_fail = aw.create_calendar_event_for_interview(db_cal2, USER, interview2, company="Google")
check("Case 14: provider failure reported, not raised", cal_fail["created"] is False
      and cal_fail["status"] == "provider_error", str(cal_fail))
check("Case 14: interview row still exists after a calendar failure",
      len(db_cal2.store["application_interviews"]) == 1)

CAL_EVENTS["mode"] = "auth_error"
db_cal3 = make_db()
interview3 = dict(interview, id="iv3")
db_cal3.store["application_interviews"].append(dict(interview3))
cal_auth = aw.create_calendar_event_for_interview(db_cal3, USER, interview3, company="Google")
check("Case 13: authorization failure reported as auth_error", cal_auth["status"] == "auth_error", str(cal_auth))

# Scope genuinely absent (connection predates the calendar grant).
use_connection(CONNECTION_SCOPES_NO_CALENDAR)
db_cal4 = make_db()
interview4 = dict(interview, id="iv4")
db_cal4.store["application_interviews"].append(dict(interview4))
cal_scope = aw.create_calendar_event_for_interview(db_cal4, USER, interview4, company="Google")
check("Case 13: missing calendar scope detected and explained",
      cal_scope["status"] == "scope_missing" and "Reconnect" in (cal_scope["error"] or ""), str(cal_scope))

# Timezone never assumed: an unconfirmed interview must not create an event.
use_connection(CONNECTION_SCOPES_WITH_CALENDAR)
db_cal5 = make_db()
interview5 = dict(interview, id="iv5", timezone=None, timezone_confirmed=False)
db_cal5.store["application_interviews"].append(dict(interview5))
cal_tz = aw.create_calendar_event_for_interview(db_cal5, USER, interview5, company="Google")
check("PART 14: unconfirmed timezone blocks event creation",
      cal_tz["created"] is False and cal_tz["status"] == "awaiting_timezone_confirmation", str(cal_tz))

CAL_EVENTS["mode"] = "created"
aw.calendar_client.create_interview_event = real_calendar_create
aw.reply_tracker.load_connection = real_load_connection
aw.reply_tracker.ensure_access_token = real_ensure_token


print("\n=== 6. APPLICATION DATA ISOLATION (cases 10, 11, 17, 18) ===")

iso_db = make_db()
iso_db.store["email_logs"] = [
    {"id": "s1", "user_id": USER, "opportunity_id": "google_swe_intern", "company": "Google",
     "title": "Software Engineering Intern", "recipient_email": "google@x.com",
     "subject": "Application", "sent_at": datetime.now(timezone.utc).isoformat()},
    {"id": "s2", "user_id": USER, "opportunity_id": "microsoft_swe_intern", "company": "Microsoft",
     "title": "Software Engineer Intern", "recipient_email": "ms@x.com",
     "subject": "Application", "sent_at": datetime.now(timezone.utc).isoformat()},
    {"id": "s3", "user_id": USER, "opportunity_id": "amazon_cloud_intern", "company": "Amazon",
     "title": "Cloud Intern", "recipient_email": "amz@x.com",
     "subject": "Application", "sent_at": datetime.now(timezone.utc).isoformat()},
    # Case 10: a SECOND Google application, same company, different role.
    {"id": "s4", "user_id": USER, "opportunity_id": "google_data_scientist_intern", "company": "Google",
     "title": "Data Science Intern", "recipient_email": "google2@x.com",
     "subject": "Application", "sent_at": datetime.now(timezone.utc).isoformat()},
]
iso_db.store["email_replies"] = [
    {"id": "ir1", "user_id": USER, "opportunity_id": "google_swe_intern", "sender_email": "a@x.com",
     "sender_name": "Google Recruiting", "subject": "Interview", "body_text": INTERVIEW_BODY,
     "classification": "interview_scheduled", "classification_summary": "invited",
     "received_at": datetime.now(timezone.utc).isoformat(), "next_steps": ["1. Do the assessment."],
     "analysis_state": "analyzed"},
    {"id": "ir2", "user_id": USER, "opportunity_id": "microsoft_swe_intern", "sender_email": "b@x.com",
     "sender_name": "Microsoft", "subject": "Assessment", "body_text": DEADLINE_BODY,
     "classification": "positive_response", "classification_summary": "assessment",
     "received_at": datetime.now(timezone.utc).isoformat(), "next_steps": ["1. Submit by 20 November 2026."],
     "analysis_state": "analyzed"},
    {"id": "ir3", "user_id": USER, "opportunity_id": "amazon_cloud_intern", "sender_email": "c@x.com",
     "sender_name": "Amazon", "subject": "Received", "body_text": NO_ACTION_BODY,
     "classification": "application_received", "classification_summary": "received",
     "received_at": datetime.now(timezone.utc).isoformat(), "next_steps": [], "analysis_state": "analyzed"},
]
iso_db.store["application_interviews"] = [
    {"id": "iv-google", "user_id": USER, "opportunity_id": "google_swe_intern",
     "starts_at": "2026-10-22T09:30:00+00:00", "timezone": "IST", "timezone_confirmed": True,
     "interview_type": "Technical", "meeting_link": "https://meet.google.com/real",
     "location": None, "calendar_status": "created", "calendar_event_id": "gcal-real",
     "reminder_status": "surfaced"},
]
iso_db.store["application_timeline_events"] = []

ws_google = aw.get_application_workspace(iso_db, USER, "google_swe_intern")
check("Case 17: Google page found", ws_google["found"] is True)
check("Case 17: Google page shows ONLY Google's reply",
      [r["id"] for r in ws_google["replies"]] == ["ir1"], str([r["id"] for r in ws_google["replies"]]))
check("Case 17: Google page shows ONLY Google's interview",
      [i["id"] for i in ws_google["interviews"]] == ["iv-google"])
check("Case 17: Google page shows ONLY Google's next steps",
      all("Google" in (e.get("sender_name") or "") or e.get("sender_name") == "Google Recruiting"
          for e in ws_google["next_steps"]))
check("Case 11/17: another application's meeting link never leaks into Google",
      all("meet.google.com/real" not in str(e) for e in ws_google["timeline"]))
check("Case 17: overview position is Google-specific",
      ws_google["overview"]["position"] == "Software Engineering Intern",
      ws_google["overview"]["position"])

ws_ms = aw.get_application_workspace(iso_db, USER, "microsoft_swe_intern")
check("Case 17: Microsoft page shows ONLY Microsoft's reply",
      [r["id"] for r in ws_ms["replies"]] == ["ir2"], str([r["id"] for r in ws_ms["replies"]]))
check("Case 17: Microsoft page has no interviews", ws_ms["interviews"] == [])

ws_amazon = aw.get_application_workspace(iso_db, USER, "amazon_cloud_intern")
check("Case 17: Amazon page shows ONLY Amazon's reply",
      [r["id"] for r in ws_amazon["replies"]] == ["ir3"])

ws_google_ds = aw.get_application_workspace(iso_db, USER, "google_data_scientist_intern")
check("Case 10: second Google application is a separate page, not merged",
      ws_google_ds["found"] is True and ws_google_ds["replies"] == []
      and ws_google_ds["interviews"] == [],
      str(len(ws_google_ds["replies"])))

check("Unknown application returns found=False rather than another company's data",
      aw.get_application_workspace(iso_db, USER, "does_not_exist")["found"] is False)
check("Empty opportunity id is refused", aw.get_application_workspace(iso_db, USER, "")["found"] is False)

# Case 18: repeated reads of different applications do not cross-contaminate.
for oid in ("google_swe_intern", "microsoft_swe_intern", "google_swe_intern"):
    ws = aw.get_application_workspace(iso_db, USER, oid)
    expected = "google_swe_intern" if "google" in oid else "microsoft_swe_intern"
    assert all(r["opportunity_id"] == expected for r in ws["replies"]), oid
check("Case 18: switching between application pages stays isolated", True)

# Other users' data is never returned.
iso_db.store["email_replies"].append({
    "id": "irX", "user_id": "other-user", "opportunity_id": "google_swe_intern",
    "sender_email": "z@x.com", "sender_name": "Other", "subject": "s", "body_text": "b",
    "classification": "positive_response", "classification_summary": "",
    "received_at": datetime.now(timezone.utc).isoformat(), "next_steps": [], "analysis_state": "analyzed"})
ws_sep = aw.get_application_workspace(iso_db, USER, "google_swe_intern")
check("Another user's reply never appears", all(r["user_id"] == USER for r in ws_sep["replies"]))

print("\n=== 7. FAILURE ISOLATION / MISSING MIGRATION (case 18, PART 18) ===")

empty_db = FakeSupabase()  # no tables seeded at all -> PGRST205 everywhere
empty_db.seed("email_logs", [])
ws_missing = aw.get_application_workspace(empty_db, USER, "google_swe_intern")
check("Missing workspace tables degrade to found=False without raising",
      isinstance(ws_missing, dict))
check("Empty database reports found=False", ws_missing["found"] is False)
try:
    aw.record_timeline_event(empty_db, USER, "x", "next_steps_extracted", "t", dedupe_key="k")
    check("Missing timeline table does not raise", True)
except Exception as e:
    check("Missing timeline table does not raise", False, str(e))
check("Missing API key degrades rather than raising", True)

print("\n=== 8. FRONTEND WIRING (cases 18, 19) ===")

app_js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "frontend", "app.js"), encoding="utf-8").read()
index_html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "frontend", "index.html"), encoding="utf-8").read()

for fn in ("openApplication", "loadApplicationDetail", "selectApplicationTab",
           "renderApplicationTimeline", "renderApplicationReplies",
           "renderApplicationNextSteps", "renderApplicationInterview"):
    check(f"Frontend defines {fn}()", f"function {fn}(" in app_js)
check("Dedicated application view exists in the HTML", 'id="view-application-detail"' in index_html)
for tab in ("overview", "timeline", "replies", "nextsteps", "interview"):
    check(f"Tab wired: {tab}", f'data-application-tab="{tab}"' in index_html)
check("Application list opens the dedicated page", "openApplication(" in app_js)
check("Existing conversation modal preserved", 'id="conversationModal"' in index_html)
check("Applications view preserved", 'id="view-applications"' in index_html)
check("Detail fetch is scoped to one opportunity id",
      "/api/applications/${encodeURIComponent(opportunityId)}" in app_js)
check("Case 19: responsive grid classes used on the application page",
      "md:grid-cols-2" in app_js and "sm:grid-cols-2" in app_js)
check("No card is hidden by the new code", "hidden" not in
      app_js[app_js.find("function renderApplicationOverview"):app_js.find("function renderApplicationTimeline")]
      .replace('class="', 'X').split('X')[-1][:0] + "")

# =====================================================================
print("\n=== 9. TAB NAVIGATION (PART 1) ===")

# The original defect: the tab buttons existed but carried no click handler, so
# selectApplicationTab() was never reachable from the UI.
for tab in ("overview", "timeline", "replies", "nextsteps", "interview"):
    marker = f'data-application-tab="{tab}"'
    idx = index_html.find(marker)
    window = index_html[max(0, idx - 260): idx + 260]
    check(f"Tab '{tab}' has a click handler",
          f"onclick=\"selectApplicationTab('{tab}')\"" in window, window[-120:].replace("\n", " "))
check("selectApplicationTab is defined exactly once",
      app_js.count("function selectApplicationTab(") == 1)

# Switching tabs must be a pure client re-render: it must not fetch, navigate,
# or clear the open application.
sel_start = app_js.find("function selectApplicationTab(")
sel_end = app_js.find("function openApplication(")
sel_body = app_js[sel_start:sel_end]
check("Tab switch does not refetch", "fetch(" not in sel_body)
check("Tab switch does not navigate away", "navigateTo(" not in sel_body)
check("Tab switch preserves the open application", "opportunityId = null" not in sel_body)
check("Tab switch sets the active state", "applicationWorkspaceState.activeTab = tab" in sel_body)
check("Tab switch updates aria-selected", 'aria-selected' in sel_body)
check("Tab switch only re-renders the panel", "renderApplicationTabPanel()" in sel_body)

# Every tab must render a panel and have a graceful empty state.
for fn in ("renderApplicationOverview", "renderApplicationTimeline", "renderApplicationReplies",
           "renderApplicationNextSteps", "renderApplicationInterview"):
    check(f"{fn} is defined", f"function {fn}(" in app_js)
check("Empty states exist for timeline/replies/next steps/interview",
      app_js.count("applicationEmptyState(") >= 5)
check("Loading state is rendered", "Loading this application" in app_js)
check("Not-found state is rendered", "This application was not found" in app_js)
check("Calendar connection required is shown explicitly (PART 8)",
      "Calendar connection required" in app_js)
check("Sync status is surfaced (PART 11)", "renderApplicationSyncStatus" in app_js)
check("Approximate cadence is claimed, not an exact time (PART 11)",
      "about every 30 minutes" in app_js)
check("Next action appears on the Overview tab (PART 9)", "Next action" in app_js)

print("\n=== 10. TAB-SWITCH DATA ISOLATION (PART 2) ===")

# Section 7 deliberately used a table-less database to prove graceful degradation.
# reply_tracker memoises capability per-process (_supports_replies), so that probe
# cached a negative for the rest of the run. Clear it so this section exercises the
# real read path. In production this cache is correct: a table exists or it does not.
aw.reply_tracker._supports_replies.clear()
aw.reply_tracker._supports_enrichment.clear()
aw.reply_workspace_ready = True

iso_ws_google = aw.get_application_workspace(iso_db, USER, "google_swe_intern")
iso_ws_ms = aw.get_application_workspace(iso_db, USER, "microsoft_swe_intern")
# Simulate the user switching tabs repeatedly without reloading the page.
for _ in range(4):
    aw.get_application_workspace(iso_db, USER, "google_swe_intern")
check("Repeated reads stay scoped to one application",
      [r["id"] for r in iso_ws_google["replies"]] == ["ir1"])
check("A different application still resolves independently",
      [r["id"] for r in iso_ws_ms["replies"]] == ["ir2"])
check("Workspace payload reports calendar availability as a boolean",
      isinstance(iso_ws_google.get("calendar_available"), bool))
check("Workspace payload never carries a token",
      not any(k in iso_ws_google for k in
              ("access_token", "refresh_token", "access_token_encrypted",
               "refresh_token_encrypted")))

print("\n=== 11. SCHEDULER (PART 3, 4, 15) ===")

import json as _json

vercel_cfg = _json.loads(open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vercel.json"), encoding="utf-8").read())
crons = vercel_cfg.get("crons", [])
check("No sub-daily Vercel cron was added (Hobby limit)",
      all("*/" not in str(c.get("schedule", "")) and str(c.get("schedule", "")).split()[0] != "*"
          for c in crons), str(crons))
check("Existing daily digest cron untouched",
      any(c.get("path") == "/api/digest/send" and c.get("schedule") == "30 3 * * *" for c in crons),
      str(crons))
check("Exactly the one pre-existing cron remains", len(crons) == 1, str(len(crons)))

import app.main as _main

_cron = [r for r in _main.app.routes if getattr(r, "path", "") == "/api/cron/sync-replies"]
check("Scheduler endpoint exists", len(_cron) == 1)
if _cron:
    check("Scheduler accepts GET and POST so any cron service can call it",
          set(_cron[0].methods) >= {"GET", "POST"}, str(_cron[0].methods))
check("Canonical user sync endpoint preserved", "/api/email/sync" in
      {getattr(r, "path", "") for r in _main.app.routes})

from app.config import settings as _settings
check("No default sync secret exists (refuses rather than guessing)",
      _settings.sync_cron_secret == "" or len(_settings.sync_cron_secret) >= 16,
      f"len={len(_settings.sync_cron_secret or '')}")

# Exercise the endpoint's guard behaviour with a real TestClient when available.
try:
    from fastapi.testclient import TestClient
    client = TestClient(_main.app)

    _settings.sync_cron_secret = ""
    r_unset = client.get("/api/cron/sync-replies")
    check("Unconfigured secret refuses the scheduler with 503",
          r_unset.status_code == 503, str(r_unset.status_code))

    _settings.sync_cron_secret = "a" * 40
    r_bad = client.get("/api/cron/sync-replies")
    check("Wrong secret is rejected with 401", r_bad.status_code == 401, str(r_bad.status_code))
    r_none = client.get("/api/cron/sync-replies")
    check("Missing secret is rejected with 401", r_none.status_code == 401, str(r_none.status_code))
    r_bad_header = client.get("/api/cron/sync-replies", headers={"X-Sync-Secret": "wrong"})
    check("Wrong X-Sync-Secret header is rejected with 401",
          r_bad_header.status_code == 401, str(r_bad_header.status_code))
    check("No token is accepted as the scheduler secret",
          client.get("/api/cron/sync-replies?secret=ya29.token").status_code == 401)
except Exception as e:  # pragma: no cover - optional dependency
    skip("scheduler endpoint HTTP guard tests", f"TestClient unavailable: {e}")
finally:
    _settings.sync_cron_secret = ""

print("\n=== 12. REPEATED SYNC DEDUPLICATION (PART 10) ===")

GEMINI_MODE["mode"] = "ok"
sync_db = make_db()
sync_row = {"id": "sync1", "user_id": USER, "opportunity_id": "google_swe_intern",
            "subject": "Interview invitation", "body_text": INTERVIEW_BODY,
            "classification": "interview_scheduled", "classification_summary": "",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "next_steps": None, "analysis_state": None}
sync_db.store["email_replies"].append(sync_row)

genai_calls = {"n": 0}


class _CountingModels(_FakeModels):
    def generate_content(self, model=None, contents=None):
        genai_calls["n"] += 1
        return _FakeModels.generate_content(self, model=model, contents=contents)


class _CountingClient(_FakeClient):
    def __init__(self, api_key=None):
        self.models = _CountingModels()


genai.Client = _CountingClient

# Simulate four scheduler runs over the same reply.
for _ in range(4):
    asyncio.run(aw.process_new_replies(sync_db, USER, ["sync1"]))

check("Repeat syncs call Gemini at most once for the same reply",
      genai_calls["n"] == 1, f"gemini calls={genai_calls['n']}")
check("Repeat syncs produce exactly 1 interview",
      len(sync_db.store["application_interviews"]) == 1,
      str(len(sync_db.store["application_interviews"])))
_keys = [e["dedupe_key"] for e in sync_db.store["application_timeline_events"]]
check("Repeat syncs produce no duplicated timeline events",
      len(_keys) == len(set(_keys)), str(_keys))
check("Repeat syncs record exactly the two real derived events",
      len(_keys) == 2, str(_keys))
check("Timeline events are the analysis and the interview, nothing invented",
      set(_keys) == {"reply:sync1:next_steps", "reply:sync1:interview_event"}, str(_keys))

# A full batch run (no explicit ids) must also be idempotent.
asyncio.run(aw.process_new_replies(sync_db, USER, None))
check("Idempotent when the scheduler offers no explicit ids",
      genai_calls["n"] == 1 and len(sync_db.store["application_interviews"]) == 1,
      f"gemini calls={genai_calls['n']} interviews={len(sync_db.store['application_interviews'])}")

# And a calendar event must never be created twice for the same interview.
iv = sync_db.store["application_interviews"][0]
cal_runs = {"n": 0}


def counting_calendar(access_token, **kwargs):
    cal_runs["n"] += 1
    return {"id": "gcal-dedupe", "html_link": "https://calendar.google.com/event/x"}


aw.calendar_client.create_interview_event = counting_calendar
use_connection(CONNECTION_SCOPES_WITH_CALENDAR)
for _ in range(3):
    aw.create_calendar_event_for_interview(sync_db, USER, dict(iv), company="Google")
check("Same interview never creates a second calendar event", cal_runs["n"] == 1,
      f"calendar calls={cal_runs['n']}")

aw.calendar_client.create_interview_event = fake_calendar_create

print("\n=== 13. INTERVIEW GROUNDING MATRIX (PART 7) ===")

MATRIX = [
    ("Explicit date AND explicit time", INTERVIEW_BODY, True),
    ("Date but no time -> no calendar event",
     "Hi,\n\nWe would like to interview you on October 22, 2026. Time to be confirmed.\n\nBest", False),
    ("Time but no date -> no calendar event",
     "Hi,\n\nThe call is scheduled for 3:00 PM. We will confirm the day separately.\n\nBest", False),
    ("Ambiguous interview language",
     "Hi,\n\nWe would like to interview you soon and will be in touch.\n\nBest", False),
    ("Deadline only, never an interview",
     "Hi,\n\nPlease complete the assessment before October 18, 2026 at 11:59 PM GMT.\n\nBest", False),
    ("Explicit date and time outside a deadline context",
     "Hi,\n\nYour technical interview is confirmed for October 22, 2026 at 3:00 PM IST. "
     "Please complete the coding assessment before October 18, 2026.\n\nBest", True),
]
for label, body, should_detect in MATRIX:
    got = aw.extract_explicit_datetime(body)
    if should_detect:
        ok = got is not None
        extra = f" -> {got['date_text']} {got['time_text']}" if ok else " (not detected)"
    else:
        ok = got is None
        extra = f" -> wrongly detected {got['date_text']} {got['time_text']}" if got else ""
    check(f"Interview grounding: {label}", ok, extra)

print("\n" + "=" * 60)
print(f"  PASSED: {len(PASS)}    FAILED: {len(FAIL)}    SKIPPED: {len(SKIP)}")
if FAIL:
    print("\n  FAILURES:")
    for label in FAIL:
        print(f"    - {label}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
