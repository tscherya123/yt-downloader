"""Visual theme definitions for the Tkinter interface."""

from __future__ import annotations

from typing import Dict

DEFAULT_THEME = "light"

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "background": "#f4f4f8",
        "frame": "#ffffff",
        "text": "#202124",
        "muted": "#5f6368",
        "button_bg": "#e6e6eb",
        "button_active_bg": "#d0d0d6",
        "button_fg": "#202124",
        "disabled_fg": "#a0a4a8",
        "entry_bg": "#ffffff",
        "canvas_bg": "#ffffff",
        "log_bg": "#ffffff",
        "log_fg": "#202124",
        "dropdown_bg": "#ffffff",
        "dropdown_fg": "#202124",
        "dropdown_select_bg": "#202124",
        "dropdown_select_fg": "#f5f5f5",
    },
    "dark": {
        "background": "#121212",
        "frame": "#1f1f1f",
        "text": "#f5f5f5",
        "muted": "#b0b0b0",
        "button_bg": "#2c2c2c",
        "button_active_bg": "#3a3a3a",
        "button_fg": "#f5f5f5",
        "disabled_fg": "#6f6f6f",
        "entry_bg": "#2a2a2a",
        "canvas_bg": "#1f1f1f",
        "log_bg": "#161616",
        "log_fg": "#f5f5f5",
        "dropdown_bg": "#000000",
        "dropdown_fg": "#f5f5f5",
        "dropdown_select_bg": "#f4f4f8",
        "dropdown_select_fg": "#202124",
    },
}
