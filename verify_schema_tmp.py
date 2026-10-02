"""Verify the live Resume Builder schema after the corrective migration."""
import json

import httpx

from app.config import settings

url = settings.supabase_url
svc = settings.supabase_service_role_key
admin = {"apikey": svc, "Authorization": f"Bearer {svc}"}

spec = httpx.get(f"{url}/rest/v1/", headers=admin, timeout=60).json()
schemas = spec.get("definitions", {})

fails = []
def chk(cond, label, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        fails.append(label)

print("=" * 72)
print("A. resume_profiles nullability")
print("=" * 72)
p = schemas["resume_profiles"]["properties"]
req = schemas["resume_profiles"].get("required", [])
for col in ["name", "email", "headline"]:
    chk(col not in req, f"{col} is nullable", f"in required={col in req}")
chk(p["name"]["type"] == "string", "name is still text")
chk(p["email"]["type"] == "string", "email is still text")
chk(p["headline"]["type"] == "string", "headline is still text")
chk("user_id" in req, "user_id still NOT NULL (ownership anchor)")
chk("id" in req and "total_experience_months" in req, "id/total_experience_months still NOT NULL")

print()
print("=" * 72)
print("B. Child tables — sort_order present and NOT NULL")
print("=" * 72)
children = ["resume_education", "resume_experience", "resume_references",
            "resume_projects", "resume_skills", "resume_hobbies",
            "resume_awards", "resume_activities", "resume_publications",
            "resume_languages"]
for name in children:
    props = schemas[name]["properties"]
    req = schemas[name].get("required", [])
    so = props.get("sort_order")
    chk(so is not None, f"{name} has sort_order", "MISSING" if so is None else so.get("type"))
    if so:
        chk(so.get("type") == "integer", f"{name}.sort_order is integer", so.get("type"))
        chk("sort_order" in req, f"{name}.sort_order is NOT NULL", "nullable" if "sort_order" not in req else "ok")

print()
print("=" * 72)
print("C. Value-list tables — shared value column, old per-table columns gone")
print("=" * 72)
value_tables = {
    "resume_skills": "skill",
    "resume_hobbies": "hobby",
    "resume_awards": "award",
    "resume_activities": "activity",
    "resume_languages": "language",
}
for name, old_col in value_tables.items():
    props = schemas[name]["properties"]
    req = schemas[name].get("required", [])
    v = props.get("value")
    chk(v is not None, f"{name}.value exists", "MISSING" if v is None else v.get("type"))
    if v:
        chk(v.get("type") == "string", f"{name}.value is text", v.get("type"))
        chk("value" in req, f"{name}.value is NOT NULL", "nullable" if "value" not in req else "ok")
    chk(old_col not in props, f"{name}.{old_col} removed", "STILL PRESENT" if old_col in props else "gone")

print()
print("=" * 72)
print("D. Storage bucket")
print("=" * 72)
r = httpx.get(f"{url}/storage/v1/bucket/resume-assets", headers=admin, timeout=30)
b = r.json()
chk(b.get("public") is False, "bucket is private", str(b.get("public")))
chk(b.get("file_size_limit") == 2097152, f"2 MiB limit ({b.get('file_size_limit')})", str(b.get("file_size_limit")))
mimes = b.get("allowed_mime_types") or []
chk(sorted(mimes) == sorted(["image/jpeg", "image/png"]), f"JPEG/PNG only ({mimes})", str(mimes))

print()
print("=" * 72)
print("E. RLS still enabled (probe: anon sees 0 rows on every table)")
print("=" * 72)
anon = {"apikey": settings.supabase_key, "Authorization": f"Bearer {settings.supabase_key}"}
for t in children + ["resume_profiles"]:
    r = httpx.get(f"{url}/rest/v1/{t}", headers=anon, params={"select": "*"}, timeout=25)
    rows = len(r.json()) if r.status_code == 200 and r.text.strip().startswith("[") else None
    chk(rows == 0, f"anon SELECT {t} -> 0 rows visible", str(rows))
    if t in ("resume_education", "resume_skills"):
        w = httpx.post(f"{url}/rest/v1/{t}", headers=anon,
                         json={"resume_id": "00000000-0000-0000-0000-000000000000"}, timeout=25)
        chk(w.status_code in (401, 403) or "row-level security" in w.text,
            f"anon INSERT {t} refused", f"{w.status_code} {w.text[:60]}")

print()
print("=" * 72)
print("F. Row counts (service role)")
print("=" * 72)
for name in children + ["resume_profiles"]:
    r = httpx.get(f"{url}/rest/v1/{name}", headers={**admin, "Prefer": "count=exact"},
                    params={"select": "id"}, timeout=30)
    rng = r.headers.get("content-range", "")
    count = rng.split("/")[-1] if "/" in rng else "?"
    print(f"  {name:<24} {count} rows")

print()
print("RESULT: " + ("ALL SCHEMA CHECKS PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}"))