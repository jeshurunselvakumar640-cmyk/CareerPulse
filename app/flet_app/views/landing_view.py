"""
CareerPulse Public Landing Page View.
"""
from typing import Callable
import flet as ft

from app.flet_app.config import (
    APP_NAME, APP_TAGLINE, SURFACE_CARD, SURFACE_BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_VIOLET, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER
)

def build_landing_view(page: ft.Page, on_start_click: Callable[[], None], on_login_click: Callable[[], None]) -> ft.Container:
    """Builds a modern SaaS landing page showcasing platform capabilities."""

    # Hero Section
    badge = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.AUTO_AWESOME, size=14, color=ACCENT_CYAN),
                ft.Text("Next-Gen Career Intelligence Platform", size=12, weight=ft.FontWeight.W_600, color=ACCENT_CYAN),
            ],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding(left=12, top=6, right=12, bottom=6),
        border_radius=20,
        bgcolor=f"{ACCENT_CYAN}18",
        border=ft.Border.all(1, f"{ACCENT_CYAN}40"),
    )

    hero_title = ft.Text(
        "Never Miss Your Dream Career Opportunity",
        size=36,
        weight=ft.FontWeight.BOLD,
        color=TEXT_PRIMARY,
        text_align=ft.TextAlign.CENTER,
    )

    hero_subtitle = ft.Text(
        "CareerPulse automatically uncovers verified jobs and internships, analyzes openings with Gemini AI, "
        "identifies your matching skills, and delivers personalized opportunities directly to your inbox.",
        size=15,
        color=TEXT_SECONDARY,
        text_align=ft.TextAlign.CENTER,
        max_lines=3,
    )

    hero_cta = ft.Row(
        controls=[
            ft.FilledButton(
                text="Open Career Dashboard",
                icon=ft.Icons.ROCKET_LAUNCH,
                on_click=lambda _: on_start_click(),
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_INDIGO,
                    color=TEXT_PRIMARY,
                    padding=ft.Padding(left=22, top=14, right=22, bottom=14),
                    shape=ft.RoundedRectangleBorder(radius=10),
                ),
            ),
            ft.OutlinedButton(
                text="Sign In with LinkedIn / Google",
                icon=ft.Icons.LOGIN,
                on_click=lambda _: on_login_click(),
                style=ft.ButtonStyle(
                    color=TEXT_PRIMARY,
                    side=ft.BorderSide(1, SURFACE_BORDER),
                    padding=ft.Padding(left=18, top=14, right=18, bottom=14),
                    shape=ft.RoundedRectangleBorder(radius=10),
                ),
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=14,
    )

    hero_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row([badge], alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(height=10),
                hero_title,
                ft.Container(height=6),
                hero_subtitle,
                ft.Container(height=20),
                hero_cta,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        padding=ft.Padding(top=30, bottom=30, left=20, right=20),
    )

    # Feature Grid
    features = [
        {
            "icon": ft.Icons.TRAVEL_EXPLORE,
            "color": ACCENT_CYAN,
            "title": "Live Opportunity Discovery",
            "desc": "Continuous Tavily web search targeting active ATS portals (Greenhouse, Lever, Ashby) to find direct job listings without aggregator spam."
        },
        {
            "icon": ft.Icons.PSYCHOLOGY,
            "color": ACCENT_INDIGO,
            "title": "Gemini AI Skill Matching",
            "desc": "Deep multi-layer parsing compares job requirements with your real projects, skills, and coursework to generate Gold, Silver, and Bronze match tiers."
        },
        {
            "icon": ft.Icons.MARK_EMAIL_READ,
            "color": ACCENT_GREEN,
            "title": "Automated 24h Email Digest",
            "desc": "Every 24 hours, scheduled Vercel cron jobs deliver top matched openings, upcoming application deadlines, and major tech news to your inbox."
        },
        {
            "icon": ft.Icons.AUTO_FIX_HIGH,
            "color": ACCENT_AMBER,
            "title": "One-Click AI Cold Outreach",
            "desc": "Instantly generates professional, personalized cold application emails tailored to hiring managers using Gemini AI with direct SMTP sending."
        },
        {
            "icon": ft.Icons.TRACK_CHANGES,
            "color": ACCENT_VIOLET,
            "title": "Mailbox Reply Tracker",
            "desc": "Read-only Gmail OAuth integration scans incoming company replies, classifies status (Interview, Offer, Rejection), and drafts instant responses."
        },
        {
            "icon": ft.Icons.DESCRIPTION,
            "color": ACCENT_CYAN,
            "title": "Interactive Resume Builder",
            "desc": "A dedicated resume workspace storing your profile, education, experience, and projects directly in Supabase for fast export and tailored applications."
        },
    ]

    feature_cards = []
    for f in features:
        card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Icon(f["icon"], size=22, color=f["color"]),
                        padding=10,
                        border_radius=8,
                        bgcolor=f"{f['color']}18",
                    ),
                    ft.Container(height=8),
                    ft.Text(f["title"], size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ft.Container(height=4),
                    ft.Text(f["desc"], size=13, color=TEXT_SECONDARY),
                ],
                spacing=4,
            ),
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, SURFACE_BORDER),
            border_radius=12,
            padding=20,
            width=360,
        )
        feature_cards.append(card)

    features_section = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Why Job Seekers Rely on CareerPulse", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Text("Engineered to replace hours of manual job board searching with automated intelligence.", size=13, color=TEXT_SECONDARY),
                ft.Container(height=16),
                ft.Row(
                    controls=feature_cards,
                    wrap=True,
                    spacing=16,
                    run_spacing=16,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        padding=ft.Padding(top=20, bottom=40, left=10, right=10),
    )

    # Technology Stack Footer
    tech_pills = ["Python FastAPI", "Flet GUI (1.0)", "Google Gemini 3.5", "Tavily Web Search", "Supabase PostgreSQL", "Gmail OAuth & SMTP", "Vercel Cron"]
    tech_chips = [
        ft.Container(
            content=ft.Text(t, size=11, color=TEXT_MUTED, weight=ft.FontWeight.W_500),
            padding=ft.Padding(left=10, top=4, right=10, bottom=4),
            border_radius=6,
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, SURFACE_BORDER),
        )
        for t in tech_pills
    ]

    stack_section = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("POWERED BY MODERN TECHNOLOGY", size=11, weight=ft.FontWeight.BOLD, color=TEXT_MUTED),
                ft.Container(height=8),
                ft.Row(controls=tech_chips, alignment=ft.MainAxisAlignment.CENTER, wrap=True, spacing=8),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding(top=10, bottom=30, left=10, right=10),
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                hero_container,
                features_section,
                stack_section,
            ],
            spacing=20,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )
