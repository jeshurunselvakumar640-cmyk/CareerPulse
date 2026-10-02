"""Confirm whether ANY DDL-capable endpoint exists on the live project."""
import httpx

from app.config import settings

url = settings.supabase_url
svc = settings.supabase_service_role_key
admin = {"apikey": svc, "Authorization": f"Bearer {svc}"}

# 1. exec_sql variants
for fn in ["exec_sql", "execsql", "run_sql", "runsql", "migrate", "migrations",
           "apply_migration", "apply_migrations", "pg_execute", "pg_exec"]:
    r = httpx.post(f"{url}/rest/v1/rpc/{fn}", headers=admin,
                     json={"query": "select 1"}, timeout=20)
    ok = r.status_code == 200
    print(f"  rpc/{fn:<16} -> {r.status_code}{'  AVAILABLE' if ok else ''}")

# 2. Management API (requires a PAT, which we don't have).
r = httpx.get(f"{url}/management/v1/projects", timeout=20)
print(f"  management API   -> {r.status_code} {'AVAILABLE' if r.status_code==200 else ''}")

# 3. pg_dump / pg_rest_ddl style endpoints.
for path in ["/pg-rest-ddl", "/api/ddl", "/rest/v1/ddl"]:
    r = httpx.get(f"{url}{path}", headers=admin, timeout=20)
    print(f"  {path:<20} -> {r.status_code}")

# 4. Confirm the schema cache really has no DDL function.
spec = httpx.get(f"{url}/rest/v1/", headers=admin, timeout=60).json()
fns = [n for n in spec.get("definitions", {}) if "exec" in n.lower() or "sql" in n.lower()]
print(f"\n  DDL-capable functions in schema cache: {fns or 'NONE'}")