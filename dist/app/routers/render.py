from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import json
from ..config import render_file, settings_file
from ..utils import safe_save_json

router = APIRouter()

DEFAULT_RENDER = {
    "font_family": "Georgia",
    "font_size": 18,
    "font_weight": 400,
    "font_thickness": 0,
    "line_height": 1.8,
    "paragraph_spacing": 1.1,
    "text_align": "justify",
    "text_indent": True,
    "hyphenation": True,
    "indent_mode": "follow",
    "h1_align": "center",
    "h2_align": "left",
    "h3_align": "left",
    "two_page_landscape": False,
    "horizontal_mode": False,
    "margin_left": 8,
    "margin_right": 8,
    "margins_linked": True,
    "margin_left_open": 5,
    "margin_right_open": 5,
    "margins_linked_open": True,
    "center_gutter": 3.5,
    "landscape_outer_margin": 4,
    "measure_lock": True,
    "sidebar_auto_collapse": "auto",
}

VALID_TEXT_ALIGN = {"left", "center", "right", "justify"}
VALID_SIDEBAR_AUTO_COLLAPSE = {"auto", "show"}
VALID_HEADING_ALIGN = {"left", "center", "right"}
VALID_INDENT_MODE = {"follow", "all"}
MARGIN_MIN = 0
MARGIN_MAX = 35
OUTER_MARGIN_MIN = 0
OUTER_MARGIN_MAX = 15
GUTTER_MIN = 3.0
GUTTER_MAX = 12.0


def _clamp_margin(value, default=8):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(MARGIN_MIN, min(MARGIN_MAX, n))


def _clamp_center_gutter(value, default=3.5):
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    n = max(GUTTER_MIN, min(GUTTER_MAX, n))
    return round(n * 4) / 4


def _clamp_outer_margin(value, default=4):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(OUTER_MARGIN_MIN, min(OUTER_MARGIN_MAX, n))


class RenderSettings(BaseModel):
    font_family: Optional[str] = None
    font_size: Optional[int] = None
    font_weight: Optional[int] = None
    font_thickness: Optional[int] = None
    line_height: Optional[float] = None
    paragraph_spacing: Optional[float] = None
    text_align: Optional[str] = None
    text_indent: Optional[bool] = None
    hyphenation: Optional[bool] = None
    indent_mode: Optional[str] = None
    h1_align: Optional[str] = None
    h2_align: Optional[str] = None
    h3_align: Optional[str] = None
    two_page_landscape: Optional[bool] = None
    horizontal_mode: Optional[bool] = None
    margin_left: Optional[int] = None
    margin_right: Optional[int] = None
    margins_linked: Optional[bool] = None
    margin_left_open: Optional[int] = None
    margin_right_open: Optional[int] = None
    margins_linked_open: Optional[bool] = None
    center_gutter: Optional[float] = None
    landscape_outer_margin: Optional[int] = None
    measure_lock: Optional[bool] = None
    sidebar_auto_collapse: Optional[str] = None


def _settings_font_size():
    if not settings_file.exists():
        return None
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict) and stored.get("font_size"):
            return int(stored["font_size"])
    except Exception:
        return None
    return None


def _sanitize(data: dict) -> dict:
    out = dict(DEFAULT_RENDER)
    out.update(data or {})
    if out.get("text_align") not in VALID_TEXT_ALIGN:
        out["text_align"] = DEFAULT_RENDER["text_align"]
    if out.get("indent_mode") not in VALID_INDENT_MODE:
        out["indent_mode"] = DEFAULT_RENDER["indent_mode"]
    for key in ("h1_align", "h2_align", "h3_align"):
        if out.get(key) not in VALID_HEADING_ALIGN:
            out[key] = DEFAULT_RENDER[key]
    out["text_indent"] = bool(out.get("text_indent", True))
    out["hyphenation"] = out.get("hyphenation", True) is not False
    out["two_page_landscape"] = bool(out.get("two_page_landscape", False))
    out["horizontal_mode"] = bool(out.get("horizontal_mode", False))
    out["margins_linked"] = out.get("margins_linked", True) is not False
    out["margin_left"] = _clamp_margin(out.get("margin_left"), DEFAULT_RENDER["margin_left"])
    out["margin_right"] = _clamp_margin(out.get("margin_right"), DEFAULT_RENDER["margin_right"])
    if out["margins_linked"]:
        out["margin_right"] = out["margin_left"]
    out["margins_linked_open"] = out.get("margins_linked_open", True) is not False
    out["margin_left_open"] = _clamp_margin(out.get("margin_left_open"), DEFAULT_RENDER["margin_left_open"])
    out["margin_right_open"] = _clamp_margin(out.get("margin_right_open"), DEFAULT_RENDER["margin_right_open"])
    if out["margins_linked_open"]:
        out["margin_right_open"] = out["margin_left_open"]
    out["center_gutter"] = _clamp_center_gutter(out.get("center_gutter"), DEFAULT_RENDER["center_gutter"])
    out["landscape_outer_margin"] = _clamp_outer_margin(
        out.get("landscape_outer_margin"), DEFAULT_RENDER["landscape_outer_margin"]
    )
    out["measure_lock"] = out.get("measure_lock", True) is not False
    if out.get("sidebar_auto_collapse") not in VALID_SIDEBAR_AUTO_COLLAPSE:
        out["sidebar_auto_collapse"] = DEFAULT_RENDER["sidebar_auto_collapse"]
    try:
        out["font_size"] = int(out.get("font_size") or DEFAULT_RENDER["font_size"])
    except (TypeError, ValueError):
        out["font_size"] = DEFAULT_RENDER["font_size"]
    try:
        out["font_weight"] = int(out.get("font_weight") or DEFAULT_RENDER["font_weight"])
    except (TypeError, ValueError):
        out["font_weight"] = DEFAULT_RENDER["font_weight"]
    try:
        out["font_thickness"] = max(0, min(12, int(out.get("font_thickness", 0))))
    except (TypeError, ValueError):
        out["font_thickness"] = DEFAULT_RENDER["font_thickness"]
    try:
        out["line_height"] = float(out.get("line_height") or DEFAULT_RENDER["line_height"])
    except (TypeError, ValueError):
        out["line_height"] = DEFAULT_RENDER["line_height"]
    try:
        out["paragraph_spacing"] = float(out.get("paragraph_spacing"))
    except (TypeError, ValueError):
        out["paragraph_spacing"] = DEFAULT_RENDER["paragraph_spacing"]
    if not out.get("font_family"):
        out["font_family"] = DEFAULT_RENDER["font_family"]
    return out


def _load_render():
    data = dict(DEFAULT_RENDER)
    migrated = _settings_font_size()
    if migrated:
        data["font_size"] = migrated
    if render_file.exists():
        try:
            with open(render_file, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                data.update(stored)
        except Exception:
            pass
    return _sanitize(data)


@router.get("/api/render")
async def get_render():
    return _load_render()


@router.post("/api/render")
async def save_render(settings: RenderSettings):
    current = _load_render()
    incoming = settings.model_dump(exclude_none=True)
    current.update(incoming)
    current = _sanitize(current)
    try:
        safe_save_json(render_file, current)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}
