"""Discover available RPC functions on the live Supabase project."""
import httpx

from app.config import settings

url = settings.supabase_url
svc = settings.supabase_service_role_key
admin = {"apikey": svc, "Authorization": f"Bearer {svc}"}

# List every function PostgREST exposes.
r = httpx.get(f"{url}/rest/v1/", headers=admin, timeout=60)
spec = r.json()
schemas = spec.get("definitions", {})
print("functions exposed via PostgREST:")
for name in sorted(schemas):
    if name.startswith("exec_") or name.startswith("migrate") or "sql" in name.lower():
        print(f"  {name}")
print()
print("total schemas:", len(schemas))
print("first 30:", sorted(schemas)[:30])