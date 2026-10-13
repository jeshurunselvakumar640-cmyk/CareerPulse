"""
Personalized Technology News View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any, List
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_VIOLET
)
from app.flet_app.state import state
from app.flet_app.components.news_card import build_news_card

def build_news_view(
    page: ft.Page,
    on_refresh_news: Callable[[bool], None],
) -> ft.Container:
    """Builds the personalized technology news feed view."""

    header = ft.Row(
        controls=[
            ft.Column(
                controls=[
                    ft.Text("Personalized Technology News", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ft.Text("Major technology developments, architecture updates, and industry news tailored to your skills.", size=13, color=TEXT_SECONDARY),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            ft.FilledButton(
                text="Refresh News Feed",
                icon=ft.Icons.REFRESH,
                on_click=lambda _: on_refresh_news(True),
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_INDIGO,
                    color=TEXT_PRIMARY,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    articles = state.news_articles or []
    news_cards = []

    if not articles:
        news_cards.append(
            ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.NEWSPAPER, size=40, color=TEXT_MUTED),
                        ft.Container(height=8),
                        ft.Text("No news stories currently cached.", size=15, color=TEXT_PRIMARY),
                        ft.Text("Click 'Refresh News Feed' to fetch real-time technology updates from Tavily.", size=13, color=TEXT_MUTED),
                        ft.Container(height=12),
                        ft.OutlinedButton(
                            text="Fetch News Now",
                            icon=ft.Icons.DOWNLOAD,
                            on_click=lambda _: on_refresh_news(True),
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
                padding=40,
                alignment=ft.alignment.center,
            )
        )
    else:
        for article in articles:
            news_cards.append(build_news_card(article))

    return ft.Container(
        content=ft.Column(
            controls=[
                header,
                ft.Container(height=14),
                ft.Column(controls=news_cards, spacing=10),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
