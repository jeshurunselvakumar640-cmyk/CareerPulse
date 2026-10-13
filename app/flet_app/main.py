"""
CareerPulse Flet Frontend Entrypoint.
Provides a modern Python GUI for both Web (via FastAPI ASGI) and Desktop (via native Flet window).
"""
import sys
import logging
from typing import Dict, Any, Optional
import flet as ft

from app.flet_app.config import (
    APP_NAME, APP_TAGLINE, BG_DARK, BG_SIDEBAR,
    SURFACE_CARD, SURFACE_BORDER, SURFACE_ELEVATED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_GREEN, ACCENT_CYAN, ACCENT_AMBER
)
from app.flet_app.state import state
from app.flet_app.api_client import api_client
from app.flet_app.components.header import build_header
from app.flet_app.components.sidebar import build_sidebar
from app.flet_app.components.toast import show_toast
from app.flet_app.components.outreach_modal import open_outreach_modal

from app.flet_app.views.landing_view import build_landing_view
from app.flet_app.views.dashboard_view import build_dashboard_view
from app.flet_app.views.jobs_view import build_jobs_view
from app.flet_app.views.news_view import build_news_view
from app.flet_app.views.saved_view import build_saved_view
from app.flet_app.views.tracker_view import build_tracker_view
from app.flet_app.views.profile_view import build_profile_view
from app.flet_app.views.resume_view import build_resume_view

logger = logging.getLogger("careerpulse.flet_main")


async def flet_main(page: ft.Page):
    """Main Flet application runner for each user session."""
    
    # 1. Page Configuration
    page.title = f"{APP_NAME} — {APP_TAGLINE}"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.bgcolor = BG_DARK
    page.window_width = 1280
    page.window_height = 860
    page.window_min_width = 800
    page.window_min_height = 600

    # 2. Main Content Slot
    content_area = ft.Container(
        content=ft.ProgressRing(color=ACCENT_INDIGO),
        expand=True,
        alignment=ft.alignment.center,
    )

    sidebar_slot = ft.Container(content=ft.Container(), expand=False)
    header_slot = ft.Container(content=ft.Container(), expand=False)

    # 3. View Switcher Logic
    def render_current_view():
        v = state.current_view
        
        if v == "landing":
            content_area.content = build_landing_view(
                page,
                on_start_click=lambda: navigate_to("dashboard"),
                on_login_click=open_login_dialog,
            )
            content_area.alignment = None
        elif v == "dashboard":
            content_area.content = build_dashboard_view(
                page,
                on_save_opportunity=handle_save_opportunity,
                on_remove_opportunity=handle_remove_opportunity,
                on_outreach=lambda opp: open_outreach_modal(page, opp),
                on_refresh=refresh_opportunities,
            )
            content_area.alignment = None
        elif v == "jobs":
            content_area.content = build_jobs_view(
                page,
                on_save_opportunity=handle_save_opportunity,
                on_remove_opportunity=handle_remove_opportunity,
                on_outreach=lambda opp: open_outreach_modal(page, opp),
                on_refresh=refresh_opportunities,
            )
            content_area.alignment = None
        elif v == "news":
            content_area.content = build_news_view(
                page,
                on_refresh_news=refresh_news,
            )
            content_area.alignment = None
        elif v == "saved":
            content_area.content = build_saved_view(
                page,
                on_remove_opportunity=handle_remove_opportunity,
                on_outreach=lambda opp: open_outreach_modal(page, opp),
                on_refresh_saved=refresh_saved_jobs,
            )
            content_area.alignment = None
        elif v == "tracker":
            content_area.content = build_tracker_view(
                page,
                on_sync_replies=refresh_applications,
            )
            content_area.alignment = None
        elif v == "profile":
            content_area.content = build_profile_view(
                page,
                on_profile_saved=refresh_all_data,
            )
            content_area.alignment = None
        elif v == "resume":
            content_area.content = build_resume_view(
                page,
                on_refresh_resume=refresh_resume,
            )
            content_area.alignment = None

        # Update sidebar active state
        sidebar_slot.content = build_sidebar(
            current_view=state.current_view,
            on_navigate=navigate_to,
            saved_count=len(state.saved_opportunities),
            apps_count=len(state.applications),
        )

        # Update header
        header_slot.content = build_header(
            page,
            on_login_click=open_login_dialog,
            on_logout_click=handle_logout,
            on_refresh_click=refresh_all_data,
        )

        page.update()

    def navigate_to(view_name: str):
        state.current_view = view_name
        render_current_view()

    # 4. Action Handlers
    async def handle_save_opportunity(opp: Dict[str, Any]):
        try:
            res = await api_client.save_opportunity(opp, status="interested")
            if res.get("status") == "success":
                state.saved_opportunities.append(opp)
                show_toast(page, f"Saved: {opp.get('title')} at {opp.get('company')} to Supabase!")
                render_current_view()
            else:
                show_toast(page, "Could not save opportunity.", is_error=True)
        except Exception as e:
            show_toast(page, f"Error saving opportunity: {e}", is_error=True)

    async def handle_remove_opportunity(opp: Dict[str, Any]):
        try:
            company = opp.get("company", "")
            title = opp.get("title", "")
            res = await api_client.remove_saved_opportunity(company, title)
            if res.get("status") == "success":
                k = f"{company}_{title}".lower().replace(" ", "_")
                state.saved_opportunities = [
                    o for o in state.saved_opportunities
                    if f"{o.get('company','')}_{o.get('title','')}".lower().replace(" ", "_") != k
                ]
                show_toast(page, "Opportunity removed from saved list.")
                render_current_view()
            else:
                show_toast(page, "Could not remove opportunity.", is_error=True)
        except Exception as e:
            show_toast(page, f"Error removing opportunity: {e}", is_error=True)

    def refresh_opportunities():
        page.run_task(load_opportunities)

    def refresh_news(force: bool = False):
        page.run_task(load_news, force)

    def refresh_saved_jobs():
        page.run_task(load_saved_jobs)

    def refresh_applications():
        page.run_task(load_applications)

    def refresh_resume():
        page.run_task(load_resume)

    def refresh_all_data():
        page.run_task(init_data)

    def handle_logout():
        state.token = None
        state.authenticated = False
        state.user_profile = {
            "name": "",
            "email": "",
            "headline": "",
            "degree": "",
            "college": "",
            "year": "",
            "location": "",
            "experience": "",
            "experience_level": "Entry-Level",
            "skills": [],
            "interests": [],
            "preferred_roles": [],
            "preferred_domains": [],
            "github": "",
            "linkedin": "",
            "projects": [],
        }
        api_client.set_token(None)
        show_toast(page, "Logged out successfully.", is_info=True)
        navigate_to("landing")

    def open_login_dialog():
        """Presents authentication dialog for LinkedIn and Google OAuth or Desktop token."""
        base_url = api_client.base_url
        linkedin_url = f"{base_url}/api/auth/linkedin"
        google_url = f"{base_url}/api/auth/google"

        token_input = ft.TextField(
            label="Supabase Access Token (Optional for Desktop / CLI)",
            hint_text="Paste your session access token if testing outside browser...",
            bgcolor=SURFACE_CARD,
            border_color=SURFACE_BORDER,
            color=TEXT_PRIMARY,
            text_size=12,
        )

        async def apply_token(e):
            t = token_input.value.strip()
            if t:
                state.token = t
                api_client.set_token(t)
                dlg.open = False
                page.update()
                show_toast(page, "Applying authentication token...")
                await init_data()

        dlg = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOGIN, color=ACCENT_INDIGO, size=20),
                    ft.Text("Sign In to CareerPulse", size=17, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ],
                spacing=8,
            ),
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Authenticate using your professional profile to calibrate AI matching "
                        "and synchronize your saved applications across sessions.",
                        size=13,
                        color=TEXT_SECONDARY,
                    ),
                    ft.Container(height=10),
                    ft.FilledButton(
                        text="Sign In with LinkedIn",
                        icon=ft.Icons.LINK,
                        url=linkedin_url,
                        style=ft.ButtonStyle(
                            bgcolor="#0a66c2",
                            color=TEXT_PRIMARY,
                            padding=ft.Padding(left=18, top=12, right=18, bottom=12),
                            shape=ft.RoundedRectangleBorder(radius=8),
                        ),
                    ),
                    ft.Container(height=6),
                    ft.OutlinedButton(
                        text="Sign In with Google",
                        icon=ft.Icons.ACCOUNT_CIRCLE,
                        url=google_url,
                        style=ft.ButtonStyle(
                            color=TEXT_PRIMARY,
                            side=ft.BorderSide(1, SURFACE_BORDER),
                            padding=ft.Padding(left=18, top=12, right=18, bottom=12),
                            shape=ft.RoundedRectangleBorder(radius=8),
                        ),
                    ),
                    ft.Container(height=14),
                    ft.Text("Desktop Direct Connection", size=12, weight=ft.FontWeight.BOLD, color=TEXT_MUTED),
                    token_input,
                ],
                tight=True,
                spacing=4,
            ),
            actions=[
                ft.TextButton("Close", on_click=lambda _: setattr(dlg, "open", False) or page.update()),
                ft.FilledButton("Set Token", on_click=apply_token, style=ft.ButtonStyle(bgcolor=ACCENT_INDIGO)),
            ],
            bgcolor=SURFACE_ELEVATED,
            open=True,
        )
        page.overlay.append(dlg)
        page.update()

    # 5. Async Data Fetchers
    async def load_saved_jobs():
        res = await api_client.get_saved_opportunities()
        if res.get("status") == "success":
            state.saved_opportunities = res.get("saved", [])
            state.waitlisted_opportunities = res.get("waitlisted", [])
            render_current_view()

    async def load_opportunities():
        res = await api_client.get_insights_and_opportunities(count=30)
        if res.get("status") == "success" or res.get("opportunities"):
            state.opportunities = res.get("opportunities", [])
            state.insights = res.get("insights", state.insights)
            if res.get("daily_news") and not state.news_articles:
                state.news_articles = res.get("daily_news", [])
            render_current_view()

    async def load_news(refresh: bool = False):
        res = await api_client.get_personalized_news(limit=10, refresh=refresh)
        if res.get("status") == "success" or res.get("articles"):
            state.news_articles = res.get("articles", [])
            render_current_view()

    async def load_applications():
        res = await api_client.get_applications()
        if res.get("status") == "success":
            state.applications = res.get("applications", [])
        status_res = await api_client.get_mailbox_status()
        state.mailbox_status = status_res
        render_current_view()

    async def load_resume():
        res = await api_client.get_resume()
        if res.get("status") == "success":
            state.resume_data = res
        render_current_view()

    async def init_data():
        # Fetch profile
        prof_res = await api_client.get_profile()
        if prof_res.get("authenticated"):
            state.authenticated = True
            state.user_profile = prof_res.get("profile", state.user_profile)

        # Concurrently start loading other sections
        await load_saved_jobs()
        await load_opportunities()
        await load_news()
        await load_applications()
        await load_resume()
        render_current_view()

    # 6. Assemble Main Layout Shell
    main_layout = ft.Column(
        controls=[
            header_slot,
            ft.Row(
                controls=[
                    sidebar_slot,
                    content_area,
                ],
                expand=True,
                spacing=0,
            ),
        ],
        expand=True,
        spacing=0,
    )

    page.add(main_layout)

    # Initialize view and fetch backend data
    render_current_view()
    await init_data()


def run_desktop():
    """Entry point for future Windows desktop application distribution."""
    ft.run(flet_main, view=ft.AppView.FLET_APP)


def run_web():
    """Entry point for standalone web browser server."""
    ft.run(flet_main, view=ft.AppView.WEB_BROWSER, port=8550)


if __name__ == "__main__":
    # If run directly with python, defaults to native desktop window (or web if specified)
    if "--web" in sys.argv:
        run_web()
    else:
        run_desktop()
