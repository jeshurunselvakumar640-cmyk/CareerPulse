# app/config.py
import os
from dotenv import load_dotenv

# Load environment variables from .env
try:
    load_dotenv()
except Exception:
    pass


class Settings:
    def __init__(self):
        try:
            # API Keys & Credentials
            self.tavily_api_key = os.getenv("TAVILY_API_KEY", "")
            self.supabase_url = os.getenv("SUPABASE_URL", "")
            self.supabase_key = os.getenv("SUPABASE_KEY", "")
            self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")

            # LinkedIn OAuth Configuration
            self.linkedin_client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
            self.linkedin_client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")
            self.linkedin_redirect_uri = os.getenv(
                "LINKEDIN_REDIRECT_URI",
                "http://127.0.0.1:8000/api/auth/linkedin/callback",
            )

            # SMTP / Email Configuration
            self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
            self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
            self.smtp_user = os.getenv("SMTP_USER", "")
            self.smtp_password = os.getenv("SMTP_PASSWORD", "")
            self.recipient_email = os.getenv("RECIPIENT_EMAIL", "")

            # Gmail OAuth Configuration (mailbox READ access for company replies).
            # Sending stays on SMTP above; this is only used to READ replies.
            # Values are stripped: an environment variable pasted with a trailing
            # newline or stray space is rejected by Google as `invalid_client`.
            self.gmail_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
            self.gmail_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
            # Empty means "derive from the incoming request", which keeps local
            # development working without hard-coding a host.
            self.gmail_redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
            # Read-only scopes only: no send, no modify, no mailbox deletion.
            # openid/userinfo.* resolve the account address; gmail.readonly is
            # the mailbox read grant and is what actually authorizes
            # users/me/profile, the endpoint that reports emailAddress.
            self.gmail_scopes = [
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/gmail.readonly",
            ]
            # Fernet key used to encrypt OAuth refresh tokens at rest.
            # When unset, a key is derived from the Supabase key (still
            # encrypted at rest, but rotating the Supabase key would strand
            # existing connections, so an explicit key is preferred).
            self.mailbox_token_encryption_key = os.getenv("MAILBOX_TOKEN_ENCRYPTION_KEY", "")
            # Seconds a user must wait between automatic mailbox syncs.
            self.mailbox_sync_min_interval = int(os.getenv("MAILBOX_SYNC_MIN_INTERVAL", "300"))

            # AI & Search Settings
            self.primary_model = os.getenv("PRIMARY_MODEL", "gemini-3.5-flash-lite")
            self.fallback_model = os.getenv("FALLBACK_MODEL", "gemini-3.1-flash-lite")
            self.search_topic = os.getenv("SEARCH_TOPIC", "general")
            self.search_depth = os.getenv("SEARCH_DEPTH", "advanced")
            self.max_search_queries = int(os.getenv("MAX_SEARCH_QUERIES", "5"))

        except Exception:
            # Safe fallbacks if parsing fails
            self.tavily_api_key = ""
            self.supabase_url = ""
            self.supabase_key = ""
            self.gemini_api_key = ""
            self.linkedin_client_id = ""
            self.linkedin_client_secret = ""
            self.linkedin_redirect_uri = "http://127.0.0.1:8000/api/auth/linkedin/callback"
            self.smtp_host = "smtp.gmail.com"
            self.smtp_port = 587
            self.smtp_user = ""
            self.smtp_password = ""
            self.recipient_email = ""
            self.gmail_client_id = ""
            self.gmail_client_secret = ""
            self.gmail_redirect_uri = ""
            self.gmail_scopes = [
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/gmail.readonly",
            ]
            self.mailbox_token_encryption_key = ""
            self.mailbox_sync_min_interval = 300
            self.primary_model = "gemini-3.5-flash-lite"
            self.fallback_model = "gemini-3.1-flash-lite"
            self.search_topic = "general"
            self.search_depth = "advanced"
            self.max_search_queries = 5


settings = Settings()