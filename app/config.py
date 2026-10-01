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
            self.primary_model = "gemini-3.5-flash-lite"
            self.fallback_model = "gemini-3.1-flash-lite"
            self.search_topic = "general"
            self.search_depth = "advanced"
            self.max_search_queries = 5


settings = Settings()