"""
Jobs & Internships Opportunities Explorer View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER
)
from app.flet_app.state import state
from app.flet_app.components.job_card import build_job_card

def build_jobs_view(
    page: ft.Page,
    on_save_opportunity: Callable[[Dict[str, Any]], None],
    on_remove_opportunity: Callable[[Dict[str, Any]], None],
    on_outreach: Callable[[Dict[str, Any]], None],
    on_refresh: Callable[[], None],
) -> ft.Container:
    """Builds the comprehensive job & internship search and exploration page."""

    header = ft.Row(
        controls=[
            ft.Column(
                controls=[
                    ft.Text("Jobs & Internships Explorer", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ft.Text("Live, strictly verified opportunities extracted from direct company career portals.", size=13, color=TEXT_SECONDARY),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            ft.FilledButton(
                text="Refresh Pipeline",
                icon=ft.Icons.REFRESH,
                on_click=lambda _: on_refresh(),
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_INDIGO,
                    color=TEXT_PRIMARY,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    filter_tier = "All"
    filter_type = "All"
    search_query = ""
    cards_column = ft.Column(spacing=10)

    def update_cards():
        cards_column.controls.clear()
        saved_keys = {
            f"{o.get('company','')}_{o.get('title','')}".lower().replace(" ", "_")
            for o in state.saved_opportunities
        }

        filtered = []
        for opp in state.opportunities:
            t = opp.get("tier", "Gold")
            loc = opp.get("location", "Remote")
            if filter_tier != "All" and t.lower() != filter_tier.lower():
                continue
            if filter_type == "Remote" and "remote" not in loc.lower():
                continue
            if search_query:
                q = search_query.lower()
                text_to_search = f"{opp.get('title','')} {opp.get('company','')} {' '.join(opp.get('skills',[]))}".lower()
                if q not in text_to_search:
                    continue
            filtered.append(opp)

        if not filtered:
            cards_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.SEARCH_OFF, size=36, color=TEXT_MUTED),
                            ft.Container(height=6),
                            ft.Text("No opportunities found matching this filter.", size=14, color=TEXT_SECONDARY),
                            ft.Text("Try modifying your keywords or tier filters.", size=12, color=TEXT_MUTED),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                    ),
                    padding=40,
                    alignment=ft.alignment.center,
                )
            )
        else:
            for opp in filtered:
                k = f"{opp.get('company','')}_{opp.get('title','')}".lower().replace(" ", "_")
                is_saved = k in saved_keys
                cards_column.controls.append(
                    build_job_card(
                        opp,
                        on_save=on_save_opportunity,
                        on_remove=on_remove_opportunity,
                        on_outreach=on_outreach,
                        is_saved=is_saved,
                        page=page,
                    )
                )
        page.update()

    def on_search_change(e):
        nonlocal search_query
        search_query = e.control.value.strip()
        update_cards()

    def on_tier_select(tier_val: str):
        nonlocal filter_tier
        filter_tier = tier_val
        update_cards()

    def on_type_select(type_val: str):
        nonlocal filter_type
        filter_type = type_val
        update_cards()

    search_field = ft.TextField(
        hint_text="Filter by title, company, or technical stack (e.g. Python, React, Cloud)...",
        prefix_icon=ft.Icons.SEARCH,
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        on_change=on_search_change,
        expand=True,
    )

    filter_row = ft.Row(
        controls=[
            search_field,
            ft.Row(
                controls=[
                    ft.TextButton("All", on_click=lambda _: on_tier_select("All")),
                    ft.TextButton("🥇 Gold", on_click=lambda _: on_tier_select("Gold")),
                    ft.TextButton("🥈 Silver", on_click=lambda _: on_tier_select("Silver")),
                    ft.TextButton("🥉 Bronze", on_click=lambda _: on_tier_select("Bronze")),
                    ft.TextButton("🌐 Remote Only", on_click=lambda _: on_type_select("Remote")),
                ],
                spacing=4,
            ),
        ],
        spacing=12,
    )

    update_cards()

    return ft.Container(
        content=ft.Column(
            controls=[
                header,
                ft.Container(height=10),
                filter_row,
                ft.Container(height=10),
                cards_column,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
