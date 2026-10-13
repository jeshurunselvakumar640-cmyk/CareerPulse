"""
CareerPulse Flet Frontend Configuration & Design Tokens.
"""
import os

# Base URL for the CareerPulse FastAPI Backend.
# Defaults to localhost for local execution and can be pointed to the Vercel deployment URL
# (e.g. https://career-pulse.vercel.app) or configured via environment variable.
API_BASE_URL = os.getenv("CAREERPULSE_API_URL", "http://127.0.0.1:8000").rstrip("/")

# Branding
APP_NAME = "CareerPulse"
APP_TAGLINE = "AI-Powered Career Opportunity Agent"
APP_VERSION = "v2.0 (Flet Edition)"

# Color Palette — Sleek Modern SaaS Dark Theme
BG_DARK = "#030712"          # Slate 950 / Deep Charcoal
BG_SIDEBAR = "#080e1a"       # Subtle darker contrast for sidebar
SURFACE_CARD = "#0f172a"     # Slate 900
SURFACE_ELEVATED = "#1e293b" # Slate 800
SURFACE_BORDER = "#1e293b"   # Slate 800 border
SURFACE_BORDER_LIGHT = "#334155" # Slate 700 border hover

# Text Colors
TEXT_PRIMARY = "#f8fafc"     # Slate 50
TEXT_SECONDARY = "#94a3b8"   # Slate 400
TEXT_MUTED = "#64748b"       # Slate 500

# Accent & Brand Colors
ACCENT_INDIGO = "#6366f1"    # Indigo 500
ACCENT_INDIGO_LIGHT = "#818cf8"
ACCENT_VIOLET = "#8b5cf6"    # Violet 500
ACCENT_CYAN = "#06b6d4"      # Cyan 500
ACCENT_GREEN = "#10b981"     # Emerald 500
ACCENT_AMBER = "#f59e0b"     # Amber 500
ACCENT_RED = "#ef4444"       # Rose/Red 500
ACCENT_BRONZE = "#d97706"    # Bronze / Deep Amber
ACCENT_SILVER = "#94a3b8"    # Silver / Slate

# Opportunity Tier Colors
TIER_COLORS = {
    "Gold": {"bg": "#78350f", "border": "#f59e0b", "text": "#fbbf24", "icon": "STAR"},
    "Silver": {"bg": "#1e293b", "border": "#94a3b8", "text": "#cbd5e1", "icon": "WORKSPACE_PREMIUM"},
    "Bronze": {"bg": "#451a03", "border": "#b45309", "text": "#f59e0b", "icon": "MILITARY_TECH"},
}
