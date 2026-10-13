"""
Career Dashboard View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any, List, Optional
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER
)
from app.flet_app.state import state
from app.flet_app.components.stat_card import build_stat_card
from app.flet_app.components.job_card import build_job_card
from app.flet_app.components.toast import show_toast

def build_dashboard_view(
    page: ft.Page,
    on_save_opportunity: Callable[[Dict[str, Any]], None],
    on_remove_opportunity: Callable[[Dict[str, Any]], None],
    on_outreach: Callable[[Dict[str, Any]], None],
    on_refresh: Callable[[], None],
) -> ft.Container:
    """Builds the main Career Dashboard overview page."""

    user_name = state.user_profile.get("name") or "Candidate"
    
    # 1. Greeting & Hero Header
    greeting = ft.Row(
        controls=[
            ft.Column(
                controls=[
                    ft.Text(f"Welcome back, {user_name} 👋", size=24, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ft.Text("Here is your personalized career intelligence briefing and opportunity pipeline.", size=13, color=TEXT_SECONDARY),
                ],
                spacing=2,
            ),
            ft.Container(expand=True),
            ft.FilledButton(
                text="Refresh Opportunities",
                icon=ft.Icons.SYNC,
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

    # 2. KPI Metrics Cards
    saved_count = len(state.saved_opportunities)
    opps_count = len(state.opportunities)
    news_count = len(state.news_articles)
    
    metrics_row = ft.Row(
        controls=[
            build_stat_card("Active Opportunities", str(opps_count or 0), ft.Icons.WORK, ACCENT_INDIGO, "Verified openings", "LIVE", ACCENT_GREEN),
            build_stat_card("Saved & Interested", str(saved_count), ft.Icons.BOOKMARK, ACCENT_AMBER, "Tracking in Supabase", "SAVED", ACCENT_AMBER),
            build_stat_card("Tech News Stories", str(news_count or 0), ft.Icons.NEWSPAPER, ACCENT_CYAN, "From Tavily & Gemini", "UPDATED", ACCENT_CYAN),
            build_stat_card("Mailbox Integration", "Connected" if state.mailbox_status.get("connected") else "Ready", ft.Icons.EMAIL, ACCENT_GREEN, "Gmail Reply Tracking", "SYNC", ACCENT_GREEN),
        ],
        spacing=14,
    )

    # 3. AI Insights Panel
    insights = state.insights or {}
    insights_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.AUTO_AWESOME, color=ACCENT_INDIGO, size=18),
                        ft.Text("AI Career Match Insights", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Container(expand=True),
                        ft.Container(
                            content=ft.Text("Gemini 3.5 Powered", size=10, weight=ft.FontWeight.W_600, color=ACCENT_CYAN),
                            padding=ft.Padding(left=8, top=2, right=8, bottom=2),
                            border_radius=10,
                            bgcolor=f"{ACCENT_CYAN}15",
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=4),
                ft.Text(insights.get("insight_text", "AI is matching opportunities with your profile stack."), size=13, color=TEXT_SECONDARY),
                ft.Container(height=10),
                ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Text("Best Matching Role", size=11, color=TEXT_MUTED),
                                    ft.Text(insights.get("best_role", "Software Engineer"), size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                                ],
                                spacing=2,
                            ),
                            bgcolor=SURFACE_BORDER,
                            border_radius=8,
                            padding=12,
                            expand=True,
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Text("Top Validated Skill", size=11, color=TEXT_MUTED),
                                    ft.Text(insights.get("top_skill", "Python / Systems"), size=14, weight=ft.FontWeight.BOLD, color=ACCENT_GREEN),
                                ],
                                spacing=2,
                            ),
                            bgcolor=SURFACE_BORDER,
                            border_radius=8,
                            padding=12,
                            expand=True,
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Text("Skill to Develop", size=11, color=TEXT_MUTED),
                                    ft.Text(insights.get("growing_skill", "Cloud / Microservices"), size=14, weight=ft.FontWeight.BOLD, color=ACCENT_AMBER),
                                ],
                                spacing=2,
                            ),
                            bgcolor=SURFACE_BORDER,
                            border_radius=8,
                            padding=12,
                            expand=True,
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[
                                    ft.Text("Recommended Domain", size=11, color=TEXT_MUTED),
                                    ft.Text(insights.get("recommended", "Full Stack Systems"), size=14, weight=ft.FontWeight.BOLD, color=ACCENT_CYAN),
                                ],
                                spacing=2,
                            ),
                            bgcolor=SURFACE_BORDER,
                            border_radius=8,
                            padding=12,
                            expand=True,
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

    # 4. Filter & Search Controls
    filter_tier = "All"
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
            if filter_tier != "All" and t.lower() != filter_tier.lower():
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
                            ft.Text("No opportunities found matching your criteria.", size=14, color=TEXT_SECONDARY),
                            ft.Text("Try adjusting your search query, selecting another tier, or refreshing the pipeline.", size=12, color=TEXT_MUTED),
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

    def on_tier_change(tier_val: str):
        nonlocal filter_tier
        filter_tier = tier_val
        update_cards()

    search_field = ft.TextField(
        hint_text="Search opportunities by title, company, or required skill...",
        prefix_icon=ft.Icons.SEARCH,
        bgcolor=SURFACE_CARD,
        border_color=SURFACE_BORDER,
        color=TEXT_PRIMARY,
        text_size=13,
        on_change=on_search_change,
        expand=True,
    )

    tier_buttons = ft.Row(
        controls=[
            ft.TextButton("All Tiers", on_click=lambda _: on_tier_change("All"), style=ft.ButtonStyle(color=TEXT_PRIMARY)),
            ft.TextButton("🥇 Gold", on_click=lambda _: on_tier_change("Gold"), style=ft.ButtonStyle(color=ACCENT_AMBER)),
            ft.TextButton("🥈 Silver", on_click=lambda _: on_tier_change("Silver"), style=ft.ButtonStyle(color=TEXT_SECONDARY)),
            ft.TextButton("🥉 Bronze", on_click=lambda _: on_tier_change("Bronze"), style=ft.ButtonStyle(color=ACCENT_INDIGO)),
        ],
        spacing=6,
    )

    filters_row = ft.Row(
        controls=[
            search_field,
            tier_buttons,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        spacing=12,
    )

    # Initial render of cards
    update_cards()

    # Content Column
    return ft.Container(
        content=ft.Column(
            controls=[
                greeting,
                ft.Container(height=10),
                metrics_row,
                ft.Container(height=10),
                insights_card,
                ft.Container(height=14),
                ft.Row(
                    controls=[
                        ft.Text("Recommended Opportunities", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Container(expand=True),
                        ft.Text(f"{len(state.opportunities)} Verified Openings", size=12, color=TEXT_MUTED),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                filters_row,
                ft.Container(height=8),
                cards_column,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
