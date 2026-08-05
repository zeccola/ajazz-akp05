"""
Status icons + text rendering for AKP05 button LCDs.

build_icon() renders any Material Design Icons name (the same "mdi:xxx"
names used throughout Home Assistant's own UI) -- so any icon name HA
itself recognizes just works, no manual per-icon drawing. build_text()
renders arbitrary text (a sensor value, anything) instead, in Roboto --
the same font Home Assistant's own frontend uses.

On first use, downloads and caches these next to this file in
.mdi_cache/ (git-ignored):
  - materialdesignicons-webfont.ttf + meta.json: the MDI icon font and
    its name -> codepoint lookup, from the @mdi npm packages via jsdelivr
    (~1.3MB + ~2MB).
  - Roboto.ttf: from Google's own font repo (~1.7MB, a single variable
    font covering every weight/width, loaded plain/default here).

Needs internet access on first use per font; after that, everything is
read from the local cache and no network call is needed to render.
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

# Same font Home Assistant's own frontend uses -- confirmed the actual
# path directly (google/fonts stores it under ofl/, not apache/ as an
# older Roboto release did; it's OFL-licensed now, not Apache 2.0,
# though both are free to embed same as MDI's font above). Single
# variable-font file (wdth/wght axes) rather than split static weights;
# loaded plain (default instance, normal weight) for simplicity.
ROBOTO_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/roboto/Roboto%5Bwdth,wght%5D.ttf"
ROBOTO_PATH = os.path.join(CACHE_DIR, "Roboto.ttf")

COLOR_ON = (40, 200, 60)
COLOR_OFF = (200, 40, 40)
COLOR_UNKNOWN = (120, 120, 120)
TEXT_COLOR = (230, 230, 230)

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}
_roboto_cache: dict[int, ImageFont.FreeTypeFont] = {}
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


def _ensure_roboto_cached():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(ROBOTO_PATH):
        print("Downloading Roboto font (one-time, ~1.7MB)...")
        resp = requests.get(ROBOTO_URL, timeout=30)
        resp.raise_for_status()
        with open(ROBOTO_PATH, "wb") as f:
            f.write(resp.content)


def _roboto_font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _roboto_cache:
        _ensure_roboto_cached()
        _roboto_cache[size] = ImageFont.truetype(ROBOTO_PATH, size)
    return _roboto_cache[size]


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


def build_text(text: str, color=TEXT_COLOR) -> Image.Image:
    """Renders arbitrary text (a sensor value, "21.4°C", a media title,
    whatever) centered on a button in Roboto, auto-shrinking to fit --
    unlike build_icon this isn't tied to on/off coloring, it's just a
    plain readable value display. Same dark background as build_icon so
    text-monitor and icon buttons look consistent next to each other."""
    size = BUTTON_IMAGE_SIZE
    img = Image.new("RGB", size, (8, 8, 8))
    draw = ImageDraw.Draw(img)

    margin = 12
    max_width, max_height = size[0] - 2 * margin, size[1] - 2 * margin

    font_size = 56
    font = _roboto_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    while (w > max_width or h > max_height) and font_size > 12:
        font_size -= 4
        font = _roboto_font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    x = size[0] / 2 - w / 2 - bbox[0]
    y = size[1] / 2 - h / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=color)

    return img
