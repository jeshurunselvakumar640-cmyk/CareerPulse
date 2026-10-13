"""
Saved & Waitlisted Opportunities View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_AMBER
)
from app.flet_app.state import state
from app.flet_app.components.job_card import build_job_card

def build_saved_view(
    page: ft.Page,
    on_remove_opportunity: Callable[[Dict[str, Any]], None],
    on_outreach: Callable[[Dict[str, Any]], None],
    on_refresh_saved: Callable[[], None],
) -> ft.Container:
    """Builds the saved opportunities tracking page."""

    active_tab = "interested"
    cards_column = ft.Column(spacing=10)

    header = ft.Row(
        controls=[
            ft.Column(
                controls=[
                    ft.Text("Saved Career Opportunities", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ft.Text("Track, organize, and follow up on opportunities stored in your Supabase workspace.", size=13, color=TEXT_SECONDARY),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            ft.FilledButton(
                text="Refresh Saved",
                icon=ft.Icons.SYNC,
                on_click=lambda _: on_refresh_saved(),
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_INDIGO,
                    color=TEXT_PRIMARY,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    def render_cards():
        cards_column.controls.clear()
        target_list = state.saved_opportunities if active_tab == "interested" else state.waitlisted_opportunities

        if not target_list:
            cards_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.BOOKMARK_BORDER, size=40, color=TEXT_MUTED),
                            ft.Container(height=8),
                            ft.Text(f"No opportunities in your {'Saved' if active_tab == 'interested' else 'Waitlist'} list.", size=15, color=TEXT_PRIMARY),
                            ft.Text("Explore the Dashboard or Jobs view and click 'Save Opportunity' on matching openings.", size=13, color=TEXT_MUTED),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                    ),
                    padding=40,
                    alignment=ft.alignment.center,
                )
            )
        else:
            for opp in target_list:
                cards_column.controls.append(
                    build_job_card(
                        opp,
                        on_remove=on_remove_opportunity,
                        on_outreach=on_outreach,
                        is_saved=True,
                        page=page,
                    )
                )
        page.update()

    def set_tab(tab_name: str):
        nonlocal active_tab
        active_tab = tab_name
        render_cards()

    tabs_row = ft.Row(
        controls=[
            ft.TextButton(
                f"Interested ({len(state.saved_opportunities)})",
                icon=ft.Icons.STAR,
                on_click=lambda _: set_tab("interested"),
                style=ft.ButtonStyle(color=ACCENT_AMBER),
            ),
            ft.TextButton(
                f"Waitlist ({len(state.waitlisted_opportunities)})",
                icon=ft.Icons.SCHEDULE,
                on_click=lambda _: set_tab("waitlisted"),
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
            ),
        ],
        spacing=8,
    )

    render_cards()

    return ft.Container(
        content=ft.Column(
            controls=[
                header,
                ft.Container(height=10),
                tabs_row,
                ft.Container(height=10),
                cards_column,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
