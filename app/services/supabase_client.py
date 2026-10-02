from supabase import create_client, Client
from app.config import settings

def get_supabase_client() -> Client:
    """
    Safely initialize and return a Supabase client instance.
    Fails safely if credentials are missing or invalid without exposing secrets.
    """
    url = getattr(settings, "supabase_url", "")
    key = getattr(settings, "supabase_key", "")

    if not url or not key:
        raise ValueError("Supabase credentials missing")

    try:
        return create_client(url, key)
    except Exception:
        raise RuntimeError("Failed to connect to Supabase service")


def get_service_role_client():
    """
    Return a service-role client for server-side writes to protected tables.

    Row Level Security on tables such as `mailbox_connections` rejects writes
    made with the anon key, so the OAuth callback cannot store a connection
    without a privileged key. This client is used ONLY inside request handlers
    and is never returned to the browser. Returns None when no service-role key
    is configured, so callers fall back to the normal client.
    """
    url = getattr(settings, "supabase_url", "")
    key = getattr(settings, "supabase_service_role_key", "")

    if not url or not key:
        return None

    try:
        return create_client(url, key)
    except Exception:
        return None
