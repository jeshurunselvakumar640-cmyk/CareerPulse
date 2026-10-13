"""
Applications & Mailbox Reply Tracker View for CareerPulse Flet GUI.
"""
from typing import Callable, Dict, Any, List
import flet as ft

from app.flet_app.config import (
    SURFACE_CARD, SURFACE_BORDER, SURFACE_BORDER_LIGHT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT_INDIGO, ACCENT_CYAN, ACCENT_GREEN, ACCENT_AMBER, ACCENT_RED
)
from app.flet_app.state import state
from app.flet_app.api_client import api_client
from app.flet_app.components.toast import show_toast

def build_tracker_view(
    page: ft.Page,
    on_sync_replies: Callable[[], None],
) -> ft.Container:
    """Builds the Applications & Mailbox Tracking view."""

    mailbox_connected = state.mailbox_status.get("connected", False)
    syncing_indicator = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_CYAN, visible=False)

    async def handle_sync_click(e):
        syncing_indicator.visible = True
        page.update()
        try:
            res = await api_client.sync_mailbox_replies(force=True)
            if res.get("status") == "success" or res.get("synced") is not None:
                show_toast(page, "Mailbox synchronized! New company replies updated.")
                on_sync_replies()
            else:
                show_toast(page, res.get("detail", "Sync completed with 0 updates."), is_info=True)
        except Exception as err:
            show_toast(page, f"Mailbox sync error: {err}", is_error=True)
        finally:
            syncing_indicator.visible = False
            page.update()

    # Mailbox status card
    status_card = ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Icon(
                        ft.Icons.MARK_EMAIL_READ if mailbox_connected else ft.Icons.MAIL_OUTLINE,
                        size=24,
                        color=ACCENT_GREEN if mailbox_connected else ACCENT_AMBER,
                    ),
                    padding=12,
                    border_radius=8,
                    bgcolor=f"{ACCENT_GREEN if mailbox_connected else ACCENT_AMBER}18",
                ),
                ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text(
                                    "Gmail Mailbox Integration: " + ("Connected" if mailbox_connected else "Ready to Connect"),
                                    size=15,
                                    weight=ft.FontWeight.BOLD,
                                    color=TEXT_PRIMARY,
                                ),
                                ft.Container(
                                    content=ft.Text("READ-ONLY", size=10, weight=ft.FontWeight.BOLD, color=ACCENT_CYAN),
                                    padding=ft.Padding(left=6, top=2, right=6, bottom=2),
                                    border_radius=4,
                                    bgcolor=f"{ACCENT_CYAN}15",
                                ),
                            ],
                            spacing=8,
                        ),
                        ft.Text(
                            "Automatically scans recruiter responses, extracts interview dates, and updates application statuses."
                            if mailbox_connected else
                            "Connect your Google account in Settings or Vercel environment to enable automatic reply tracking.",
                            size=12,
                            color=TEXT_SECONDARY,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Row(
                    controls=[
                        syncing_indicator,
                        ft.FilledButton(
                            text="Sync Replies",
                            icon=ft.Icons.SYNC,
                            on_click=handle_sync_click,
                            style=ft.ButtonStyle(
                                bgcolor=ACCENT_INDIGO,
                                color=TEXT_PRIMARY,
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                        ),
                    ],
                    spacing=10,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=SURFACE_CARD,
        border=ft.Border.all(1, SURFACE_BORDER),
        border_radius=12,
        padding=18,
    )

    # Applications list
    apps = state.applications or []
    app_cards = []

    if not apps:
        app_cards.append(
            ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.CHECKLIST_RTL, size=40, color=TEXT_MUTED),
                        ft.Container(height=8),
                        ft.Text("No active applications recorded yet.", size=15, color=TEXT_PRIMARY),
                        ft.Text("Use 'Outreach Draft' on opportunity cards to record applications and send emails.", size=13, color=TEXT_MUTED),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
                padding=40,
                alignment=ft.alignment.center,
            )
        )
    else:
        for app in apps:
            company = app.get("company", "Company")
            title = app.get("title", "Position")
            status = app.get("status", "APPLIED").upper()
            updated_at = app.get("last_updated", "Recent")
            
            status_color = (
                ACCENT_GREEN if "INTERVIEW" in status or "OFFER" in status else
                (ACCENT_RED if "REJECT" in status else ACCENT_CYAN)
            )

            app_cards.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                                    ft.Text(company, size=13, color=TEXT_SECONDARY),
                                    ft.Text(f"Updated: {updated_at}", size=11, color=TEXT_MUTED),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                            ft.Container(
                                content=ft.Text(status, size=11, weight=ft.FontWeight.BOLD, color=status_color),
                                padding=ft.Padding(left=10, top=4, right=10, bottom=4),
                                border_radius=12,
                                bgcolor=f"{status_color}18",
                                border=ft.Border.all(1, f"{status_color}40"),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    bgcolor=SURFACE_CARD,
                    border=ft.Border.all(1, SURFACE_BORDER),
                    border_radius=10,
                    padding=16,
                    margin=ft.Margin(left=0, top=0, right=0, bottom=8),
                )
            )

    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Applications & Reply Tracker", size=22, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Text("Track application lifecycles, interview invitations, and status changes in real-time.", size=13, color=TEXT_SECONDARY),
                ft.Container(height=10),
                status_card,
                ft.Container(height=14),
                ft.Text("Tracked Applications", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Column(controls=app_cards, spacing=6),
            ],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=20,
    )
