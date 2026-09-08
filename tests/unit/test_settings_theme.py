"""Theme settings validation: whitelist, hex, ui_scale clamp."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.settings_service import THEME_COLOR_KEYS, AppSettings


def test_theme_defaults() -> None:
    settings = AppSettings()
    assert settings.appearance == "cyberpunk-dark"
    assert settings.font_body == "inter"
    assert settings.font_display == "chakra"
    assert settings.font_mono == "jetbrains"
    assert settings.ui_scale == 1.0
    assert settings.theme_colors == {}


def test_accepts_new_templates_and_colours() -> None:
    settings = AppSettings.model_validate(
        {
            "appearance": "ember",
            "font_body": "system",
            "ui_scale": 1.1,
            "theme_colors": {
                "graph_edge_selected": "#FF2D55",
                "graph_edge_default": "#3a3f4a",
            },
        }
    )
    assert settings.appearance == "ember"
    assert settings.theme_colors["graph_edge_selected"] == "#ff2d55"
    assert "graph_edge_selected" in THEME_COLOR_KEYS


def test_drops_unknown_colour_keys() -> None:
    settings = AppSettings.model_validate(
        {
            "theme_colors": {
                "accent_primary": "#22e0f5",
                "not_a_real_token": "#ffffff",
            }
        }
    )
    assert settings.theme_colors == {"accent_primary": "#22e0f5"}


def test_rejects_invalid_hex() -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate(
            {"theme_colors": {"accent_primary": "red"}}
        )


def test_clamps_ui_scale() -> None:
    assert AppSettings.model_validate({"ui_scale": 0.1}).ui_scale == 0.85
    assert AppSettings.model_validate({"ui_scale": 9}).ui_scale == 1.35
