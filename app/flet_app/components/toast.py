"""
Toast / SnackBar notification helpers for CareerPulse Flet GUI.
"""
import flet as ft
from app.flet_app.config import ACCENT_GREEN, ACCENT_RED, ACCENT_INDIGO, BG_DARK, TEXT_PRIMARY

def show_toast(page: ft.Page, message: str, is_error: bool = False, is_info: bool = False):
    """Show non-blocking feedback toast message to user."""
    bg = ACCENT_RED if is_error else (ACCENT_INDIGO if is_info else ACCENT_GREEN)
    icon = ft.Icons.ERROR_OUTLINE if is_error else (ft.Icons.INFO_OUTLINE if is_info else ft.Icons.CHECK_CIRCLE_OUTLINE)
    
    snack = ft.SnackBar(
        content=ft.Row(
            controls=[
                ft.Icon(icon, color=TEXT_PRIMARY, size=18),
                ft.Text(message, color=TEXT_PRIMARY, size=13, weight=ft.FontWeight.W_500),
            ],
            spacing=10,
        ),
        bgcolor=bg,
        duration=3500,
        open=True,
    )
    page.overlay.append(snack)
    page.update()
