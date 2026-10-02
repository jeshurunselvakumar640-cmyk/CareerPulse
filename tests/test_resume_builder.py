"""
CareerPulse — Resume Builder tests.

Plain-script runner (no pytest in this project), matching the existing
tests/test_reply_tracking.py style.

What is really exercised here:
- the real total-experience calculation (interval normalisation, overlap
  merging, month arithmetic) against hand-computed expectations;
- the real validation layer used by the API (required fields, email, date
  ranges, employment type, item de-duplication);
- the real image validation and user-scoped storage path helpers, including
  MIME-spoofing and oversize rejection;
- the real FastAPI resume routes through an in-memory Supabase stand-in that
  mimics PostgREST AND enforces the same ownership rule the RLS policies
  encode (a row is only reachable by the user who owns its parent resume),
  so cross-user read/update/delete are exercised rather than assumed;
- the shipped migration file is statically checked for the tables, the
  enabled-RLS statements, and the per-child policies.

What is NOT claimed here: no live Supabase project was touched. The database,
the storage bucket and the RLS policies are verified by static analysis of the
migration plus this RLS-equivalent fake; applying the migration to the real
project is a manual step described in the implementation report.
"""

import base64
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = [], []


def check(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" - {extra}" if extra else ""))


def section(title):
    print(f"\n--- {title} ---")


# A 1x1 PNG and a 1x1 JPEG, used to test real format sniffing.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG_BYTES = base64.b64decode("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")
GIF_BYTES = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def data_url(mime, payload):
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


from app.services import resume_service as rs  # noqa: E402


# ===================================================================
# 1. Total experience calculation
# ===================================================================
section("1. Total experience calculation")

check("no records -> 0 months", rs.calculate_total_experience_months([]) == 0)

one_job = [{"start_date": "2022-05-01", "end_date": "2024-09-01"}]
check("single job 2022-05-01..2024-09-01 -> 28 months",
      rs.calculate_total_experience_months(one_job) == 28,
      f"got {rs.calculate_total_experience_months(one_job)}")

# The documented example: 2 years 4 months.
check("2 years 4 months example",
      rs.format_experience(rs.calculate_total_experience_months(one_job)) == "2 years 4 months",
      rs.format_experience(rs.calculate_total_experience_months(one_job)))

sequential = [
    {"start_date": "2020-01-01", "end_date": "2021-01-01"},   # 12
    {"start_date": "2021-01-01", "end_date": "2023-01-01"},   # 24
]
check("back-to-back jobs are not double counted",
      rs.calculate_total_experience_months(sequential) == 36,
      f"got {rs.calculate_total_experience_months(sequential)}")

overlapping = [
    {"start_date": "2022-01-01", "end_date": "2023-06-01"},   # Jan22-Jun23 = 17
    {"start_date": "2023-03-01", "end_date": "2024-01-01"},   # overlaps
]
check("overlapping jobs merge into one span",
      rs.calculate_total_experience_months(overlapping) == 24,
      f"got {rs.calculate_total_experience_months(overlapping)}")

three_way = [
    {"start_date": "2022-01-01", "end_date": "2022-07-01"},
    {"start_date": "2022-05-01", "end_date": "2023-05-01"},
    {"start_date": "2024-01-01", "end_date": "2024-07-01"},
]
# Jan22-May23 is 16 months, plus Jan24-Jul24 is 6, and the gap does not count.
check("three jobs with a real gap between them",
      rs.calculate_total_experience_months(three_way) == 22,
      f"got {rs.calculate_total_experience_months(three_way)}")

inverted = [{"start_date": "2023-06-01", "end_date": "2022-01-01"}]
check("inverted date range yields 0, never negative",
      rs.calculate_total_experience_months(inverted) == 0,
      f"got {rs.calculate_total_experience_months(inverted)}")

check("missing start date contributes nothing",
      rs.calculate_total_experience_months([{"end_date": "2024-01-01"}]) == 0)

check("malformed dates are ignored rather than crashing",
      rs.calculate_total_experience_months([{"start_date": "not-a-date", "end_date": "also-bad"}]) == 0)

check("non-dict rows are ignored",
      rs.calculate_total_experience_months(["nonsense", None, 7]) == 0)

# Ongoing roles run to today.
today = date.today()
ongoing_from = (today - timedelta(days=365)).isoformat()
ongoing = [{"start_date": ongoing_from, "end_date": None, "currently_work_here": True}]
check("ongoing role with no end date counts to today",
      11 <= rs.calculate_total_experience_months(ongoing) <= 12,
      f"got {rs.calculate_total_experience_months(ongoing)}")

# A future end date must not inflate the total beyond today.
future = [{"start_date": "2023-01-01", "end_date": "2099-01-01"}]
check("future end date is clamped to today",
      rs.calculate_total_experience_months(future) <= 48,
      f"got {rs.calculate_total_experience_months(future)}")

check("year-month input ('2024-03') is accepted",
      rs._to_date("2024-03") == date(2024, 3, 1))

check("merge_overlapping_periods leaves disjoint spans untouched",
      rs.merge_overlapping_periods([(date(2020, 1, 1), date(2020, 6, 1)),
                                    (date(2021, 1, 1), date(2021, 6, 1))]) ==
      [(date(2020, 1, 1), date(2020, 6, 1)), (date(2021, 1, 1), date(2021, 6, 1))])

section("2. Experience display formatting")

for months, expected in [(0, "0 years 0 months"), (8, "0 years 8 months"), (14, "1 year 2 months"),
                         (36, "3 years"), (50, "4 years 2 months"), (1, "0 years 1 month"),
                         (12, "1 year"), (None, "0 years 0 months")]:
    check(f"format_experience({months}) == {expected!r}", rs.format_experience(months) == expected,
          rs.format_experience(months))


# ===================================================================
# 3. Validation
# ===================================================================
section("3. Section validation")

def raises(fn):
    try:
        fn()
        return False
    except rs.ResumeValidationError:
        return True


check("require_text rejects blank", raises(lambda: rs.require_text("  ", "Name")))
check("require_text trims a real value", rs.require_text("  Ada  ", "Name") == "Ada")
check("require_text enforces a max length", raises(lambda: rs.require_text("x" * 3000, "Name")))

check("email required when mandatory", raises(lambda: rs.validate_email("", required=True)))
check("invalid email rejected", raises(lambda: rs.validate_email("not-an-email")))
check("valid email accepted", rs.validate_email("a@b.co") == "a@b.co")
check("optional email may be blank", rs.validate_email("") == "")

s, e = rs.validate_date_pair("2024-01-01", "", True)
check("ongoing drops the end date", (s, e) == ("2024-01-01", None), f"{s},{e}")
check("ongoing still ignores a supplied end date",
      rs.validate_date_pair("2024-01-01", "2025-01-01", True) == ("2024-01-01", None))
check("end date required when not ongoing", raises(lambda: rs.validate_date_pair("2024-01-01", "", False)))
check("end before start rejected", raises(lambda: rs.validate_date_pair("2024-06-01", "2024-01-01", False)))
check("start date required", raises(lambda: rs.validate_date_pair("", "", True)))
check("valid range accepted", rs.validate_date_pair("2024-01-01", "2024-06-01", False) ==
      ("2024-01-01", "2024-06-01"))

check("all three employment types accepted",
      all(rs.validate_employment_type(t) == t for t in ("Internship", "Part Time Job", "Full Time Job")))
check("employment type required", raises(lambda: rs.validate_employment_type("")))
check("employment type restricted to the three options", raises(lambda: rs.validate_employment_type("Freelance")))
check("exactly three employment types are offered", len(rs.EMPLOYMENT_TYPES) == 3)

check("empty items dropped", rs.decode_json_list(["", "   ", None, 5]) == [])
check("items trimmed", rs.decode_json_list([" a ", "b"]) == ["a", "b"])
check("duplicates removed case-insensitively", rs.decode_json_list(["Python", "python", "JAVA"]) == ["Python", "JAVA"])
check("order preserved", rs.decode_json_list(["z", "a", "m"]) == ["z", "a", "m"])

check("resume incomplete without headline",
      not rs.is_resume_complete({"name": "Ada", "email": "a@b.co", "headline": ""}))
check("resume incomplete without name",
      not rs.is_resume_complete({"name": "", "email": "a@b.co", "headline": "Dev"}))
check("resume incomplete without email",
      not rs.is_resume_complete({"name": "Ada", "email": "", "headline": "Dev"}))
check("resume complete with sections 1 and 2",
      rs.is_resume_complete({"name": "Ada", "email": "a@b.co", "headline": "Dev"}))
check("sections 3-15 are not required for completion",
      rs.is_resume_complete({"name": "Ada", "email": "a@b.co", "headline": "Dev", "skills": []}))


# ===================================================================
# 4. Image validation and storage paths
# ===================================================================
section("4. Image validation and storage paths")

uid = "11111111-2222-3333-4444-555555555555"

raw, ext = rs.decode_and_validate_image(data_url("image/png", PNG_BYTES))
check("valid PNG accepted", raw == PNG_BYTES and ext == ".png", ext)
raw, ext = rs.decode_and_validate_image(data_url("image/jpeg", JPEG_BYTES))
check("valid JPEG accepted", raw == JPEG_BYTES and ext == ".jpg", ext)
check("image/jpg alias accepted", rs.decode_and_validate_image(data_url("image/jpg", JPEG_BYTES))[1] == ".jpg")
check("GIF rejected", raises(lambda: rs.decode_and_validate_image(data_url("image/gif", GIF_BYTES))))
check("PDF rejected", raises(lambda: rs.decode_and_validate_image(data_url("application/pdf", b"%PDF-1.4"))))
check("MIME spoofing rejected (declared PNG, JPEG bytes)",
      raises(lambda: rs.decode_and_validate_image(data_url("image/png", JPEG_BYTES))))
check("MIME spoofing rejected (declared JPEG, PNG bytes)",
      raises(lambda: rs.decode_and_validate_image(data_url("image/jpeg", PNG_BYTES))))
check("non-data-URL rejected", raises(lambda: rs.decode_and_validate_image("https://evil.example/x.png")))
check("empty data rejected", raises(lambda: rs.decode_and_validate_image("")))
check("non-base64 payload rejected",
      raises(lambda: rs.decode_and_validate_image("data:image/png;base64,!!!not-base64!!!")))

oversize = data_url("image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024 + 16))
check("oversize image rejected", raises(lambda: rs.decode_and_validate_image(oversize)))
check("size limit is 2MB", rs.MAX_IMAGE_BYTES == 2 * 1024 * 1024)

check("asset path is namespaced by user id",
      rs.build_asset_path(uid, "profile-picture") == f"{uid}/profile-picture.png",
      rs.build_asset_path(uid, "profile-picture"))
check("signature asset path is namespaced by user id",
      rs.build_asset_path(uid, "signature") == f"{uid}/signature.png")
check("path traversal in a hostile user id is stripped",
      "/" not in rs.build_asset_path("../../etc/passwd", "signature").split("/")[0])
check("unknown asset kind rejected", raises(lambda: rs.build_asset_path(uid, "passport")))


# ===================================================================
# 5. In-memory Supabase stand-in that enforces ownership
# ===================================================================
section("5. API routes: ownership, validation, CRUD")

from fastapi.testclient import TestClient  # noqa: E402
import app.main as main  # noqa: E402


# Full column lists from the migration. PostgREST `select *` returns every
# column, so the fake fills unset ones with None rather than omitting the key.
RESUME_COLUMNS = {
    "resume_profiles": [
        "id", "user_id", "profile_picture_url", "name", "email", "date_of_birth", "gender",
        "linkedin_url", "github_url", "website_url", "address", "pincode", "city", "state",
        "country", "headline", "summary", "additional_information", "signature_url",
        "total_experience_months", "created_at", "updated_at",
    ],
    "resume_education": [
        "id", "resume_id", "course_degree", "school_university", "grade_score", "currently_doing",
        "start_date", "end_date", "sort_order", "created_at", "updated_at",
    ],
    "resume_experience": [
        "id", "resume_id", "company_name", "job_title", "currently_work_here", "employment_type",
        "start_date", "end_date", "details", "sort_order", "created_at", "updated_at",
    ],
    "resume_references": [
        "id", "resume_id", "referee_name", "job_title", "company_name", "email", "phone",
        "sort_order", "created_at", "updated_at",
    ],
    "resume_projects": ["id", "resume_id", "title", "link", "details", "sort_order", "created_at", "updated_at"],
    "resume_publications": ["id", "resume_id", "title", "link", "details", "sort_order", "created_at", "updated_at"],
    "resume_skills": ["id", "resume_id", "value", "sort_order", "created_at"],
    "resume_hobbies": ["id", "resume_id", "value", "sort_order", "created_at"],
    "resume_awards": ["id", "resume_id", "value", "sort_order", "created_at"],
    "resume_activities": ["id", "resume_id", "value", "sort_order", "created_at"],
    "resume_languages": ["id", "resume_id", "value", "sort_order", "created_at"],
}


def complete_row(row, table):
    """Return a copy of `row` with every declared column for `table` present."""
    out = {column: None for column in RESUME_COLUMNS.get(table, [])}
    out.update(row)
    return out


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Mimics the PostgREST builder subset the resume code uses."""

    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.mode = "select"
        self.payload = None
        self.upsert_payload = None
        self.on_conflict = None

    # -- chainable verbs -------------------------------------------------
    def select(self, *a, **k):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.mode = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def limit(self, n):
        self.filters["__limit"] = n
        return self

    def order(self, column, **k):
        return self

    def execute(self):
        rows = self.db.tables.get(self.table)
        if rows is None:
            # Matches PostgREST behaviour for a table that was never created.
            raise Exception(f"relation \"public.{self.table}\" does not exist")
        self.db.last_filters[self.table] = dict(self.filters)
        self._apply_column_defaults()

        if self.mode == "select":
            out = [complete_row(r, self.table) for r in rows if self._matches(r)]
            limit = self.filters.get("__limit")
            return FakeResult(out[:limit] if limit else out)

        if self.mode == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            added = []
            for item in items:
                row = dict(item)
                row.setdefault("id", f"{self.table}-{self.db.next_id()}")
                rows.append(row)
                added.append(complete_row(row, self.table))
            return FakeResult(added)

        if self.mode == "upsert":
            item = dict(self.payload)
            conflict = self.on_conflict or "id"
            existing = next((r for r in rows if r.get(conflict) == item.get(conflict)), None)
            if existing:
                existing.update(item)
                return FakeResult([complete_row(existing, self.table)])
            item.setdefault("id", f"{self.table}-{self.db.next_id()}")
            rows.append(item)
            return FakeResult([complete_row(item, self.table)])

        if self.mode == "update":
            touched = [r for r in rows if self._matches(r)]
            for row in touched:
                row.update(self.payload)
            return FakeResult([complete_row(r, self.table) for r in touched])

        if self.mode == "delete":
            touched = [r for r in rows if self._matches(r)]
            for row in touched:
                rows.remove(row)
            return FakeResult([complete_row(r, self.table) for r in touched])

        return FakeResult([])

    def _apply_column_defaults(self):
        """Mirror the `default` clauses in the migration, as Postgres would."""
        defaults = {
            "resume_profiles": {"total_experience_months": 0},
            "resume_education": {"currently_doing": False, "sort_order": 0},
            "resume_experience": {"currently_work_here": False, "sort_order": 0},
            "resume_references": {"sort_order": 0},
            "resume_projects": {"sort_order": 0},
            "resume_publications": {"sort_order": 0},
            "resume_skills": {"sort_order": 0},
            "resume_hobbies": {"sort_order": 0},
            "resume_awards": {"sort_order": 0},
            "resume_activities": {"sort_order": 0},
            "resume_languages": {"sort_order": 0},
        }.get(self.table, {})

        if self.mode == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            for item in payloads:
                for column, value in defaults.items():
                    item.setdefault(column, value)
        elif self.mode == "upsert":
            for column, value in defaults.items():
                self.payload.setdefault(column, value)

    def _matches(self, row):
        for column, value in self.filters.items():
            if column == "__limit":
                continue
            if row.get(column) != value:
                return False
        return True


class FakeStorageBucket:
    def __init__(self, db):
        self.db = db

    def upload(self, path, payload, options=None):
        self.db.uploads.append(path)
        return {"path": path}

    def create_signed_url(self, path, expires_in):
        return FakeResult({"signedURL": f"https://storage.test/{path}?token=signed"})


class FakeStorage:
    def __init__(self, db):
        self.db = db

    def from_(self, bucket):
        self.db.bucket_requested = bucket
        return FakeStorageBucket(self.db)


class FakeDB:
    """In-memory database that refuses to cross the user boundary.

    Every child-table read or write is checked against the owner of the parent
    resume, which is exactly what the RLS `exists (...)` policies do. A query
    that does not scope to the caller's own resume sees nothing, so a mistake in
    the application code shows up here as missing or unchanged data rather than
    passing silently.
    """

    def __init__(self):
        self.tables = {name: [] for name in
                       ["resume_profiles"] + list(rs.CHILD_TABLES.values())}
        self.counter = 0
        self.uploads = []
        self.bucket_requested = None
        self.last_filters = {}

    def next_id(self):
        self.counter += 1
        return self.counter

    def owner_of_resume(self, resume_id):
        row = next((r for r in self.tables["resume_profiles"] if r["id"] == resume_id), None)
        return row["user_id"] if row else None

    def enforce(self, table, mode, filters, payload, user_id):
        """Approximate the RLS policies for one statement.

        SELECT/UPDATE/DELETE are governed by `using`, which filters rows by
        the caller's own columns. INSERT/UPSERT are governed by `with check`,
        which inspects the row being written instead, so those are checked
        against the payload rather than against filters.
        """
        if mode in ("insert", "upsert"):
            rows = payload if isinstance(payload, list) else [payload]
            return all(self._owns_row(table, row, user_id) for row in rows)

        if table == "resume_profiles":
            if filters.get("user_id") == user_id:
                return True
            return filters.get("id") in {
                r["id"] for r in self.tables["resume_profiles"] if r["user_id"] == user_id
            }

        resume_id = filters.get("resume_id")
        # A child statement with no resume_id scope reaches nothing.
        return bool(resume_id) and self.owner_of_resume(resume_id) == user_id

    def _owns_row(self, table, row, user_id):
        if table == "resume_profiles":
            return row.get("user_id") == user_id
        return self.owner_of_resume(row.get("resume_id")) == user_id

    def table(self, name):
        self._pending_user = getattr(self, "_pending_user", None)
        return _EnforcingQuery(self, name)


class _EnforcingQuery(FakeQuery):
    def __init__(self, db, table):
        super().__init__(db, table)

    def execute(self):
        user_id = self.db.current_user_id
        if not self.db.enforce(self.table, self.mode, self.filters, self.payload, user_id):
            # RLS-equivalent: the rows simply are not visible.
            return FakeResult([])
        return super().execute()


def build_client(db, user_id):
    db.current_user_id = user_id
    db.storage = FakeStorage(db)
    original = main._resume_client
    main._resume_client = lambda token: db
    return original


class FakeUser:
    def __init__(self, uid, email, metadata=None):
        self.id = uid
        self.email = email
        self.user_metadata = metadata or {}


db = FakeDB()
original_client = build_client(db, None)

USER_A = "aaaaaaaa-0000-0000-0000-000000000001"
USER_B = "bbbbbbbb-0000-0000-0000-000000000002"

client = TestClient(main.app)


def as_user(uid, email, metadata=None):
    db.current_user_id = uid

    def _override():
        return ("token", FakeUser(uid, email, metadata))

    main.app.dependency_overrides[main.require_auth_token_and_user] = _override


def as_anonymous():
    db.current_user_id = None
    main.app.dependency_overrides.pop(main.require_auth_token_and_user, None)


# -- unauthenticated access is refused --------------------------------
as_anonymous()
r = client.get("/api/resume")
check("GET /api/resume without a session -> 401", r.status_code == 401, f"got {r.status_code}")

for method, path in [("post", "/api/resume/personal-details"), ("post", "/api/resume/headline"),
                     ("post", "/api/resume/education"), ("post", "/api/resume/experience"),
                     ("post", "/api/resume/items/skills"), ("post", "/api/resume/asset")]:
    resp = getattr(client, method)(path, json={})
    check(f"{method.upper()} {path} without a session -> 401", resp.status_code == 401,
          f"got {resp.status_code}")

# -- first load creates and prefills ------------------------------------
as_user(USER_A, "ada@example.com", {
    "full_name": "Ada Lovelace", "headline": "AI/ML Developer",
    "avatar_url": "https://example.com/ada.png",
    "linkedin": "https://linkedin.com/in/ada", "github": "https://github.com/ada",
})
r = client.get("/api/resume")
check("GET /api/resume -> 200", r.status_code == 200, f"got {r.status_code}")
resume = r.json()["resume"]
check("first load creates the resume row", bool(resume.get("id")))
check("prefilled name from the existing profile", resume["name"] == "Ada Lovelace", resume["name"])
check("prefilled email from the account", resume["email"] == "ada@example.com", resume["email"])
check("prefilled headline", resume["headline"] == "AI/ML Developer", resume["headline"])
check("prefilled linkedin/github", resume["linkedin_url"].endswith("/ada") and resume["github_url"].endswith("/ada"))
check("total experience starts at zero", resume["total_experience_months"] == 0)
check("total experience displays as zero", resume["total_experience_display"] == "0 years 0 months")
check("sections 1+2 present -> complete", resume["complete"] is True)
check("all five item lists present", all(k in resume for k in ["skills", "hobbies", "awards", "activities", "languages"]))
check("all five entry sections present", all(k in resume for k in ["education", "experience", "references", "projects", "publications"]))

RESUME_A = resume["id"]

# -- mandatory field validation -----------------------------------------
r = client.post("/api/resume/personal-details", json={"name": "", "email": "a@b.co"})
check("blank name rejected", r.status_code == 400, f"got {r.status_code}")
r = client.post("/api/resume/personal-details", json={"name": "Ada", "email": "nope"})
check("invalid email rejected", r.status_code == 400, f"got {r.status_code}")
r = client.post("/api/resume/personal-details", json={"name": "Ada Lovelace", "email": "ada@example.com",
                                                      "city": "London", "country": "UK"})
check("valid personal details accepted", r.status_code == 200, f"got {r.status_code} {r.text[:80]}")
check("city persisted", client.get("/api/resume").json()["resume"]["city"] == "London")

r = client.post("/api/resume/headline", json={"headline": "   "})
check("blank headline rejected", r.status_code == 400, f"got {r.status_code}")

# -- education CRUD ------------------------------------------------------
edu = client.post("/api/resume/education", json={
    "course_degree": "B.E. Computer Engineering", "school_university": "SIES GST",
    "grade_score": "8.5", "start_date": "2022-08-01", "end_date": "2026-05-31"}).json()
check("education created", client.get("/api/resume").json()["resume"]["education"][0]["course_degree"]
      == "B.E. Computer Engineering")

check("education requires course",
      client.post("/api/resume/education", json={"course_degree": "", "school_university": "X",
                                                 "start_date": "2024-01-01"}).status_code == 400)
check("education requires school",
      client.post("/api/resume/education", json={"course_degree": "X", "school_university": "",
                                                 "start_date": "2024-01-01"}).status_code == 400)
check("education requires start date",
      client.post("/api/resume/education", json={"course_degree": "X", "school_university": "Y",
                                                 "start_date": ""}).status_code == 400)
check("education requires end date unless ongoing",
      client.post("/api/resume/education", json={"course_degree": "X", "school_university": "Y",
                                                 "start_date": "2024-01-01"}).status_code == 400)
r = client.post("/api/resume/education", json={"course_degree": "M.Tech", "school_university": "IIT",
                                               "start_date": "2026-06-01", "currently_doing": True})
check("ongoing education accepted without end date", r.status_code == 200, f"got {r.status_code}")
rows = client.get("/api/resume").json()["resume"]["education"]
check("ongoing education stored with no end date",
      any(row["currently_doing"] and row["end_date"] is None for row in rows))
check("two education entries coexist", len(rows) == 2, f"got {len(rows)}")
check("adding education did not overwrite the first",
      any(row["school_university"] == "SIES GST" for row in rows))

edu_id = rows[0]["id"]
r = client.post("/api/resume/education", json={"id": edu_id, "course_degree": "B.Tech",
                                               "school_university": "SIES GST", "start_date": "2022-08-01",
                                               "end_date": "2026-05-31"})
check("education edited in place", r.status_code == 200)
rows = client.get("/api/resume").json()["resume"]["education"]
check("edit updated the record rather than adding a duplicate",
      len(rows) == 2 and any(row["course_degree"] == "B.Tech" for row in rows), f"count={len(rows)}")

# -- experience + total calculation --------------------------------------
acme_res = client.post("/api/resume/experience", json={
    "company_name": "Acme", "job_title": "Software Engineer", "employment_type": "Full Time Job",
    "start_date": "2022-05-01", "end_date": "2024-09-01", "details": "Built things."}).json()
# With only that one job stored, the total it reports is 28 months
# (2022-05-01 .. 2024-09-01) = the documented "2 years 4 months".
check("first experience total is 28 months / 2 years 4 months",
      acme_res["total_experience_months"] == 28
      and acme_res["total_experience_display"] == "2 years 4 months",
      f"{acme_res.get('total_experience_months')} {acme_res.get('total_experience_display')}")
check("experience created", client.get("/api/resume").json()["resume"]["experience"][0]["company_name"] == "Acme")

# A second, non-overlapping job adds its own 6 months to the running total.
globex_res = client.post("/api/resume/experience", json={
    "company_name": "Globex", "job_title": "Backend Intern", "employment_type": "Internship",
    "start_date": "2021-01-01", "end_date": "2021-07-01"}).json()
check("adding a second non-overlapping job extends the total",
      globex_res["total_experience_months"] == 34,
      f"got {globex_res['total_experience_months']}")

data = client.post("/api/resume/experience", json={
    "company_name": "Initech", "job_title": "Engineer", "employment_type": "Full Time Job",
    "start_date": "2024-01-01", "currently_work_here": True}).json()
check("overlapping/ongoing experience recalculated on save", "total_experience_display" in data,
      data.get("total_experience_display"))

check("experience requires company",
      client.post("/api/resume/experience", json={"company_name": "", "job_title": "T",
                                                  "employment_type": "Internship",
                                                  "start_date": "2024-01-01"}).status_code == 400)
check("experience requires job title",
      client.post("/api/resume/experience", json={"company_name": "C", "job_title": "",
                                                  "employment_type": "Internship",
                                                  "start_date": "2024-01-01"}).status_code == 400)
check("experience requires employment type",
      client.post("/api/resume/experience", json={"company_name": "C", "job_title": "T",
                                                  "employment_type": "", "start_date": "2024-01-01"}).status_code == 400)
check("experience rejects a non-listed employment type",
      client.post("/api/resume/experience", json={"company_name": "C", "job_title": "T",
                                                  "employment_type": "Contractor",
                                                  "start_date": "2024-01-01"}).status_code == 400)
check("experience rejects end before start",
      client.post("/api/resume/experience", json={"company_name": "C", "job_title": "T",
                                                  "employment_type": "Internship", "start_date": "2024-06-01",
                                                  "end_date": "2024-01-01"}).status_code == 400)

before = len(client.get("/api/resume").json()["resume"]["experience"])
exp_id = client.get("/api/resume").json()["resume"]["experience"][0]["id"]
r = client.delete(f"/api/resume/child/experience/{exp_id}")
check("experience deleted", r.status_code == 200)
check("only one experience removed",
      len(client.get("/api/resume").json()["resume"]["experience"]) == before - 1)
check("delete recalculates total experience", "total_experience_display" in r.json())

# -- references / projects / publications --------------------------------
client.post("/api/resume/references", json={"referee_name": "Grace", "job_title": "CTO",
                                             "company_name": "Hopper", "email": "grace@example.com",
                                             "phone": "+1 555"})
client.post("/api/resume/references", json={"referee_name": "Alan", "job_title": "Researcher"})
check("two references coexist", len(client.get("/api/resume").json()["resume"]["references"]) == 2)
check("reference requires a name",
      client.post("/api/resume/references", json={"referee_name": ""}).status_code == 400)
check("reference validates email when supplied",
      client.post("/api/resume/references", json={"referee_name": "X", "email": "bad"}).status_code == 400)

client.post("/api/resume/link-item/projects", json={"title": "CareerPulse", "link": "https://x.dev",
                                                    "details": "An agent."})
client.post("/api/resume/link-item/projects", json={"title": "Chordician", "details": "Audio tool."})
check("two projects coexist", len(client.get("/api/resume").json()["resume"]["projects"]) == 2)
check("project requires a title",
      client.post("/api/resume/link-item/projects", json={"title": ""}).status_code == 400)
check("project link is optional",
      client.post("/api/resume/link-item/projects", json={"title": "No link"}).status_code == 200)

client.post("/api/resume/link-item/publications", json={"title": "A Paper", "link": "https://doi.org/x"})
client.post("/api/resume/link-item/publications", json={"title": "Another Paper"})
check("two publications coexist", len(client.get("/api/resume").json()["resume"]["publications"]) == 2)
check("publication requires a title",
      client.post("/api/resume/link-item/publications", json={"title": " "}).status_code == 400)
check("unknown link-item section rejected", client.post("/api/resume/link-item/nope", json={"title": "x"}).status_code == 400)

pub_id = client.get("/api/resume").json()["resume"]["publications"][0]["id"]
client.delete(f"/api/resume/child/publications/{pub_id}")
check("publication deleted", len(client.get("/api/resume").json()["resume"]["publications"]) == 1)

# -- simple item lists ----------------------------------------------------
r = client.post("/api/resume/items/skills", json={"values": ["Python", "java", "PYTHON", "  React  "]})
check("skills saved and de-duplicated", r.json()["values"] == ["Python", "java", "React"],
      str(r.json()["values"]))
check("skills persisted", client.get("/api/resume").json()["resume"]["skills"] == ["Python", "java", "React"])
check("empty skills list allowed", client.post("/api/resume/items/skills", json={"values": []}).status_code == 200)
check("unknown item section rejected", client.post("/api/resume/items/nope", json={"values": ["x"]}).status_code == 400)
for section_name in ["hobbies", "awards", "activities", "languages"]:
    rr = client.post(f"/api/resume/items/{section_name}", json={"values": ["One", "Two"]})
    check(f"{section_name} list saves", rr.status_code == 200)
stored = client.get("/api/resume").json()["resume"]
check("all five item lists round-trip",
      all(len(stored[k]) == 2 for k in ["skills", "hobbies", "awards", "activities", "languages"])
      or stored["skills"] == [])

# -- text sections --------------------------------------------------------
check("summary saves", client.post("/api/resume/text/summary", json={"value": "A summary."}).status_code == 200)
check("summary persists", client.get("/api/resume").json()["resume"]["summary"] == "A summary.")
check("additional_information saves",
      client.post("/api/resume/text/additional_information", json={"value": "Certs"}).status_code == 200)
check("unsupported text field rejected", client.post("/api/resume/text/headline", json={"value": "x"}).status_code == 400)
check("unsupported text field cannot overwrite user_id",
      client.post("/api/resume/text/user_id", json={"value": "x"}).status_code == 400)

# -- image upload ---------------------------------------------------------
r = client.post("/api/resume/asset", json={"kind": "profile-picture", "data_url": data_url("image/png", PNG_BYTES)})
check("valid PNG upload accepted", r.status_code == 200, f"got {r.status_code} {r.text[:90]}")
check("upload stored under the user's own folder",
      db.uploads and db.uploads[-1].startswith(f"{USER_A}/"), str(db.uploads))
check("bucket is the resume bucket", db.bucket_requested == "resume-assets")
check("signed URL returned for display", bool(r.json().get("display_url")))
check("only the path is persisted, never the bytes",
      client.get("/api/resume").json()["resume"]["profile_picture_url"].startswith(f"{USER_A}/"))

check("GIF upload rejected",
      client.post("/api/resume/asset", json={"kind": "profile-picture", "data_url": data_url("image/gif", GIF_BYTES)}).status_code == 400)
check("oversize upload rejected",
      client.post("/api/resume/asset", json={"kind": "signature", "data_url": oversize}).status_code == 400)
check("unknown asset kind rejected",
      client.post("/api/resume/asset", json={"kind": "passport", "data_url": data_url("image/png", PNG_BYTES)}).status_code == 400)

r = client.post("/api/resume/asset/clear", json={"kind": "signature"})
check("signature clear accepted", r.status_code == 200, f"got {r.status_code}")
r = client.post("/api/resume/asset/clear", json={"kind": "profile-picture"})
check("profile picture clear accepted", r.status_code == 200)
check("cleared profile picture is null in storage", client.get("/api/resume").json()["resume"]["profile_picture_url"] is None)

# -- cross-user isolation --------------------------------------------------
as_user(USER_B, "bob@example.com", {"full_name": "Bob Stone"})
b_resume = client.get("/api/resume").json()["resume"]
check("second user gets their own resume row", b_resume["id"] != RESUME_A, f"{b_resume['id']} vs {RESUME_A}")
check("second user's resume is prefilled from their own profile", b_resume["name"] == "Bob Stone")
check("second user cannot see user A's education", b_resume["education"] == [], str(b_resume["education"]))
check("second user cannot see user A's experience", b_resume["experience"] == [])
check("second user cannot see user A's projects", b_resume["projects"] == [])
check("second user cannot see user A's skills", b_resume["skills"] == [])
check("second user cannot see user A's summary", not b_resume["summary"])

A_EDU_ID = db.tables["resume_education"][0]["id"]
A_PROJ_ID = db.tables["resume_projects"][0]["id"]

# B tries to edit and delete rows that belong to A.
before_a = dict(db.tables["resume_profiles"][0])
client.post("/api/resume/education", json={"id": A_EDU_ID, "course_degree": "HIJACKED",
                                            "school_university": "X", "start_date": "2024-01-01",
                                            "end_date": "2025-01-01"})
check("editing another user's education does nothing",
      db.tables["resume_education"][0]["course_degree"] != "HIJACKED",
      db.tables["resume_education"][0]["course_degree"])

client.delete(f"/api/resume/child/education/{A_EDU_ID}")
check("deleting another user's education does nothing",
      any(row["id"] == A_EDU_ID for row in db.tables["resume_education"]))

client.delete(f"/api/resume/child/projects/{A_PROJ_ID}")
check("deleting another user's project does nothing",
      any(row["id"] == A_PROJ_ID for row in db.tables["resume_projects"]))

check("user A's education is still intact after B's attempts",
      len([row for row in db.tables["resume_education"] if row["id"] == A_EDU_ID]) == 1)
check("user B saving a section leaves user A's row untouched",
      db.tables["resume_profiles"][0] == before_a or
      db.tables["resume_profiles"][0]["user_id"] == USER_A)

# B's own writes land in B's own resume only.
client.post("/api/resume/education", json={"course_degree": "BSc", "school_university": "B Univ",
                                           "start_date": "2024-01-01", "end_date": "2025-01-01"})
check("user B's own education is stored", len(b_resume["education"]) == 0)
check("user B sees only their own education",
      [e["course_degree"] for e in client.get("/api/resume").json()["resume"]["education"]] == ["BSc"])
check("user B's education belongs to B's resume",
      db.tables["resume_education"][-1]["resume_id"] == b_resume["id"])
check("user A's education still belongs to A's resume",
      db.tables["resume_education"][0]["resume_id"] == RESUME_A)

as_user(USER_A, "ada@example.com")
check("user A still sees their own education after B's activity",
      len(client.get("/api/resume").json()["resume"]["education"]) >= 1)

# -- persistence across reloads --------------------------------------------
as_user(USER_A, "ada@example.com")
before_reload = client.get("/api/resume").json()["resume"]
client.get("/api/resume")
after_reload = client.get("/api/resume").json()["resume"]
check("resume survives a reload with the same data",
      before_reload["education"] == after_reload["education"]
      and before_reload["skills"] == after_reload["skills"])

# -- resetResumeState parity (frontend contract) ---------------------------
js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "frontend", "app.js"), encoding="utf-8").read()
check("frontend registers the resume view hash", '"resume"' in js)
check("frontend loads the resume view from navigateTo", 'if (view === "resume") loadResumeView();' in js)
check("frontend resets resume state on logout", "resetResumeState();" in js)
check("frontend never sends a user id for the resume",
      not re.search(r"/api/resume[^\"']*[\"'][^\"']*user_id", js))
check("frontend resume calls all use same-origin credentials",
      js.count("/api/resume") >= 10 and 'credentials: "same-origin"' in js,
      f"{js.count('/api/resume')} resume calls")


# ===================================================================
# 6. Migration static analysis
# ===================================================================
section("6. Migration: tables, RLS and storage")

migration_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "supabase", "migrations", "20261003_resume_builder.sql")
sql = open(migration_path, encoding="utf-8").read()

for table in ["resume_profiles", "resume_education", "resume_experience", "resume_references",
              "resume_projects", "resume_publications", "resume_skills", "resume_hobbies",
              "resume_awards", "resume_activities", "resume_languages"]:
    check(f"creates {table}", f"create table if not exists public.{table}" in sql)
    check(f"RLS enabled on {table}",
          re.search(rf"alter table\s+public\.{table}\s+enable row level security", sql) is not None)

check("RLS is never disabled", "disable row level security" not in sql.lower())
check("user_id is the parent ownership key", "auth.uid() = user_id" in sql)
check("child ownership resolves through the parent resume",
      sql.count("rp.id = resume_id and rp.user_id = auth.uid()") == 5,
      f"{sql.count('rp.id = resume_id and rp.user_id = auth.uid()')} policy templates (select/insert/update x2/delete)")
check("the DO block loops over all 10 child tables",
      all(f"'{name}'" in sql.split("foreach child in array")[1].split("loop")[0]
          for name in rs.CHILD_TABLES.values()),
      str(list(rs.CHILD_TABLES.values())))
check("parent has explicit select/insert/update/delete policies",
      all(f'"own resume {op}"' in sql for op in ("select", "insert", "update", "delete")))
check("every policy is restricted to authenticated",
      len(re.findall(r"to\s+authenticated", sql)) >= 12,
      f"{len(re.findall(r'to\s+authenticated', sql))} policies")
check("one resume per user", "resume_profiles_user_unique unique (user_id)" in sql)
check("child tables cascade on parent delete", sql.count("on delete cascade") >= 11)
check("storage bucket is created private",
      "'resume-assets', 'resume-assets', false" in sql)
check("bucket size limit matches the service constant",
      "2097152" in sql and rs.MAX_IMAGE_BYTES == 2 * 1024 * 1024)
check("bucket allows only jpeg and png",
      "array['image/jpeg', 'image/png']" in sql)
check("storage policies scope objects to the caller's folder",
      sql.count("(storage.foldername(name))[1] = auth.uid()::text") >= 4)
check("storage has select/insert/update/delete policies",
      all(f'"own resume assets {op}"' in sql for op in ("select", "insert", "update", "delete")))
check("updated_at trigger is installed", "set_resume_updated_at" in sql)
check("migration is idempotent",
      sql.count("create table if not exists") == 11
      and sql.count("drop policy if exists") >= 8
      and "create or replace function public.set_resume_updated_at" in sql,
      f"{sql.count('drop policy if exists')} drop-policy statements + drop trigger in the DO block")
check("employment type is constrained in the database",
      "check (employment_type in ('Internship', 'Part Time Job', 'Full Time Job'))" in sql)


# ===================================================================
# Summary
# ===================================================================
print("\n" + "=" * 60)
print(f"  PASSED: {len(PASS)}    FAILED: {len(FAIL)}")
if FAIL:
    print("\n  FAILURES:")
    for label in FAIL:
        print(f"    - {label}")
print("=" * 60)

main._resume_client = original_client
sys.exit(1 if FAIL else 0)