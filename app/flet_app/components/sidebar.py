"""
Sidebar Navigation Component for CareerPulse Flet GUI.
"""
from typing import Callable, Optional
import flet as ft

from app.flet_app.config import (
    BG_SIDEBAR, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_AMBER
)
from app.flet_app.state import state

def build_sidebar(
    current_view: str,
    on_navigate: Callable[[str], None],
    saved_count: int = 0,
    apps_count: int = 0
) -> ft.Container:
    """Builds a responsive, dark navigation sidebar."""

    nav_items = [
        {"key": "dashboard", "label": "Career Dashboard", "icon": ft.Icons.DASHBOARD_ROUNDED, "section": "OVERVIEW"},
        {"key": "jobs", "label": "Jobs & Internships", "icon": ft.Icons.WORK_ROUNDED, "section": "DISCOVERY"},
        {"key": "news", "label": "Technology News", "icon": ft.Icons.NEWSPAPER_ROUNDED, "section": "DISCOVERY"},
        {"key": "saved", "label": "Saved Opportunities", "icon": ft.Icons.BOOKMARK_ROUNDED, "section": "TRACKING", "badge": saved_count},
        {"key": "tracker", "label": "Applications & Mailbox", "icon": ft.Icons.MARK_EMAIL_READ_ROUNDED, "section": "TRACKING", "badge": apps_count},
        {"key": "resume", "label": "Resume Builder", "icon": ft.Icons.DESCRIPTION_ROUNDED, "section": "TOOLS"},
        {"key": "profile", "label": "Profile & Settings", "icon": ft.Icons.SETTINGS_ROUNDED, "section": "TOOLS"},
        {"key": "landing", "label": "Landing / About", "icon": ft.Icons.INFO_OUTLINE_ROUNDED, "section": "PLATFORM"},
    ]

    sidebar_controls = []
    current_section = None

    for item in nav_items:
        section = item["section"]
        if section != current_section:
            current_section = section
            sidebar_controls.append(
                ft.Container(
                    content=ft.Text(section, size=10, weight=ft.FontWeight.BOLD, color=TEXT_MUTED),
                    padding=ft.Padding(left=12, top=14, right=12, bottom=4),
                )
            )

        is_active = current_view == item["key"]
        
        row_children = [
            ft.Icon(
                item["icon"],
                size=18,
                color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
            ),
            ft.Text(
                item["label"],
                size=13,
                weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.W_400,
                color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
            ),
            ft.Container(expand=True),
        ]

        if item.get("badge") and item["badge"] > 0:
            row_children.append(
                ft.Container(
                    content=ft.Text(str(item["badge"]), size=10, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    padding=ft.Padding(left=6, top=2, right=6, bottom=2),
                    border_radius=10,
                    bgcolor=ACCENT_INDIGO,
                )
            )

        btn = ft.Container(
            content=ft.Row(controls=row_children, spacing=10),
            padding=ft.Padding(left=12, top=10, right=12, bottom=10),
            border_radius=8,
            bgcolor=f"{ACCENT_INDIGO}25" if is_active else ft.Colors.TRANSPARENT,
            border=ft.Border.all(1, f"{ACCENT_INDIGO}60") if is_active else None,
            on_click=lambda _, key=item["key"]: on_navigate(key),
            ink=True,
        )
        sidebar_controls.append(btn)

    return ft.Container(
        content=ft.Column(
            controls=sidebar_controls,
            spacing=3,
            scroll=ft.ScrollMode.AUTO,
        ),
        width=250,
        bgcolor=BG_SIDEBAR,
        border=ft.Border(right=ft.BorderSide(1, SURFACE_BORDER)),
        padding=ft.Padding(left=12, top=16, right=12, bottom=16),
    )
