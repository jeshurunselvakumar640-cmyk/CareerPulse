"""
Personalized Technology News Card Component.
"""
from typing import Dict, Any
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_CYAN, ACCENT_INDIGO, ACCENT_VIOLET
)

def build_news_card(article: Dict[str, Any]) -> ft.Container:
    """Builds a modern tech news story card with verified source link."""
    title = article.get("title", "Technology Industry Update")
    url = article.get("url", "")
    domain = article.get("source_domain") or article.get("domain") or "Verified Source"
    published_date = article.get("published_date") or article.get("date") or "Recent"
    summary = article.get("summary") or article.get("snippet") or ""
    tags = article.get("tags") or []

    tag_chips = []
    for tag in tags[:3]:
        tag_chips.append(
            ft.Container(
                content=ft.Text(f"#{tag}", size=11, color=ACCENT_CYAN, weight=ft.FontWeight.W_500),
                padding=ft.Padding(left=8, top=2, right=8, bottom=2),
                border_radius=4,
                bgcolor=f"{ACCENT_CYAN}15",
            )
        )

    return ft.Container(
        content=ft.Column(
            controls=[
                # Top header: Source domain + Date
                ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.NEWSPAPER, size=13, color=ACCENT_VIOLET),
                                    ft.Text(domain, size=11, weight=ft.FontWeight.BOLD, color=ACCENT_VIOLET),
                                ],
                                spacing=4,
                            ),
                            padding=ft.Padding(left=8, top=3, right=8, bottom=3),
                            border_radius=6,
                            bgcolor=f"{ACCENT_VIOLET}18",
                        ),
                        ft.Container(expand=True),
                        ft.Text(published_date, size=11, color=TEXT_MUTED),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=4),
                # Title
                ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Container(height=4),
                # Summary
                ft.Text(summary, size=13, color=TEXT_SECONDARY, max_lines=4),
                ft.Container(height=6),
                # Tags & Read Source link
                ft.Row(
                    controls=[
                        ft.Row(controls=tag_chips, spacing=6),
                        ft.Container(expand=True),
                        ft.TextButton(
                            text="Read Full Article",
                            icon=ft.Icons.OPEN_IN_NEW,
                            url=url,
                            style=ft.ButtonStyle(
                                color=ACCENT_CYAN,
                            ),
                        ) if url else ft.Container(),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=4,
        ),
        bgcolor=SURFACE_CARD,
        border=ft.Border.all(1, SURFACE_BORDER),
        border_radius=12,
        padding=16,
        margin=ft.Margin(left=0, top=0, right=0, bottom=12),
    )
