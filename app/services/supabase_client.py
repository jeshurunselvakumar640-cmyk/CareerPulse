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
