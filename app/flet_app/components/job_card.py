"""
Opportunity Job Card Component.
Presents verified job & internship postings with tier ratings, skill match analysis, and actions.
"""
from typing import Dict, Any, Callable, Optional
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, SURFACE_BORDER_LIGHT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_GREEN, ACCENT_AMBER, ACCENT_CYAN,
    TIER_COLORS
)

def build_job_card(
    opportunity: Dict[str, Any],
    on_save: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_remove: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_outreach: Optional[Callable[[Dict[str, Any]], None]] = None,
    is_saved: bool = False,
    page: Optional[ft.Page] = None,
) -> ft.Container:
    """Builds a rich, responsive opportunity card with AI skill matching indicators."""
    
    tier = opportunity.get("tier", "Gold")
    tier_info = TIER_COLORS.get(tier, TIER_COLORS["Gold"])
    
    title = opportunity.get("title", "Software Engineering Opportunity")
    company = opportunity.get("company", "Technology Company")
    location = opportunity.get("location", "Remote")
    match_pct = int(opportunity.get("matchPercentage", 75))
    url = opportunity.get("url", "")
    summary = opportunity.get("summary", "")
    matching_skills = opportunity.get("skills", []) or []
    lacking_skills = opportunity.get("lackingSkills", []) or []

    # Tier Badge
    tier_badge = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.STAR if tier == "Gold" else ft.Icons.WORKSPACE_PREMIUM, size=14, color=tier_info["text"]),
                ft.Text(f"{tier} Match", size=11, weight=ft.FontWeight.BOLD, color=tier_info["text"]),
            ],
            spacing=4,
        ),
        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
        border_radius=12,
        bgcolor=tier_info["bg"],
        border=ft.Border.all(1, tier_info["border"]),
    )

    # Match Percentage Badge
    match_color = ACCENT_GREEN if match_pct >= 80 else (ACCENT_CYAN if match_pct >= 60 else ACCENT_AMBER)
    match_badge = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.AUTO_AWESOME, size=13, color=match_color),
                ft.Text(f"{match_pct}% Match", size=11, weight=ft.FontWeight.W_600, color=match_color),
            ],
            spacing=4,
        ),
        padding=ft.Padding(left=8, top=3, right=8, bottom=3),
        border_radius=12,
        bgcolor=f"{match_color}18",
        border=ft.Border.all(1, f"{match_color}40"),
    )

    # Skill matching chips
    skill_chips = []
    for skill in matching_skills[:4]:
        skill_chips.append(
            ft.Container(
                content=ft.Text(f"✓ {skill}", size=11, color=ACCENT_GREEN, weight=ft.FontWeight.W_500),
                padding=ft.Padding(left=8, top=3, right=8, bottom=3),
                border_radius=6,
                bgcolor=f"{ACCENT_GREEN}15",
                border=ft.Border.all(1, f"{ACCENT_GREEN}30"),
            )
        )
    for skill in lacking_skills[:3]:
        skill_chips.append(
            ft.Container(
                content=ft.Text(f"+ {skill}", size=11, color=ACCENT_AMBER, weight=ft.FontWeight.W_500),
                padding=ft.Padding(left=8, top=3, right=8, bottom=3),
                border_radius=6,
                bgcolor=f"{ACCENT_AMBER}15",
                border=ft.Border.all(1, f"{ACCENT_AMBER}30"),
            )
        )

    # Action Buttons
    action_controls = []
    
    if url:
        action_controls.append(
            ft.FilledButton(
                text="Apply Now",
                icon=ft.Icons.OPEN_IN_NEW,
                url=url,
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_INDIGO,
                    color=TEXT_PRIMARY,
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding(left=14, top=8, right=14, bottom=8),
                ),
            )
        )
    
    if on_outreach:
        action_controls.append(
            ft.OutlinedButton(
                text="Outreach Draft",
                icon=ft.Icons.EMAIL_OUTLINED,
                on_click=lambda _: on_outreach(opportunity),
                style=ft.ButtonStyle(
                    color=ACCENT_CYAN,
                    side=ft.BorderSide(1, ACCENT_CYAN),
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding(left=12, top=8, right=12, bottom=8),
                ),
            )
        )

    if on_save and not is_saved:
        action_controls.append(
            ft.IconButton(
                icon=ft.Icons.BOOKMARK_BORDER,
                tooltip="Save Opportunity",
                icon_color=TEXT_SECONDARY,
                on_click=lambda _: on_save(opportunity),
            )
        )
    elif on_remove and is_saved:
        action_controls.append(
            ft.IconButton(
                icon=ft.Icons.BOOKMARK,
                tooltip="Remove from Saved",
                icon_color=ACCENT_AMBER,
                on_click=lambda _: on_remove(opportunity),
            )
        )

    return ft.Container(
        content=ft.Column(
            controls=[
                # Top row: Tier + Match % + Location
                ft.Row(
                    controls=[
                        tier_badge,
                        match_badge,
                        ft.Container(expand=True),
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCATION_ON_OUTLINED, size=14, color=TEXT_MUTED),
                                ft.Text(location, size=12, color=TEXT_SECONDARY),
                            ],
                            spacing=3,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=4),
                # Title and Company
                ft.Text(title, size=17, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.BUSINESS_OUTLINED, size=14, color=TEXT_MUTED),
                        ft.Text(company, size=13, weight=ft.FontWeight.W_500, color=TEXT_SECONDARY),
                    ],
                    spacing=5,
                ),
                ft.Container(height=6),
                # Summary if available
                ft.Text(
                    summary[:240] + ("..." if len(summary) > 240 else ""),
                    size=12,
                    color=TEXT_SECONDARY,
                    max_lines=3,
                ) if summary else ft.Container(),
                ft.Container(height=6),
                # Skill matching tags
                ft.Row(
                    controls=skill_chips,
                    wrap=True,
                    spacing=6,
                    run_spacing=6,
                ) if skill_chips else ft.Container(),
                ft.Container(height=8),
                # Actions row
                ft.Row(
                    controls=action_controls,
                    alignment=ft.MainAxisAlignment.END,
                    spacing=10,
                ),
            ],
            spacing=4,
        ),
        bgcolor=SURFACE_CARD,
        border=ft.Border.all(1, SURFACE_BORDER),
        border_radius=12,
        padding=18,
        margin=ft.Margin(left=0, top=0, right=0, bottom=12),
    )
