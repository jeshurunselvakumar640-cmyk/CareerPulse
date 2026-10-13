"""
Dashboard Metric Stat Card Component.
"""
import flet as ft
from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER
)

def build_stat_card(
    title: str,
    value: str,
    icon: ft.Icons,
    icon_color: str = ACCENT_INDIGO,
    subtitle: str = "",
    badge_text: str = "",
    badge_color: str = ACCENT_GREEN
) -> ft.Container:
    """Builds a polished modern KPI card with dark glassmorphic styling."""
    
    header_row = [
        ft.Container(
            content=ft.Icon(icon, color=icon_color, size=20),
            padding=10,
            border_radius=8,
            bgcolor=f"{icon_color}18", # 10% opacity tint
        ),
        ft.Container(expand=True),
    ]
    if badge_text:
        header_row.append(
            ft.Container(
                content=ft.Text(badge_text, color=badge_color, size=11, weight=ft.FontWeight.W_600),
                padding=ft.Padding(left=8, top=4, right=8, bottom=4),
                border_radius=12,
                bgcolor=f"{badge_color}20",
                border=ft.Border.all(1, f"{badge_color}40"),
            )
        )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(controls=header_row, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Container(height=10),
                ft.Text(value, size=26, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Text(title, size=13, weight=ft.FontWeight.W_500, color=TEXT_SECONDARY),
                ft.Text(subtitle, size=11, color=TEXT_MUTED) if subtitle else ft.Container(),
            ],
            spacing=2,
        ),
        bgcolor=SURFACE_CARD,
        border=ft.Border.all(1, SURFACE_BORDER),
        border_radius=12,
        padding=18,
        expand=True,
    )
