"""
Profile & Preferences Editor View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any, List
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, SURFACE_ELEVATED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_GREEN, ACCENT_CYAN, ACCENT_AMBER, ACCENT_RED
)
from app.flet_app.state import state
from app.flet_app.api_client import api_client
from app.flet_app.components.toast import show_toast

def build_profile_view(
    page: ft.Page,
    on_profile_saved: Callable[[], None],
) -> ft.Container:
    """Builds the profile and preference configuration page."""

    prof = state.user_profile or {}

    name_field = ft.TextField(
        label="Full Name",
        value=prof.get("name", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    email_field = ft.TextField(
        label="Email Address",
        value=prof.get("email", ""),
        disabled=True,
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_MUTED,
        text_size=13,
        expand=True,
    )

    headline_field = ft.TextField(
        label="Professional Headline / Target Role",
        hint_text="e.g. Aspiring Full Stack Engineer | Python & React Developer",
        value=prof.get("headline", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
    )

    degree_field = ft.TextField(
        label="Degree / Program",
        hint_text="e.g. B.Tech Computer Science",
        value=prof.get("degree", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    college_field = ft.TextField(
        label="College / University",
        value=prof.get("college", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    year_field = ft.TextField(
        label="Graduation Year / Semester",
        hint_text="e.g. 2026",
        value=prof.get("year", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        width=180,
    )

    location_field = ft.TextField(
        label="Preferred Location",
        hint_text="e.g. Remote, Bangalore, New York",
        value=prof.get("location", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    experience_dropdown = ft.Dropdown(
        label="Experience Level",
        value=prof.get("experience_level") or "Entry-Level",
        options=[
            ft.dropdown.Option("Internship"),
            ft.dropdown.Option("Entry-Level"),
            ft.dropdown.Option("Mid-Level"),
            ft.dropdown.Option("Senior-Level"),
        ],
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        width=200,
    )

    github_field = ft.TextField(
        label="GitHub Profile URL",
        hint_text="https://github.com/username",
        value=prof.get("github", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    linkedin_field = ft.TextField(
        label="LinkedIn Profile URL",
        hint_text="https://linkedin.com/in/username",
        value=prof.get("linkedin", ""),
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    # Skills Editor
    skills_list: List[str] = list(prof.get("skills") or ["Python", "FastAPI", "SQL", "Git"])
    skills_chips_row = ft.Row(wrap=True, spacing=6, run_spacing=6)
    new_skill_input = ft.TextField(
        hint_text="Type skill and press Add (e.g. Docker, PyTorch)...",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    def render_skill_chips():
        skills_chips_row.controls.clear()
        for sk in skills_list:
            def remove_sk(_, skill_name=sk):
                if skill_name in skills_list:
                    skills_list.remove(skill_name)
                    render_skill_chips()
                    page.update()

            skills_chips_row.controls.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text(sk, size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.W_500),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                icon_size=12,
                                icon_color=TEXT_MUTED,
                                on_click=remove_sk,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    padding=ft.Padding(left=10, top=2, right=4, bottom=2),
                    border_radius=8,
                    bgcolor=f"{ACCENT_INDIGO}20",
                    border=ft.Border.all(1, f"{ACCENT_INDIGO}40"),
                )
            )

    def add_skill(e):
        val = new_skill_input.value.strip()
        if val and val not in skills_list:
            skills_list.append(val)
            new_skill_input.value = ""
            render_skill_chips()
            page.update()

    render_skill_chips()

    saving_indicator = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_INDIGO, visible=False)

    async def save_profile(e):
        saving_indicator.visible = True
        page.update()
        try:
            update_data = {
                "headline": headline_field.value.strip(),
                "degree": degree_field.value.strip(),
                "college": college_field.value.strip(),
                "year": year_field.value.strip(),
                "location": location_field.value.strip(),
                "experience_level": experience_dropdown.value,
                "github": github_field.value.strip(),
                "linkedin": linkedin_field.value.strip(),
                "skills": skills_list,
                "interests": prof.get("interests", []),
                "preferred_roles": [headline_field.value.strip()] if headline_field.value.strip() else [],
                "preferred_domains": prof.get("preferred_domains", []),
                "projects": prof.get("projects", []),
            }
            res = await api_client.update_profile(update_data)
            if res.get("status") == "success":
                show_toast(page, "Profile successfully updated in Supabase!")
                state.user_profile.update(update_data)
                on_profile_saved()
            else:
                show_toast(page, res.get("detail", "Failed to update profile."), is_error=True)
        except Exception as err:
            show_toast(page, f"Save error: {err}", is_error=True)
        finally:
            saving_indicator.visible = False
            page.update()

    # Digest trigger button
    digest_indicator = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_GREEN, visible=False)

    async def send_test_digest(e):
        digest_indicator.visible = True
        page.update()
        try:
            res = await api_client.trigger_daily_digest(force=True)
            if res.get("status") == "success":
                show_toast(page, "Test daily digest dispatched to your recipient email!")
            else:
                show_toast(page, res.get("message") or res.get("detail", "Digest dispatch skipped (already sent within 24h)."), is_info=True)
        except Exception as err:
            show_toast(page, f"Digest trigger error: {err}", is_error=True)
        finally:
            digest_indicator.visible = False
            page.update()

    # Email Digest Settings Card
    digest_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.EMAIL_ROUNDED, color=ACCENT_GREEN, size=20),
                        ft.Text("24-Hour Email Digest Automation", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Container(expand=True),
                        ft.Container(
                            content=ft.Text("SCHEDULED (03:30 UTC)", size=10, weight=ft.FontWeight.BOLD, color=ACCENT_GREEN),
                            padding=ft.Padding(left=8, top=2, right=8, bottom=2),
                            border_radius=4,
                            bgcolor=f"{ACCENT_GREEN}18",
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=4),
                ft.Text(
                    "CareerPulse runs an automated cron job on Vercel every 24 hours to deliver verified openings, "
                    "AI skill match gap analysis, and major technology news directly to your inbox.",
                    size=13,
                    color=TEXT_SECONDARY,
                ),
                ft.Container(height=10),
                ft.Row(
                    controls=[
                        digest_indicator,
                        ft.FilledButton(
                            text="Send Test Digest Email Now",
                            icon=ft.Icons.SEND_ROUNDED,
                            on_click=send_test_digest,
                            style=ft.ButtonStyle(
                                bgcolor=ACCENT_GREEN,
                                color=TEXT_PRIMARY,
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                        ),
                    ],
                    spacing=10,
                ),
            ],
            spacing=4,
        ),
        bgcolor=SURFACE_CARD,
        border=ft.Border.all(1, SURFACE_BORDER),
        border_radius=12,
        padding=18,
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Profile & Career Preferences", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Text("Configure your credentials, degree, and skills to calibrate the Gemini AI matching engine.", size=13, color=TEXT_SECONDARY),
                ft.Container(height=12),
                # Form Card
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(controls=[name_field, email_field], spacing=12),
                            ft.Container(height=6),
                            headline_field,
                            ft.Container(height=6),
                            ft.Row(controls=[degree_field, college_field, year_field], spacing=12),
                            ft.Container(height=6),
                            ft.Row(controls=[location_field, experience_dropdown], spacing=12),
                            ft.Container(height=6),
                            ft.Row(controls=[github_field, linkedin_field], spacing=12),
                            ft.Container(height=14),
                            ft.Text("Technical Skills", size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                            ft.Text("These skills are used to score opportunities and determine matching tiers.", size=12, color=TEXT_MUTED),
                            ft.Container(height=6),
                            ft.Row(
                                controls=[
                                    new_skill_input,
                                    ft.FilledButton(
                                        "Add Skill",
                                        icon=ft.Icons.ADD,
                                        on_click=add_skill,
                                        style=ft.ButtonStyle(
                                            bgcolor=ACCENT_INDIGO,
                                            shape=ft.RoundedRectangleBorder(radius=8),
                                        ),
                                    ),
                                ],
                                spacing=10,
                            ),
                            ft.Container(height=8),
                            skills_chips_row,
                            ft.Container(height=16),
                            ft.Row(
                                controls=[
                                    saving_indicator,
                                    ft.FilledButton(
                                        text="Save Profile Changes",
                                        icon=ft.Icons.SAVE,
                                        on_click=save_profile,
                                        style=ft.ButtonStyle(
                                            bgcolor=ACCENT_INDIGO,
                                            color=TEXT_PRIMARY,
                                            padding=ft.Padding(left=20, top=12, right=20, bottom=12),
                                            shape=ft.RoundedRectangleBorder(radius=8),
                                        ),
                                    ),
                                ],
                                spacing=10,
                                alignment=ft.MainAxisAlignment.END,
                            ),
                        ],
                        spacing=6,
                    ),
                    bgcolor=SURFACE_CARD,
                    border=ft.Border.all(1, SURFACE_BORDER),
                    border_radius=12,
                    padding=20,
                ),
                ft.Container(height=16),
                digest_card,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
