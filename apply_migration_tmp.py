"""Apply the corrective migration to live Supabase via the pgREST exec_sql RPC.

The project has no psql/docker/supabase-cli, so the migration is applied by
POSTing the raw SQL to /rest/v1/rpc/exec_sql with the service-role key.
"""
import httpx

from app.config import settings

url = settings.supabase_url
svc = settings.supabase_service_role_key
admin = {"apikey": svc, "Authorization": f"Bearer {svc}"}

sql = open("supabase/migrations/20261004_resume_builder_schema_sync.sql").read()

r = httpx.post(f"{url}/rest/v1/rpc/exec_sql", headers=admin,
               json={"query": sql}, timeout=120)
print("exec_sql ->", r.status_code)
print(r.text[:1500])
if r.status_code != 200:
    raise SystemExit(1)
print("MIGRATION APPLIED")