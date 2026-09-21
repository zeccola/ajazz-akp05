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
  - Roboto.ttf: static Regular (~170KB), from the same jsdelivr CDN as
    the MDI font above -- see ROBOTO_URL for why both live on one host.

Needs internet access on first use per font; after that, everything is
read from the local cache and no network call is needed to render. The
Home Assistant add-on doesn't rely on that first-use download at all --
its Dockerfile calls prefetch_fonts() at build time so the fonts ship
inside the image (see that function for why).
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

# Same font Home Assistant's own frontend uses (OFL-licensed, free to
# embed same as MDI's font above). Served from jsdelivr -- the same host
# as the MDI font -- and deliberately NOT from raw.githubusercontent.com
# where this used to live: that split the two fonts across two hosts, so
# a network that could reach one but not the other produced working
# icons and dead text, which is exactly what got reported. One host,
# one reachability question. Static Regular rather than google/fonts'
# variable Roboto[wdth,wght].ttf for the same reason -- one less thing
# that can behave differently on a given Pillow/FreeType build.
ROBOTO_URL = "https://cdn.jsdelivr.net/npm/@expo-google-fonts/roboto@latest/Roboto_400Regular.ttf"
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


def _download(url: str, dest: str, label: str):
    """Fetch to a temp file, then rename into place. The caching checks
    below only test whether the file exists, so a half-written one (the
    process killed mid-write) would look cached forever and fail every
    later render -- surviving restarts, since nothing re-downloads a
    file that's already there."""
    print(f"Downloading {label} (one-time)...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    tmp = f"{dest}.part"
    with open(tmp, "wb") as f:
        f.write(resp.content)
    os.replace(tmp, dest)


def _ensure_cached():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(FONT_PATH):
        _download(FONT_URL, FONT_PATH, "MDI icon font, ~1.3MB")
    if not os.path.exists(META_PATH):
        _download(META_URL, META_PATH, "MDI icon metadata, ~2MB")


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


def _load_font(path: str, size: int, ensure) -> ImageFont.FreeTypeFont:
    """Load a cached font, repairing it once if it won't open. The
    caching checks only test whether the file exists, so a file that's
    there but unparseable -- a partial download from before these were
    written atomically, most likely -- would fail every render from then
    on and nothing would ever re-fetch it. Neither a restart nor a
    reinstall clears that; deleting and re-downloading once does."""
    ensure()
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:
        print(f"Font at {path} wouldn't load ({exc}) -- discarding it and re-downloading once")
        try:
            os.remove(path)
        except OSError:
            pass
        ensure()
        return ImageFont.truetype(path, size)


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        _font_cache[size] = _load_font(FONT_PATH, size, _ensure_cached)
    return _font_cache[size]


def _ensure_roboto_cached():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(ROBOTO_PATH):
        _download(ROBOTO_URL, ROBOTO_PATH, "Roboto font, ~170KB")


def prefetch_fonts():
    """Download both fonts now rather than on the first render. The
    add-on's Dockerfile calls this at build time so the container never
    needs network access to draw anything: CACHE_DIR sits in the image
    layer, so a rebuild used to wipe it and leave every icon/text render
    failing until jsdelivr/GitHub were reachable again -- with button
    presses still working, since they never come through here, and
    neither a restart nor a replug fixing it. Failing here breaks the
    image build loudly instead."""
    _ensure_cached()
    _ensure_roboto_cached()


def _roboto_font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _roboto_cache:
        _roboto_cache[size] = _load_font(ROBOTO_PATH, size, _ensure_roboto_cached)
    return _roboto_cache[size]


def resolve_icon_name(name: str) -> str:
    """Accepts 'floor-lamp-outline' or the HA-style 'mdi:floor-lamp-outline'."""
    return name[4:] if name.startswith("mdi:") else name


def icon_exists(name: str) -> bool:
    return resolve_icon_name(name) in _codepoint_map()


def build_icon(name: str, is_on: bool | None, size=BUTTON_IMAGE_SIZE) -> Image.Image:
    """Render any MDI icon name (e.g. 'floor-lamp-outline', 'fan',
    'light-switch') colored by state. Raises KeyError with a clear
    message if the name isn't a real MDI icon. Defaults to a button's
    size; pass a strip chunk's size (e.g. (200, 112)) to center the same
    glyph in a wider, non-square tile -- glyph_size below is derived from
    min(size), so a wide tile just gets extra side margin instead of a
    stretched icon."""
    codepoints = _codepoint_map()
    key = resolve_icon_name(name)
    codepoint = codepoints.get(key)
    if codepoint is None:
        raise KeyError(f"'{name}' isn't a known MDI icon name -- check https://pictogrammers.com/library/mdi/")

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


def build_text(text: str, size=BUTTON_IMAGE_SIZE, color=TEXT_COLOR) -> Image.Image:
    """Renders arbitrary text (a sensor value, "21.4°C", a media title,
    whatever) centered in Roboto, auto-shrinking to fit `size` -- unlike
    build_icon this isn't tied to on/off coloring, it's just a plain
    readable value display. Same dark background as build_icon so
    text-monitor buttons and icon buttons look consistent next to each
    other. Defaults to a button's size; pass STRIP_IMAGE_SIZE for the
    touch strip (akp05_write_strip_text.py) -- both share the same
    112px height, so starting the font size from a fraction of the
    height (rather than a fixed guess) scales sensibly for either
    without needing separate logic: a short string on the much wider
    strip actually uses the extra room instead of rendering at a small
    fixed size just because that's what fit a 112px-wide button."""
    img = Image.new("RGB", size, (8, 8, 8))
    draw = ImageDraw.Draw(img)

    margin = 12
    max_width, max_height = size[0] - 2 * margin, size[1] - 2 * margin

    font_size = max(12, int(size[1] * 0.7))
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
