"""Font discovery.

The renderer needs real TrueType fonts: PIL's built-in bitmap font is a single
fixed size, and this project's whole point is measuring what happens at a given
pixel size. We look in the usual system locations rather than vendoring a font
file, and we fail loudly with the search path if nothing is found. A silent
fallback to the bitmap font would quietly invalidate every measurement.
"""

from __future__ import annotations

import os
from pathlib import Path

# Searched in order. The DejaVu family ships with almost every Linux distro and
# with Pillow's own test fixtures; the others are fallbacks.
_SEARCH_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    os.path.expanduser("~/.fonts"),
)

# family -> (regular filename, bold filename)
_FAMILIES = {
    "sans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    "serif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"),
    "mono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
}


class FontNotFound(RuntimeError):
    """Raised when no usable TrueType font is on this machine."""


def font_path(family: str = "sans", bold: bool = False) -> str:
    """Absolute path to a TrueType font file.

    Raises FontNotFound with the full search path rather than falling back to
    a bitmap font, because a silent fallback would change every glyph metric
    the measurement depends on.
    """
    if family not in _FAMILIES:
        raise ValueError(f"unknown family {family!r}; have {sorted(_FAMILIES)}")
    override = os.environ.get("VEI_FONT_DIR")
    dirs = (override,) + _SEARCH_DIRS if override else _SEARCH_DIRS
    name = _FAMILIES[family][1 if bold else 0]
    for d in dirs:
        candidate = Path(d) / name
        if candidate.is_file():
            return str(candidate)
    raise FontNotFound(
        f"no TrueType font {name!r} found. Searched: {', '.join(dirs)}. "
        "Install fonts-dejavu-core, or set VEI_FONT_DIR to a directory "
        "containing the DejaVu .ttf files."
    )


def available() -> bool:
    """True when at least the sans family is present."""
    try:
        font_path("sans")
    except FontNotFound:
        return False
    return True
