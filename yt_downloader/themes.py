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


DEFAULT_THEME = "modern"


_PALETTES: Dict[str, Dict[str, str]] = {
    "modern": {
        "background": "#09090b",
        "surface": "#18181b",
        "panel": "#202023",
        "input": "#27272a",
        "text": "#e4e4e7",
        "muted": "#a1a1aa",
        "accent": "#6366f1",
        "accent_hover": "#4f46e5",
        "disabled": "#3f3f46",
        "button_disabled": "#27272a",
        "button_disabled_text": "#a1a1aa",
        "button_text": "#ffffff",
        "entry": "#27272a",
        "canvas": "#09090b",
        "log_bg": "#18181b",
        "log_fg": "#e4e4e7",
        "highlight": "#3f3f46",
        "success": "#22c55e",
        "error": "#ef4444",
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
