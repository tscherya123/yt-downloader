"""Theme utilities adapted for the CustomTkinter based interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import customtkinter as ctk


@dataclass(frozen=True)
class ThemeDefinition:
    """Immutable description of an application theme."""

    appearance_mode: str
    colors: Dict[str, str]


DEFAULT_THEME = "light"


_PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "background": "#f4f4f8",
        "surface": "#ffffff",
        "text": "#202124",
        "muted": "#5f6368",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "disabled": "#a0a4a8",
        "entry": "#ffffff",
        "canvas": "#ffffff",
        "log_bg": "#ffffff",
        "log_fg": "#202124",
        "highlight": "#e6e6eb",
    },
    "dark": {
        "background": "#121212",
        "surface": "#1f1f1f",
        "text": "#f5f5f5",
        "muted": "#b0b0b0",
        "accent": "#3b82f6",
        "accent_hover": "#2563eb",
        "disabled": "#6f6f6f",
        "entry": "#2a2a2a",
        "canvas": "#1f1f1f",
        "log_bg": "#161616",
        "log_fg": "#f5f5f5",
        "highlight": "#2c2c2c",
    },
}


THEMES: Dict[str, ThemeDefinition] = {
    name: ThemeDefinition(
        appearance_mode="Light" if name == "light" else "Dark",
        colors=palette,
    )
    for name, palette in _PALETTES.items()
}


def resolve_theme(code: str | None) -> ThemeDefinition:
    """Return a :class:`ThemeDefinition` for the requested theme code."""

    if code and code in THEMES:
        return THEMES[code]
    return THEMES[DEFAULT_THEME]


def apply_theme(code: str | None) -> ThemeDefinition:
    """Set CustomTkinter appearance mode and return palette for the theme."""

    theme = resolve_theme(code)
    ctk.set_appearance_mode(theme.appearance_mode)
    return theme


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "ThemeDefinition",
    "apply_theme",
    "resolve_theme",
]
