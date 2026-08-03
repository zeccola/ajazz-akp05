"""
Status icons for AKP05 button LCDs, rendered from the real Material
Design Icons set (the same "mdi:xxx" names used throughout Home
Assistant's own UI) -- so any icon name HA itself recognizes just works,
no manual per-icon drawing.

On first use, downloads and caches two files from the @mdi npm packages
via jsdelivr (~1.3MB font + ~2MB metadata, one-time, cached in
.mdi_cache/ next to this file -- git-ignored):
  - materialdesignicons-webfont.ttf: the icon font itself
  - meta.json: name -> codepoint lookup for every icon in the set

Needs internet access on first use per icon name; after that, both files
are read from the local cache and no network call is needed to render.
"""

import json
import os

import requests
from PIL import Image, ImageDraw, ImageFont

from akp05_device import BUTTON_IMAGE_SIZE

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mdi_cache")
FONT_PATH = os.path.join(CACHE_DIR, "materialdesignicons-webfont.ttf")
META_PATH = os.path.join(CACHE_DIR, "meta.json")

FONT_URL = "https://cdn.jsdelivr.net/npm/@mdi/font@latest/fonts/materialdesignicons-webfont.ttf"
META_URL = "https://cdn.jsdelivr.net/npm/@mdi/svg@latest/meta.json"

COLOR_ON = (40, 200, 60)
COLOR_OFF = (200, 40, 40)
COLOR_UNKNOWN = (120, 120, 120)

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}
_codepoint_by_name: dict[str, str] | None = None


def state_color(is_on: bool | None):
    if is_on is True:
        return COLOR_ON
    if is_on is False:
        return COLOR_OFF
    return COLOR_UNKNOWN


def _ensure_cached():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(FONT_PATH):
        print("Downloading MDI icon font (one-time, ~1.3MB)...")
        resp = requests.get(FONT_URL, timeout=30)
        resp.raise_for_status()
        with open(FONT_PATH, "wb") as f:
            f.write(resp.content)
    if not os.path.exists(META_PATH):
        print("Downloading MDI icon metadata (one-time, ~2MB)...")
        resp = requests.get(META_URL, timeout=30)
        resp.raise_for_status()
        with open(META_PATH, "wb") as f:
            f.write(resp.content)


def _codepoint_map() -> dict[str, str]:
    global _codepoint_by_name
    if _codepoint_by_name is None:
        _ensure_cached()
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
        by_name = {}
        for entry in meta:
            by_name[entry["name"]] = entry["codepoint"]
            for alias in entry.get("aliases", []):
                by_name.setdefault(alias, entry["codepoint"])
        _codepoint_by_name = by_name
    return _codepoint_by_name


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        _ensure_cached()
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


def resolve_icon_name(name: str) -> str:
    """Accepts 'floor-lamp-outline' or the HA-style 'mdi:floor-lamp-outline'."""
    return name[4:] if name.startswith("mdi:") else name


def icon_exists(name: str) -> bool:
    return resolve_icon_name(name) in _codepoint_map()


def build_icon(name: str, is_on: bool | None) -> Image.Image:
    """Render any MDI icon name (e.g. 'floor-lamp-outline', 'fan',
    'light-switch') colored by state. Raises KeyError with a clear
    message if the name isn't a real MDI icon."""
    codepoints = _codepoint_map()
    key = resolve_icon_name(name)
    codepoint = codepoints.get(key)
    if codepoint is None:
        raise KeyError(f"'{name}' isn't a known MDI icon name -- check https://pictogrammers.com/library/mdi/")

    size = BUTTON_IMAGE_SIZE
    img = Image.new("RGB", size, (8, 8, 8))
    draw = ImageDraw.Draw(img)
    color = state_color(is_on)

    glyph_size = int(min(size) * 0.72)
    font = _font(glyph_size)
    ch = chr(int(codepoint, 16))
    bbox = draw.textbbox((0, 0), ch, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = size[0] / 2 - w / 2 - bbox[0]
    y = size[1] / 2 - h / 2 - bbox[1]
    draw.text((x, y), ch, font=font, fill=color)

    return img
