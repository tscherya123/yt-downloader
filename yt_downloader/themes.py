"""Theme utilities adapted for the CustomTkinter based interface."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Dict, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only needed for typing tools
    import customtkinter as ctk


@dataclass(frozen=True)
class ThemeDefinition:
    """Immutable description of an application theme."""

    appearance_mode: str
    colors: Dict[str, str]


DEFAULT_THEME = "light"


_PALETTES: Dict[str, Dict[str, str]] = {
    "light": {
        "background": "#f5f6fb",
        "surface": "#ffffff",
        "text": "#0f172a",
        "muted": "#6b7287",
        "accent": "#7c5cff",
        "accent_hover": "#8b6dff",
        "disabled": "#c8cce0",
        "button_disabled": "#e8eaf5",
        "button_disabled_text": "#9aa1bd",
        "button_text": "#ffffff",
        "entry": "#f2f4ff",
        "canvas": "#eef1fb",
        "log_bg": "#ffffff",
        "log_fg": "#0f172a",
        "highlight": "#e8e9f7",
    },
    "dark": {
        "background": "#05060b",
        "surface": "#0f111c",
        "text": "#e9eafb",
        "muted": "#9ea4c7",
        "accent": "#7c5cff",
        "accent_hover": "#8d6dff",
        "disabled": "#484d6a",
        "button_disabled": "#161827",
        "button_disabled_text": "#7d83a6",
        "button_text": "#ffffff",
        "entry": "#0b0e19",
        "canvas": "#0b0e19",
        "log_bg": "#0a0c15",
        "log_fg": "#e9eafb",
        "highlight": "#171a2a",
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
    ctk = import_module("customtkinter")
    ctk.set_appearance_mode(theme.appearance_mode)
    return theme


__all__ = [
    "DEFAULT_THEME",
    "THEMES",
    "ThemeDefinition",
    "apply_theme",
    "resolve_theme",
]
