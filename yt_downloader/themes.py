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
        "background": "#f3f4fb",
        "surface": "#ffffff",
        "text": "#141625",
        "muted": "#5b6178",
        "accent": "#6b4cfe",
        "accent_hover": "#7b63ff",
        "disabled": "#b7bfd7",
        "button_disabled": "#e4e6f4",
        "button_disabled_text": "#8389a3",
        "entry": "#f7f8ff",
        "canvas": "#f7f8ff",
        "log_bg": "#ffffff",
        "log_fg": "#141625",
        "highlight": "#e2e6f5",
    },
    "dark": {
        "background": "#0b0d16",
        "surface": "#11162a",
        "text": "#e8e9f5",
        "muted": "#9ba3c7",
        "accent": "#7b5cff",
        "accent_hover": "#8d73ff",
        "disabled": "#4f5672",
        "button_disabled": "#1a1f33",
        "button_disabled_text": "#6f7692",
        "entry": "#0f1323",
        "canvas": "#0d111f",
        "log_bg": "#0b1021",
        "log_fg": "#e8e9f5",
        "highlight": "#171d33",
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
