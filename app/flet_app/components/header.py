"""
Header Bar Component for CareerPulse Flet GUI.
"""
from typing import Callable, Optional
import flet as ft

from app.flet_app.config import (
    APP_NAME, APP_VERSION, BG_DARK, SURFACE_CARD, SURFACE_BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_GREEN, ACCENT_CYAN, ACCENT_AMBER
)
from app.flet_app.state import state
from app.flet_app.api_client import api_client

def build_header(
    page: ft.Page,
    on_login_click: Optional[Callable[[], None]] = None,
    on_logout_click: Optional[Callable[[], None]] = None,
    on_refresh_click: Optional[Callable[[], None]] = None,
) -> ft.Container:
    """Builds the top navigation header bar."""
    
    # Brand logo and title
    brand_logo = ft.Row(
        controls=[
            ft.Container(
                content=ft.Icon(ft.Icons.ROCKET_LAUNCH, size=18, color=TEXT_PRIMARY),
                padding=6,
                border_radius=8,
                bgcolor=ACCENT_INDIGO,
            ),
            ft.Text(APP_NAME, size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ft.Container(
                content=ft.Text("FLET", size=10, weight=ft.FontWeight.BOLD, color=ACCENT_CYAN),
                padding=ft.Padding(left=6, top=2, right=6, bottom=2),
                border_radius=4,
                bgcolor=f"{ACCENT_CYAN}20",
                border=ft.Border.all(1, f"{ACCENT_CYAN}40"),
            ),
        ],
        spacing=8,
    )

    # Backend status pill
    status_pill = ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(width=7, height=7, border_radius=4, bgcolor=ACCENT_GREEN),
                ft.Text("Backend Connected", size=11, color=ACCENT_GREEN, weight=ft.FontWeight.W_500),
            ],
            spacing=6,
        ),
        padding=ft.Padding(left=10, top=4, right=10, bottom=4),
        border_radius=12,
        bgcolor=f"{ACCENT_GREEN}15",
        border=ft.Border.all(1, f"{ACCENT_GREEN}30"),
    )

    # Right side: User info or Auth button
    user_name = state.user_profile.get("name") or "Candidate"
    user_email = state.user_profile.get("email") or ""
    
    right_controls = [status_pill]

    if on_refresh_click:
        right_controls.append(
            ft.IconButton(
                icon=ft.Icons.REFRESH,
                icon_color=TEXT_SECONDARY,
                tooltip="Refresh Opportunities & News",
                on_click=lambda _: on_refresh_click(),
            )
        )

    if state.authenticated or user_email:
        user_chip = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ACCOUNT_CIRCLE, size=24, color=ACCENT_INDIGO),
                    ft.Column(
                        controls=[
                            ft.Text(user_name, size=12, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                            ft.Text(user_email[:20] + "..." if len(user_email) > 20 else user_email, size=10, color=TEXT_MUTED),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.Padding(left=8, top=4, right=10, bottom=4),
            border_radius=8,
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, SURFACE_BORDER),
        )
        right_controls.append(user_chip)

        if on_logout_click:
            right_controls.append(
                ft.IconButton(
                    icon=ft.Icons.LOGOUT,
                    icon_color=TEXT_MUTED,
                    tooltip="Log Out",
                    on_click=lambda _: on_logout_click(),
                )
            )
    else:
        if on_login_click:
            right_controls.append(
                ft.FilledButton(
                    text="Sign In",
                    icon=ft.Icons.LOGIN,
                    on_click=lambda _: on_login_click(),
                    style=ft.ButtonStyle(
                        bgcolor=ACCENT_INDIGO,
                        color=TEXT_PRIMARY,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    ),
                )
            )

    return ft.Container(
        content=ft.Row(
            controls=[
                brand_logo,
                ft.Container(expand=True),
                ft.Row(controls=right_controls, spacing=10),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=BG_DARK,
        border=ft.Border(bottom=ft.BorderSide(1, SURFACE_BORDER)),
        padding=ft.Padding(left=20, top=12, right=20, bottom=12),
    )
