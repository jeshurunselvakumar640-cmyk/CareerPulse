"""
Interactive Resume Builder Workspace View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any, List
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, SURFACE_ELEVATED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER, ACCENT_RED
)
from app.flet_app.state import state
from app.flet_app.api_client import api_client
from app.flet_app.components.toast import show_toast

def build_resume_view(
    page: ft.Page,
    on_refresh_resume: Callable[[], None],
) -> ft.Container:
    """Builds the structured Resume Builder workspace."""

    res_data = state.resume_data or {}
    prof = res_data.get("profile") or {}
    education_list: List[Dict[str, Any]] = res_data.get("education") or []
    experience_list: List[Dict[str, Any]] = res_data.get("experience") or []

    # Personal Details Form
    name_field = ft.TextField(
        label="Candidate Name",
        value=prof.get("name") or state.user_profile.get("name") or "",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    email_field = ft.TextField(
        label="Contact Email",
        value=prof.get("email") or state.user_profile.get("email") or "",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    headline_field = ft.TextField(
        label="Professional Resume Headline",
        value=prof.get("headline") or state.user_profile.get("headline") or "",
        hint_text="e.g. Full-Stack Software Engineer with expertise in Cloud & AI",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
    )

    location_field = ft.TextField(
        label="Location / City",
        value=prof.get("city") or state.user_profile.get("location") or "",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    website_field = ft.TextField(
        label="Portfolio / Website URL",
        value=prof.get("website_url") or "",
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        expand=True,
    )

    saving_indicator = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_INDIGO, visible=False)

    async def save_personal(e):
        saving_indicator.visible = True
        page.update()
        try:
            payload = {
                "name": name_field.value.strip(),
                "email": email_field.value.strip(),
                "city": location_field.value.strip(),
                "website_url": website_field.value.strip(),
            }
            res = await api_client.save_resume_personal_details(payload)
            if res.get("status") == "success":
                show_toast(page, "Resume personal details saved!")
            else:
                show_toast(page, "Failed to save details.", is_error=True)
        except Exception as err:
            show_toast(page, f"Error: {err}", is_error=True)
        finally:
            saving_indicator.visible = False
            page.update()

    # Education Cards
    edu_column = ft.Column(spacing=8)
    for edu in education_list:
        degree = edu.get("course_degree", "Degree")
        school = edu.get("school_university", "University")
        grade = edu.get("grade_score", "")
        item_id = str(edu.get("id", ""))

        async def delete_edu(_, i_id=item_id):
            res = await api_client.delete_resume_child("education", i_id)
            if res.get("status") == "success":
                show_toast(page, "Education entry removed.")
                on_refresh_resume()

        edu_column.controls.append(
            ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(degree, size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                                ft.Text(f"{school} • {grade}" if grade else school, size=12, color=TEXT_SECONDARY),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=16,
                            icon_color=TEXT_MUTED,
                            on_click=delete_edu,
                        ),
                    ],
                ),
                bgcolor=SURFACE_CARD,
                border=ft.Border.all(1, SURFACE_BORDER),
                border_radius=8,
                padding=12,
            )
        )

    # Experience Cards
    exp_column = ft.Column(spacing=8)
    for exp in experience_list:
        role = exp.get("job_title", "Role")
        comp = exp.get("company_name", "Company")
        details = exp.get("details", "")
        item_id = str(exp.get("id", ""))

        async def delete_exp(_, i_id=item_id):
            res = await api_client.delete_resume_child("experience", i_id)
            if res.get("status") == "success":
                show_toast(page, "Experience entry removed.")
                on_refresh_resume()

        exp_column.controls.append(
            ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(role, size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                                ft.Text(comp, size=12, color=TEXT_SECONDARY),
                                ft.Text(details[:100] + "..." if len(details) > 100 else details, size=11, color=TEXT_MUTED) if details else ft.Container(),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_size=16,
                            icon_color=TEXT_MUTED,
                            on_click=delete_exp,
                        ),
                    ],
                ),
                bgcolor=SURFACE_CARD,
                border=ft.Border.all(1, SURFACE_BORDER),
                border_radius=8,
                padding=12,
            )
        )

    # Add Education Dialog
    def open_add_edu_dialog(e):
        degree_in = ft.TextField(label="Course / Degree", hint_text="e.g. B.Tech Computer Science", bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)
        school_in = ft.TextField(label="School / University", hint_text="e.g. Stanford University", bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)
        grade_in = ft.TextField(label="Grade / Score", hint_text="e.g. 3.8 GPA or 85%", bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)

        async def submit_edu(ev):
            payload = {
                "course_degree": degree_in.value.strip(),
                "school_university": school_in.value.strip(),
                "grade_score": grade_in.value.strip(),
            }
            res = await api_client.save_resume_education(payload)
            if res.get("status") == "success":
                show_toast(page, "Education entry saved to Supabase!")
                dlg.open = False
                on_refresh_resume()
            else:
                show_toast(page, "Failed to save education.", is_error=True)

        dlg = ft.AlertDialog(
            title=ft.Text("Add Education", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            content=ft.Column(controls=[degree_in, school_in, grade_in], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or page.update()),
                ft.FilledButton("Save", on_click=submit_edu, style=ft.ButtonStyle(bgcolor=ACCENT_INDIGO)),
            ],
            bgcolor=SURFACE_ELEVATED,
            open=True,
        )
        page.overlay.append(dlg)
        page.update()

    # Add Experience Dialog
    def open_add_exp_dialog(e):
        role_in = ft.TextField(label="Job Title / Role", hint_text="e.g. Backend Engineer Intern", bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)
        comp_in = ft.TextField(label="Company Name", hint_text="e.g. Acme Inc.", bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)
        det_in = ft.TextField(label="Key Responsibilities & Accomplishments", multiline=True, min_lines=3, bgcolor=SURFACE_CARD, color=TEXT_PRIMARY, text_size=13)

        async def submit_exp(ev):
            payload = {
                "job_title": role_in.value.strip(),
                "company_name": comp_in.value.strip(),
                "details": det_in.value.strip(),
            }
            res = await api_client.save_resume_experience(payload)
            if res.get("status") == "success":
                show_toast(page, "Experience entry saved to Supabase!")
                dlg.open = False
                on_refresh_resume()
            else:
                show_toast(page, "Failed to save experience.", is_error=True)

        dlg = ft.AlertDialog(
            title=ft.Text("Add Work Experience", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            content=ft.Column(controls=[role_in, comp_in, det_in], tight=True, spacing=10),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or page.update()),
                ft.FilledButton("Save", on_click=submit_exp, style=ft.ButtonStyle(bgcolor=ACCENT_INDIGO)),
            ],
            bgcolor=SURFACE_ELEVATED,
            open=True,
        )
        page.overlay.append(dlg)
        page.update()

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Interactive Resume Builder", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Text("Maintain a structured resume portfolio in Supabase synchronized with your career agent.", size=13, color=TEXT_SECONDARY),
                ft.Container(height=12),
                # Personal Details Card
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text("Personal Details & Contact", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                            ft.Container(height=4),
                            ft.Row(controls=[name_field, email_field], spacing=12),
                            ft.Container(height=4),
                            headline_field,
                            ft.Container(height=4),
                            ft.Row(controls=[location_field, website_field], spacing=12),
                            ft.Container(height=8),
                            ft.Row(
                                controls=[
                                    saving_indicator,
                                    ft.FilledButton("Save Details", icon=ft.Icons.SAVE, on_click=save_personal, style=ft.ButtonStyle(bgcolor=ACCENT_INDIGO)),
                                ],
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
                ft.Container(height=14),
                # Education Section
                ft.Row(
                    controls=[
                        ft.Text("Education History", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Container(expand=True),
                        ft.OutlinedButton("Add Education", icon=ft.Icons.ADD, on_click=open_add_edu_dialog),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                edu_column if education_list else ft.Text("No education entries added yet.", size=12, color=TEXT_MUTED),
                ft.Container(height=14),
                # Experience Section
                ft.Row(
                    controls=[
                        ft.Text("Work & Internship Experience", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Container(expand=True),
                        ft.OutlinedButton("Add Experience", icon=ft.Icons.ADD, on_click=open_add_exp_dialog),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                exp_column if experience_list else ft.Text("No experience entries added yet.", size=12, color=TEXT_MUTED),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
