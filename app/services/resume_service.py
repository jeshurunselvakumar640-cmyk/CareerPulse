"""
CareerPulse — Resume Builder data access.

Scope of this module:

* owning the Supabase table names and column shapes used by the Resume Builder;
* the pure, dependency-free total-experience calculation (overlap-merging),
  which is the piece most worth testing in isolation;
* image validation and the user-scoped storage path helpers;
* small read/write helpers that always operate through a client carrying the
  caller's JWT, so the Row Level Security policies created in
  supabase/migrations/20261003_resume_builder.sql are the actual enforcement
  boundary rather than something this module works around.

Nothing here touches the existing profile, news, opportunity, Gmail or OAuth
code paths. Resume data is stored in its own tables and is deliberately
independent of `user.user_metadata`, which the Candidate Profile view owns.
"""

import base64
import binascii
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger("careerpulse.resume")


# -------------------------------------------------------------------
# Table / column names
# -------------------------------------------------------------------

RESUME_TABLE = "resume_profiles"
ASSET_BUCKET = "resume-assets"

#: Child tables keyed by the resume section they back. The order is the order
#: the dashboard loads and renders them in.
CHILD_TABLES: Dict[str, str] = {
    "education": "resume_education",
    "experience": "resume_experience",
    "references": "resume_references",
    "projects": "resume_projects",
    "publications": "resume_publications",
    "skills": "resume_skills",
    "hobbies": "resume_hobbies",
    "awards": "resume_awards",
    "activities": "resume_activities",
    "languages": "resume_languages",
}

#: Sections stored as a list of single values. All of them share the same
#: `value` column, so one code path handles skills/hobbies/awards/activities/
#: languages.
VALUE_LIST_SECTIONS = ("skills", "hobbies", "awards", "activities", "languages")

EMPLOYMENT_TYPES = ("Internship", "Part Time Job", "Full Time Job")

#: Hard ceiling for an uploaded image. Matches the bucket's file_size_limit in
#: the migration, so a file this module accepts is a file the bucket accepts.
MAX_IMAGE_BYTES = 2 * 1024 * 1024

_ALLOWED_IMAGE_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
}


class ResumeValidationError(ValueError):
    """Raised for input the caller can correct; surfaced as HTTP 400."""


# -------------------------------------------------------------------
# Total experience calculation (pure functions)
# -------------------------------------------------------------------

def _to_date(value: Any) -> Optional[date]:
    """Parse a date from the shapes PostgREST and the API layer can hand us.

    Returns None when the value is absent or unparseable, so a malformed row
    degrades to "no contribution" instead of crashing the whole calculation.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # Bare year-month, e.g. "2025-01".
    if re.fullmatch(r"\d{4}-\d{2}", text):
        text = f"{text}-01"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _months_between(start: date, end: date) -> int:
    """Whole months from start to end, never negative."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def normalize_experience_periods(records: List[Dict[str, Any]]) -> List[Tuple[date, date]]:
    """Turn raw experience rows into a list of valid [start, end] intervals.

    Rules:
      * a row missing a start date contributes nothing;
      * a row marked "currently working here" (or with no end date) runs to
        today;
      * a row whose end date precedes its start date is inverted to a
        zero-length interval at the start date rather than producing a
        negative duration, so bad input can never subtract from the total.
    """
    today = date.today()
    periods: List[Tuple[date, date]] = []

    for record in records or []:
        if not isinstance(record, dict):
            continue
        start = _to_date(record.get("start_date"))
        if start is None:
            continue

        ongoing = bool(record.get("currently_work_here"))
        end = _to_date(record.get("end_date"))

        if ongoing or end is None:
            end = today
        if end < start:
            end = start
        if end > today:
            end = today

        periods.append((start, end))

    return periods


def merge_overlapping_periods(periods: List[Tuple[date, date]]) -> List[Tuple[date, date]]:
    """Merge overlapping/touching intervals so shared months count once.

    Two jobs from Jan 2024-Jun 2024 and Mar 2024-Dec 2024 are one continuous
    span of Jan 2024-Dec 2024, not eleven months of double-counted work.
    """
    if not periods:
        return []

    ordered = sorted(periods, key=lambda p: (p[0], p[1]))
    merged: List[Tuple[date, date]] = [ordered[0]]

    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        # `start <= last_end` also joins back-to-back jobs, which is correct:
        # there is no gap between them.
        if start <= last_end:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))

    return merged


def calculate_total_experience_months(records: List[Dict[str, Any]]) -> int:
    """Total distinct months of experience across all experience records."""
    periods = normalize_experience_periods(records)
    merged = merge_overlapping_periods(periods)
    return sum(_months_between(start, end) for start, end in merged)


def format_experience(months: Optional[int]) -> str:
    """Render stored months the way the UI displays them.

    Follows the documented examples exactly:
      0  -> "0 years 0 months";  8  -> "0 years 8 months"
      14 -> "1 year 2 months";  36 -> "3 years"
      50 -> "4 years 2 months"
    Years are only dropped when they are zero *and* there are no months.
    """
    if not months or months < 0:
        months = 0
    years, remainder = divmod(int(months), 12)
    if years and not remainder:
        return f"{years} year{'s' if years != 1 else ''}"
    return (
        f"{years} year{'s' if years != 1 else ''} "
        f"{remainder} month{'s' if remainder != 1 else ''}"
    )


def is_resume_complete(profile: Optional[Dict[str, Any]]) -> bool:
    """Sections 1 and 2 are mandatory; everything else is optional."""
    if not profile:
        return False
    return bool((profile.get("name") or "").strip()) and \
           bool((profile.get("email") or "").strip()) and \
           bool((profile.get("headline") or "").strip())


# -------------------------------------------------------------------
# Validation helpers
# -------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def require_text(value: Optional[str], field: str, max_length: int = 2000) -> str:
    text = (value or "").strip()
    if not text:
        raise ResumeValidationError(f"{field} is required.")
    if len(text) > max_length:
        raise ResumeValidationError(f"{field} must be {max_length} characters or fewer.")
    return text


def optional_text(value: Optional[str], max_length: int = 2000) -> str:
    text = (value or "").strip()
    return text[:max_length]


def validate_email(value: Optional[str], required: bool = False) -> str:
    text = (value or "").strip()
    if not text:
        if required:
            raise ResumeValidationError("Email is required.")
        return ""
    if not _EMAIL_RE.match(text):
        raise ResumeValidationError("Enter a valid email address.")
    return text


def validate_date_pair(
    start_raw: Optional[str],
    end_raw: Optional[str],
    ongoing: bool,
) -> Tuple[Optional[str], Optional[str]]:
    """Validate a start/end date pair shared by education and experience.

    Returns ISO strings. When `ongoing` is true the end date is dropped, which
    is also what makes the "hide End Date" checkbox meaningful in storage.
    """
    start = _to_date(start_raw)
    if start is None:
        raise ResumeValidationError("Start date is required.")

    if ongoing:
        return start.isoformat(), None

    end = _to_date(end_raw)
    if end is None:
        raise ResumeValidationError("End date is required unless this is ongoing.")
    if end < start:
        raise ResumeValidationError("End date cannot be earlier than the start date.")

    return start.isoformat(), end.isoformat()


def validate_employment_type(value: Optional[str]) -> str:
    text = (value or "").strip()
    if not text:
        raise ResumeValidationError("Employment type is required.")
    if text not in EMPLOYMENT_TYPES:
        raise ResumeValidationError(
            "Employment type must be one of: " + ", ".join(EMPLOYMENT_TYPES)
        )
    return text


# -------------------------------------------------------------------
# Image handling
# -------------------------------------------------------------------

def decode_and_validate_image(data_url: Optional[str]) -> Tuple[bytes, str]:
    """Validate a base64 data URL and return (bytes, extension).

    Checks three independent things, because a client-supplied content type is
    not trustworthy:
      1. the data URL shape and declared MIME type;
      2. the decoded size, against MAX_IMAGE_BYTES;
      3. the actual magic bytes, which is what really decides the format.
    """
    if not data_url:
        raise ResumeValidationError("No image was provided.")

    match = re.match(r"^data:([\w.+-]+/[\w.+-]+);base64,(.+)$", data_url.strip(), re.DOTALL)
    if not match:
        raise ResumeValidationError("Image must be sent as a base64 data URL.")

    declared_mime = match.group(1).lower()
    if declared_mime == "image/jpg":
        # Some browsers still emit this non-standard alias for JPEG.
        declared_mime = "image/jpeg"
    if declared_mime not in _ALLOWED_IMAGE_MIME:
        raise ResumeValidationError("Only JPG, JPEG and PNG images are accepted.")

    payload = match.group(2).strip()
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ResumeValidationError("Image data could not be decoded.")

    if not raw:
        raise ResumeValidationError("Image data is empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ResumeValidationError(
            f"Image must be {MAX_IMAGE_BYTES // (1024 * 1024)}MB or smaller."
        )

    if raw.startswith(b"\xff\xd8\xff"):
        actual = "image/jpeg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        actual = "image/png"
    else:
        raise ResumeValidationError("File is not a valid JPG or PNG image.")

    if declared_mime != actual:
        raise ResumeValidationError(
            "Image content does not match its declared type. Upload a real JPG or PNG."
        )

    return raw, _ALLOWED_IMAGE_MIME[actual]


def build_asset_path(user_id: str, kind: str) -> str:
    """Storage path for a resume image, namespaced by the owning user id.

    The leading folder is the user id, which is exactly what the storage
    policies in the migration match on, so one user can never address another
    user's objects.
    """
    if kind not in ("profile-picture", "signature"):
        raise ResumeValidationError("Unknown resume asset type.")
    safe_uid = re.sub(r"[^0-9a-zA-Z-]", "", str(user_id))
    if not safe_uid:
        raise ResumeValidationError("Invalid user identifier.")
    return f"{safe_uid}/{kind}.png"


def decode_json_list(values: Optional[List[str]], max_items: int = 200) -> List[str]:
    """Normalise a simple item list: trimmed, de-duplicated, order preserved."""
    if not values:
        return []
    seen, out = set(), []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text[:120])
        if len(out) >= max_items:
            break
    return out


# -------------------------------------------------------------------
# Supabase access
# -------------------------------------------------------------------

def _rows(result: Any) -> List[Dict[str, Any]]:
    """PostgREST results are list-like objects; tolerate None and plain lists."""
    data = getattr(result, "data", None)
    if data is None and isinstance(result, list):
        data = result
    return list(data or [])


def get_or_create_resume(
    client,
    user_id: str,
    defaults: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch the caller's single resume row, creating an empty one if needed.

    On first creation the row is seeded from `defaults` (the existing
    CareerPulse profile: full_name/email/avatar_url/linkedin/github/headline
    and skills). This is a one-time copy: once the row exists it is never
    re-seeded, which is what keeps the resume independently editable and stops
    later profile edits from silently overwriting resume data.

    Relies on the `resume_profiles_user_unique` constraint so this is a
    deterministic upsert rather than an insert that could race.
    """
    existing = _rows(
        client.table(RESUME_TABLE).select("*").eq("user_id", user_id).limit(1).execute()
    )
    if existing:
        return existing[0]

    seed: Dict[str, Any] = {"user_id": user_id}
    for column, value in (defaults or {}).items():
        if value in (None, "", [], {}):
            continue
        seed[column] = value

    created = _rows(
        client.table(RESUME_TABLE).upsert(seed, on_conflict="user_id").execute()
    )
    return created[0] if created else None


def load_child_records(client, resume_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Load every child collection for one resume, in a stable order."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for section, table in CHILD_TABLES.items():
        try:
            rows = _rows(
                client.table(table)
                .select("*")
                .eq("resume_id", resume_id)
                .order("sort_order")
                .order("created_at")
                .execute()
            )
        except Exception as exc:
            # A missing table (migration not applied yet) must not take the
            # whole resume page down.
            logger.warning("Resume child table %s unavailable: %s", table, exc)
            rows = []
        out[section] = rows
    return out


def replace_value_list(client, resume_id: str, section: str, values: List[str]) -> None:
    """Rewrite one single-column item list to match `values` exactly."""
    table = CHILD_TABLES[section]
    client.table(table).delete().eq("resume_id", resume_id).execute()
    if values:
        client.table(table).insert(
            [
                {"resume_id": resume_id, "value": value, "sort_order": index}
                for index, value in enumerate(values)
            ]
        ).execute()


def sign_asset_url(client, path: Optional[str], expires_in: int = 3600) -> Optional[str]:
    """Mint a short-lived signed URL for a private resume asset.

    The bucket is private, so the stored path is useless without this. Signed
    URLs keep resume images out of public indexing.
    """
    if not path:
        return None
    try:
        result = client.storage.from_(ASSET_BUCKET).create_signed_url(path, expires_in)
        signed = getattr(result, "signed_url", None)
        if signed:
            return signed
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            return data.get("signedURL") or data.get("signed_url")
    except Exception as exc:
        logger.warning("Could not sign resume asset %s: %s", path, exc)
    return None


def upload_asset(client, user_id: str, kind: str, data_url: str) -> str:
    """Validate and upload a resume image, returning its storage path."""
    raw, extension = decode_and_validate_image(data_url)
    path = build_asset_path(user_id, kind)
    if extension == ".jpg":
        path = path.replace(".png", ".jpg")

    client.storage.from_(ASSET_BUCKET).upload(
        path,
        raw,
        {"content-type": "image/png" if extension == ".png" else "image/jpeg", "upsert": "true"},
    )
    return path


def recompute_total_experience(client, resume_id: str) -> int:
    """Recalculate and persist total_experience_months from saved experience.

    `updated_at` is maintained by the set_resume_updated_at trigger created in
    the migration, so this only has to write the derived value.
    """
    rows = _rows(
        client.table(CHILD_TABLES["experience"])
        .select("*")
        .eq("resume_id", resume_id)
        .execute()
    )
    months = calculate_total_experience_months(rows)
    client.table(RESUME_TABLE).update(
        {"total_experience_months": months}
    ).eq("id", resume_id).execute()
    return months